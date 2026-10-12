from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.age import MAX_AGE, MIN_AGE, calculate_age
from app.core.enums import (
    ActivityLevel,
    Allergen,
    DietaryRestriction,
    Equipment,
    Goal,
    Sex,
)


class ProfileBase(BaseModel):
    goal: Goal
    birth_date: date
    sex: Sex
    height_cm: float = Field(ge=120, le=230)
    weight_kg: float = Field(ge=35, le=300)
    activity_level: ActivityLevel
    training_days_per_week: int = Field(ge=1, le=7)
    # At least one item: someone with no gear picks "bodyweight".
    equipment: list[Equipment] = Field(min_length=1)
    allergies: list[Allergen] = []
    dietary_restrictions: list[DietaryRestriction] = []


class ProfileUpdate(ProfileBase):
    @field_validator("birth_date")
    @classmethod
    def check_age(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Birth date cannot be in the future")
        age = calculate_age(value)
        if age < MIN_AGE:
            raise ValueError(f"You must be at least {MIN_AGE} years old")
        if age > MAX_AGE:
            raise ValueError(f"Age cannot be over {MAX_AGE}")
        return value

    @field_validator("equipment", "allergies", "dietary_restrictions")
    @classmethod
    def remove_duplicates(cls, value: list) -> list:
        return list(dict.fromkeys(value))


class ProfileRead(ProfileBase):
    model_config = ConfigDict(from_attributes=True)
