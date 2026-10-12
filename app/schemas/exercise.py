import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Equipment, MuscleGroup


class ExerciseBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    muscle_group: MuscleGroup
    equipment: list[Equipment] = Field(min_length=1)
    description: str = Field(min_length=1)


class ExerciseRead(ExerciseBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
