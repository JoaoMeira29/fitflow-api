import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, LLMClientDep
from app.api.profile import targets_for_profile
from app.api.progress import recent_progress
from app.core.diet_plans import Nutrients, food_nutrients, generate_diet_plan
from app.core.llm import LLMError
from app.core.plan_generation import PlanGenerationError
from app.core.targets import CalorieTargetError
from app.models.diet_plan import DietPlan, DietPlanMeal, DietPlanMealItem
from app.models.food import Food
from app.models.profile import Profile
from app.schemas.diet import (
    DietPlanCreate,
    DietPlanItemRead,
    DietPlanMealRead,
    DietPlanRead,
    DietPlanSummary,
    NutrientsRead,
)
from app.schemas.food import FoodRead
from app.schemas.profile import ProfileRead

router = APIRouter(prefix="/plans/diet", tags=["Diet plans"])


def _nutrients_read(nutrients: Nutrients) -> NutrientsRead:
    return NutrientsRead(
        kcal=round(nutrients.kcal),
        protein_g=round(nutrients.protein_g),
        carbs_g=round(nutrients.carbs_g),
        fat_g=round(nutrients.fat_g),
    )


def _targets_read(plan: DietPlan) -> NutrientsRead:
    return NutrientsRead(
        kcal=plan.target_kcal,
        protein_g=plan.target_protein_g,
        carbs_g=plan.target_carbs_g,
        fat_g=plan.target_fat_g,
    )


def _summary(plan: DietPlan) -> DietPlanSummary:
    return DietPlanSummary(
        id=plan.id,
        is_active=plan.is_active,
        model=plan.model,
        created_at=plan.created_at,
        targets=_targets_read(plan),
    )


def _full_plan(plan: DietPlan) -> DietPlanRead:
    """Build the response for a stored plan. Only foods and grams are stored;
    the calories and macros are computed here from the catalog values."""
    meals = []
    plan_total = Nutrients()
    for meal in plan.meals:
        items = []
        meal_total = Nutrients()
        for item in meal.items:
            nutrients = food_nutrients(item.food, item.grams)
            meal_total += nutrients
            items.append(
                DietPlanItemRead(
                    position=item.position,
                    grams=item.grams,
                    food=FoodRead.model_validate(item.food),
                    nutrients=_nutrients_read(nutrients),
                )
            )
        plan_total += meal_total
        meals.append(
            DietPlanMealRead(
                position=meal.position,
                meal_type=meal.meal_type,
                items=items,
                totals=_nutrients_read(meal_total),
            )
        )
    return DietPlanRead(
        **_summary(plan).model_dump(),
        totals=_nutrients_read(plan_total),
        meals=meals,
    )


async def _load_plan(
    db: AsyncSession, plan_id: uuid.UUID, user_id: uuid.UUID
) -> DietPlan | None:
    """Load one of the user's plans with its meals, items and foods. Async
    SQLAlchemy cannot load relationships lazily, so they are loaded here."""
    statement = (
        select(DietPlan)
        .where(DietPlan.id == plan_id, DietPlan.user_id == user_id)
        .options(
            selectinload(DietPlan.meals)
            .selectinload(DietPlanMeal.items)
            .selectinload(DietPlanMealItem.food)
        )
        .execution_options(populate_existing=True)
    )
    return await db.scalar(statement)


@router.post("", response_model=DietPlanRead, status_code=status.HTTP_201_CREATED)
async def create_diet_plan(
    current_user: CurrentUser,
    db: DbSession,
    llm: LLMClientDep,
    payload: DietPlanCreate | None = None,
):
    profile_row = await db.get(Profile, current_user.id)
    if profile_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete your profile before generating a plan",
        )
    profile = ProfileRead.model_validate(profile_row)
    # The targets are computed in code; the model is only told what they are.
    # A calorie target chosen by the user still has to pass the safety limits.
    try:
        targets = targets_for_profile(
            profile, requested_calories=payload.target_kcal if payload else None
        )
    except CalorieTargetError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from None
    foods = (await db.scalars(select(Food))).all()
    # What the user logged recently, so a regenerated plan can adapt to it.
    progress = await recent_progress(
        db, current_user.id, profile.training_days_per_week
    )
    # The model can take a long time to answer. Ending the transaction here
    # returns the database connection to the pool while we wait.
    await db.commit()

    try:
        generated = await generate_diet_plan(
            llm, profile=profile, targets=targets, foods=foods, progress=progress
        )
    except PlanGenerationError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "The model did not produce a valid plan. Try again.",
                "errors": error.errors,
            },
        ) from None
    except LLMError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The plan generator is unavailable. Try again later.",
        ) from None

    # The new plan replaces the active one; older plans stay as history.
    await db.execute(
        update(DietPlan)
        .where(DietPlan.user_id == current_user.id, DietPlan.is_active)
        .values(is_active=False)
    )
    plan = DietPlan(
        user_id=current_user.id,
        target_kcal=targets.calories,
        target_protein_g=targets.protein_g,
        target_carbs_g=targets.carbs_g,
        target_fat_g=targets.fat_g,
        profile_snapshot=profile.model_dump(mode="json"),
        model=generated.model,
        meals=[
            DietPlanMeal(
                meal_type=meal.meal_type,
                position=meal_position,
                items=[
                    DietPlanMealItem(
                        food_id=item.food_id,
                        position=item_position,
                        grams=item.grams,
                    )
                    for item_position, item in enumerate(meal.items, start=1)
                ],
            )
            for meal_position, meal in enumerate(generated.draft.meals, start=1)
        ],
    )
    db.add(plan)
    await db.commit()
    return _full_plan(await _load_plan(db, plan.id, current_user.id))


@router.get("", response_model=list[DietPlanSummary])
async def list_diet_plans(current_user: CurrentUser, db: DbSession):
    statement = (
        select(DietPlan)
        .where(DietPlan.user_id == current_user.id)
        .order_by(DietPlan.created_at.desc())
    )
    return [_summary(plan) for plan in await db.scalars(statement)]


@router.get("/{plan_id}", response_model=DietPlanRead)
async def read_diet_plan(plan_id: uuid.UUID, current_user: CurrentUser, db: DbSession):
    # A plan that belongs to someone else gives the same 404 as one that does
    # not exist, so the endpoint does not reveal which plan ids are real.
    plan = await _load_plan(db, plan_id, current_user.id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diet plan not found",
        )
    return _full_plan(plan)
