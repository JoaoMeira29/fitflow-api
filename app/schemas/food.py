import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Allergen, DietaryRestriction


class FoodBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kcal_per_100g: float = Field(ge=0, le=900)
    protein_per_100g: float = Field(ge=0, le=100)
    carbs_per_100g: float = Field(ge=0, le=100)
    fat_per_100g: float = Field(ge=0, le=100)
    max_portion_g: float = Field(gt=0, le=600)
    allergens: list[Allergen]
    diet_tags: list[DietaryRestriction]


class FoodRead(FoodBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
