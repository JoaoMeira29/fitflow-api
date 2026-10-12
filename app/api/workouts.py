import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, LLMClientDep
from app.core.age import calculate_age
from app.core.llm import LLMError
from app.core.plan_generation import PlanGenerationError
from app.core.workout_plans import generate_workout_plan
from app.models.exercise import Exercise
from app.models.profile import Profile
from app.models.workout_plan import WorkoutPlan, WorkoutPlanDay, WorkoutPlanExercise
from app.schemas.profile import ProfileRead
from app.schemas.workout import WorkoutPlanRead, WorkoutPlanSummary

router = APIRouter(prefix="/plans/workout", tags=["Workout plans"])


async def _load_plan(
    db: AsyncSession, plan_id: uuid.UUID, user_id: uuid.UUID
) -> WorkoutPlan | None:
    """Load one of the user's plans with its days and exercises. Async
    SQLAlchemy cannot load relationships lazily, so they are loaded here."""
    statement = (
        select(WorkoutPlan)
        .where(WorkoutPlan.id == plan_id, WorkoutPlan.user_id == user_id)
        .options(
            selectinload(WorkoutPlan.days)
            .selectinload(WorkoutPlanDay.exercises)
            .selectinload(WorkoutPlanExercise.exercise)
        )
        .execution_options(populate_existing=True)
    )
    return await db.scalar(statement)


@router.post("", response_model=WorkoutPlanRead, status_code=status.HTTP_201_CREATED)
async def create_workout_plan(
    current_user: CurrentUser, db: DbSession, llm: LLMClientDep
):
    profile_row = await db.get(Profile, current_user.id)
    if profile_row is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete your profile before generating a plan",
        )
    profile = ProfileRead.model_validate(profile_row)
    exercises = (await db.scalars(select(Exercise))).all()
    # The model can take a long time to answer. Ending the transaction here
    # returns the database connection to the pool while we wait.
    await db.commit()

    try:
        generated = await generate_workout_plan(
            llm,
            profile=profile,
            age=calculate_age(profile.birth_date),
            exercises=exercises,
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
        update(WorkoutPlan)
        .where(WorkoutPlan.user_id == current_user.id, WorkoutPlan.is_active)
        .values(is_active=False)
    )
    plan = WorkoutPlan(
        user_id=current_user.id,
        profile_snapshot=profile.model_dump(mode="json"),
        model=generated.model,
        days=[
            WorkoutPlanDay(
                day_index=day_index,
                focus=day.focus,
                exercises=[
                    WorkoutPlanExercise(
                        exercise_id=item.exercise_id,
                        position=position,
                        sets=item.sets,
                        reps=item.reps,
                        rest_seconds=item.rest_seconds,
                    )
                    for position, item in enumerate(day.exercises, start=1)
                ],
            )
            for day_index, day in enumerate(generated.draft.days, start=1)
        ],
    )
    db.add(plan)
    await db.commit()
    return await _load_plan(db, plan.id, current_user.id)


@router.get("", response_model=list[WorkoutPlanSummary])
async def list_workout_plans(current_user: CurrentUser, db: DbSession):
    statement = (
        select(WorkoutPlan)
        .where(WorkoutPlan.user_id == current_user.id)
        .order_by(WorkoutPlan.created_at.desc())
    )
    return (await db.scalars(statement)).all()


@router.get("/{plan_id}", response_model=WorkoutPlanRead)
async def read_workout_plan(
    plan_id: uuid.UUID, current_user: CurrentUser, db: DbSession
):
    # A plan that belongs to someone else gives the same 404 as one that does
    # not exist, so the endpoint does not reveal which plan ids are real.
    plan = await _load_plan(db, plan_id, current_user.id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workout plan not found",
        )
    return plan
