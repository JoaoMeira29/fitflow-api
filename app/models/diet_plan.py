import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.food import Food


class DietPlan(Base):
    __tablename__ = "diet_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # The targets the plan was built for, as computed when it was generated.
    target_kcal: Mapped[int]
    target_protein_g: Mapped[int]
    target_carbs_g: Mapped[int]
    target_fat_g: Mapped[int]
    # Only the user's newest plan is active; older ones are kept as history.
    is_active: Mapped[bool] = mapped_column(default=True)
    # The profile as it was when the plan was generated.
    profile_snapshot: Mapped[dict] = mapped_column(JSONB)
    # The LLM model that produced the plan.
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    meals: Mapped[list["DietPlanMeal"]] = relationship(
        order_by="DietPlanMeal.position", cascade="all, delete-orphan"
    )


class DietPlanMeal(Base):
    __tablename__ = "diet_plan_meals"
    __table_args__ = (UniqueConstraint("plan_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diet_plans.id", ondelete="CASCADE")
    )
    meal_type: Mapped[str] = mapped_column(String(32))
    position: Mapped[int]

    items: Mapped[list["DietPlanMealItem"]] = relationship(
        order_by="DietPlanMealItem.position", cascade="all, delete-orphan"
    )


class DietPlanMealItem(Base):
    __tablename__ = "diet_plan_meal_items"
    __table_args__ = (UniqueConstraint("meal_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diet_plan_meals.id", ondelete="CASCADE")
    )
    # A real foreign key: the database rejects a food that does not exist,
    # and refuses to delete one that a plan uses.
    food_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("foods.id"))
    position: Mapped[int]
    grams: Mapped[float]

    food: Mapped[Food] = relationship()
