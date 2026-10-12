import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Profile(Base):
    __tablename__ = "profiles"

    # The user's ID is the primary key, so a user can have at most one profile.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    goal: Mapped[str] = mapped_column(String(32))
    birth_date: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(String(32))
    height_cm: Mapped[float]
    weight_kg: Mapped[float]
    activity_level: Mapped[str] = mapped_column(String(32))
    training_days_per_week: Mapped[int]
    equipment: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    allergies: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    dietary_restrictions: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
