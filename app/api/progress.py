import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.progress import PROMPT_WINDOW_DAYS, ProgressSummary, summarize_progress
from app.models.profile import Profile
from app.models.progress import WeightLog, WorkoutLog
from app.models.workout_plan import WorkoutPlan, WorkoutPlanDay
from app.schemas.progress import (
    ProgressRead,
    ProgressSummaryRead,
    WeightLogCreate,
    WeightLogRead,
    WorkoutLogCreate,
    WorkoutLogRead,
)

router = APIRouter(prefix="/progress", tags=["Progress"])


async def _logs_since(
    db: AsyncSession, user_id: uuid.UUID, first_day: date
) -> tuple[list[WeightLog], list[WorkoutLog]]:
    weight_logs = await db.scalars(
        select(WeightLog)
        .where(WeightLog.user_id == user_id, WeightLog.logged_on >= first_day)
        .order_by(WeightLog.logged_on.desc())
    )
    workout_logs = await db.scalars(
        select(WorkoutLog)
        .where(WorkoutLog.user_id == user_id, WorkoutLog.completed_on >= first_day)
        .order_by(WorkoutLog.completed_on.desc())
    )
    return list(weight_logs), list(workout_logs)


def _summarize(
    weight_logs: list[WeightLog],
    workout_logs: list[WorkoutLog],
    window_days: int,
    training_days_per_week: int | None,
) -> ProgressSummary:
    return summarize_progress(
        [(log.logged_on, log.weight_kg) for log in weight_logs],
        [log.completed_on for log in workout_logs],
        today=date.today(),
        window_days=window_days,
        training_days_per_week=training_days_per_week,
    )


async def recent_progress(
    db: AsyncSession, user_id: uuid.UUID, training_days_per_week: int
) -> ProgressSummary:
    """The summary that goes into the prompt when a plan is regenerated."""
    first_day = date.today() - timedelta(days=PROMPT_WINDOW_DAYS - 1)
    weight_logs, workout_logs = await _logs_since(db, user_id, first_day)
    return _summarize(
        weight_logs, workout_logs, PROMPT_WINDOW_DAYS, training_days_per_week
    )


@router.post(
    "/weight", response_model=WeightLogRead, status_code=status.HTTP_201_CREATED
)
async def log_weight(payload: WeightLogCreate, current_user: CurrentUser, db: DbSession):
    logged_on = payload.logged_on or date.today()
    # One weight per day: logging again on the same day replaces the value.
    statement = (
        insert(WeightLog)
        .values(
            id=uuid.uuid4(),
            user_id=current_user.id,
            weight_kg=payload.weight_kg,
            logged_on=logged_on,
        )
        .on_conflict_do_update(
            index_elements=[WeightLog.user_id, WeightLog.logged_on],
            set_={"weight_kg": payload.weight_kg},
        )
        .returning(WeightLog)
    )
    log = await db.scalar(statement, execution_options={"populate_existing": True})

    # The profile always holds the current weight, so the calorie and macro
    # targets follow the newest log. An older date being filled in does not
    # change it.
    newest_date = await db.scalar(
        select(WeightLog.logged_on)
        .where(WeightLog.user_id == current_user.id)
        .order_by(WeightLog.logged_on.desc())
        .limit(1)
    )
    profile = await db.get(Profile, current_user.id)
    if profile is not None and logged_on == newest_date:
        profile.weight_kg = payload.weight_kg

    await db.commit()
    return log


@router.post(
    "/workouts", response_model=WorkoutLogRead, status_code=status.HTTP_201_CREATED
)
async def log_workout(
    payload: WorkoutLogCreate, current_user: CurrentUser, db: DbSession
):
    # The day must belong to one of this user's plans. Someone else's day
    # gives the same 404 as one that does not exist.
    owned_day = await db.scalar(
        select(WorkoutPlanDay.id)
        .join(WorkoutPlan, WorkoutPlan.id == WorkoutPlanDay.plan_id)
        .where(
            WorkoutPlanDay.id == payload.plan_day_id,
            WorkoutPlan.user_id == current_user.id,
        )
    )
    if owned_day is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workout plan day not found",
        )

    completed_on = payload.completed_on or date.today()
    values = {
        "user_id": current_user.id,
        "plan_day_id": payload.plan_day_id,
        "completed_on": completed_on,
    }
    # Logging the same day twice for the same date changes nothing.
    await db.execute(
        insert(WorkoutLog).values(id=uuid.uuid4(), **values).on_conflict_do_nothing()
    )
    await db.commit()
    return await db.scalar(select(WorkoutLog).filter_by(**values))


@router.get("", response_model=ProgressRead)
async def read_progress(
    current_user: CurrentUser,
    db: DbSession,
    days: Annotated[
        int, Query(ge=1, le=365, description="How many days back to include.")
    ] = 30,
):
    first_day = date.today() - timedelta(days=days - 1)
    weight_logs, workout_logs = await _logs_since(db, current_user.id, first_day)
    profile = await db.get(Profile, current_user.id)
    summary = _summarize(
        weight_logs,
        workout_logs,
        days,
        profile.training_days_per_week if profile else None,
    )
    return ProgressRead(
        summary=ProgressSummaryRead.model_validate(summary),
        weight_logs=[WeightLogRead.model_validate(log) for log in weight_logs],
        workout_logs=[WorkoutLogRead.model_validate(log) for log in workout_logs],
    )
