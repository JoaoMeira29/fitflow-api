import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Food(Base):
    __tablename__ = "foods"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The name is unique so the seed script can be re-run: it updates the row
    # with the same name and keeps its ID.
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kcal_per_100g: Mapped[float]
    protein_per_100g: Mapped[float]
    carbs_per_100g: Mapped[float]
    fat_per_100g: Mapped[float]
    # The largest portion of this food that makes sense in one meal, in
    # grams: 20 for honey, 300 for meat. Diet plans never go above it.
    max_portion_g: Mapped[float]
    allergens: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    # The diets this food is suitable for, e.g. lentils carry all three tags
    # and chicken carries none.
    diet_tags: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
