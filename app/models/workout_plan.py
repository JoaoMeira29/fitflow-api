import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.exercise import Exercise


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Only the user's newest plan is active; older ones are kept as history.
    is_active: Mapped[bool] = mapped_column(default=True)
    # The profile as it was when the plan was generated, so the plan still
    # makes sense after the user edits their profile.
    profile_snapshot: Mapped[dict] = mapped_column(JSONB)
    # The LLM model that produced the plan.
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    days: Mapped[list["WorkoutPlanDay"]] = relationship(
        order_by="WorkoutPlanDay.day_index", cascade="all, delete-orphan"
    )


class WorkoutPlanDay(Base):
    __tablename__ = "workout_plan_days"
    __table_args__ = (UniqueConstraint("plan_id", "day_index"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workout_plans.id", ondelete="CASCADE")
    )
    day_index: Mapped[int]
    focus: Mapped[str] = mapped_column(String(100))

    exercises: Mapped[list["WorkoutPlanExercise"]] = relationship(
        order_by="WorkoutPlanExercise.position", cascade="all, delete-orphan"
    )


class WorkoutPlanExercise(Base):
    __tablename__ = "workout_plan_exercises"
    __table_args__ = (UniqueConstraint("day_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    day_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workout_plan_days.id", ondelete="CASCADE")
    )
    # A real foreign key: the database rejects an exercise that does not
    # exist, and refuses to delete one that a plan uses.
    exercise_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exercises.id"))
    position: Mapped[int]
    sets: Mapped[int]
    reps: Mapped[int]
    rest_seconds: Mapped[int]

    exercise: Mapped[Exercise] = relationship()
