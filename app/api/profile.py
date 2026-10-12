from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import CurrentUser, DbSession
from app.core.age import calculate_age
from app.core.config import settings
from app.core.enums import ActivityLevel, Goal, Sex
from app.core.targets import SafetyLimits, Targets, calculate_targets
from app.models.profile import Profile
from app.schemas.profile import ProfileRead, ProfileUpdate
from app.schemas.targets import TargetsRead

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.put("", response_model=ProfileRead)
async def upsert_profile(
    payload: ProfileUpdate, current_user: CurrentUser, db: DbSession
):
    values = payload.model_dump()
    # INSERT ... ON CONFLICT DO UPDATE creates or replaces the profile in one
    # atomic statement, so two simultaneous requests cannot both try to insert.
    statement = (
        insert(Profile)
        .values(user_id=current_user.id, **values)
        .on_conflict_do_update(index_elements=[Profile.user_id], set_=values)
        .returning(Profile)
    )
    profile = await db.scalar(
        statement, execution_options={"populate_existing": True}
    )
    await db.commit()
    return profile


async def get_profile_or_404(current_user: CurrentUser, db: DbSession) -> Profile:
    profile = await db.get(Profile, current_user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    return profile


CurrentProfile = Annotated[Profile, Depends(get_profile_or_404)]


@router.get("", response_model=ProfileRead)
async def read_profile(profile: CurrentProfile):
    return profile


def targets_for_profile(
    profile: Profile | ProfileRead, requested_calories: int | None = None
) -> Targets:
    """The calorie and macro targets for a profile. The safety limits come
    from the settings; the arithmetic is pure Python. Raises
    CalorieTargetError if `requested_calories` is outside the safety limits."""
    limits = SafetyLimits(
        min_calories_male=settings.MIN_DAILY_CALORIES_MALE,
        min_calories_female=settings.MIN_DAILY_CALORIES_FEMALE,
        max_weekly_weight_change_percent=settings.MAX_WEEKLY_WEIGHT_CHANGE_PERCENT,
    )
    return calculate_targets(
        sex=Sex(profile.sex),
        age=calculate_age(profile.birth_date),
        weight_kg=profile.weight_kg,
        height_cm=profile.height_cm,
        activity_level=ActivityLevel(profile.activity_level),
        goal=Goal(profile.goal),
        limits=limits,
        requested_calories=requested_calories,
    )


@router.get("/targets", response_model=TargetsRead)
async def read_targets(profile: CurrentProfile):
    return targets_for_profile(profile)
