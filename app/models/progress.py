import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WeightLog(Base):
    __tablename__ = "weight_logs"
    # One weight per user per day; logging again that day replaces it.
    __table_args__ = (UniqueConstraint("user_id", "logged_on"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    weight_kg: Mapped[float]
    logged_on: Mapped[date] = mapped_column(Date)


class WorkoutLog(Base):
    __tablename__ = "workout_logs"
    # A plan day can be completed once per date.
    __table_args__ = (UniqueConstraint("user_id", "plan_day_id", "completed_on"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_day_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workout_plan_days.id", ondelete="CASCADE")
    )
    completed_on: Mapped[date] = mapped_column(Date)
