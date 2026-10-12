import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _not_in_the_future(value: date | None) -> date | None:
    if value is not None and value > date.today():
        raise ValueError("The date cannot be in the future")
    return value


class WeightLogCreate(BaseModel):
    # The same limits as the profile's weight.
    weight_kg: float = Field(ge=35, le=300)
    # Defaults to today.
    logged_on: date | None = None

    _check_date = field_validator("logged_on")(_not_in_the_future)


class WeightLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    weight_kg: float
    logged_on: date


class WorkoutLogCreate(BaseModel):
    # The day of one of your workout plans that you completed.
    plan_day_id: uuid.UUID
    # Defaults to today.
    completed_on: date | None = None

    _check_date = field_validator("completed_on")(_not_in_the_future)


class WorkoutLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_day_id: uuid.UUID
    completed_on: date


class ProgressSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    window_days: int
    start_weight_kg: float | None
    current_weight_kg: float | None
    weight_change_kg: float | None
    workouts_completed: int
    workouts_planned: int | None


class ProgressRead(BaseModel):
    summary: ProgressSummaryRead
    # Both lists are newest first and cover the same window as the summary.
    weight_logs: list[WeightLogRead]
    workout_logs: list[WorkoutLogRead]
