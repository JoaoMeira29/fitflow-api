from pydantic import BaseModel, ConfigDict


class TargetsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bmr: int
    tdee: int
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int
    calorie_floor_applied: bool
    weight_change_cap_applied: bool
