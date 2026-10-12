from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.enums import Equipment, MuscleGroup
from app.models.exercise import Exercise
from app.schemas.exercise import ExerciseRead

router = APIRouter(prefix="/exercises", tags=["Catalogs"])


@router.get("", response_model=list[ExerciseRead])
async def list_exercises(
    current_user: CurrentUser,
    db: DbSession,
    muscle_group: MuscleGroup | None = None,
    equipment: Annotated[
        list[Equipment] | None,
        Query(
            description=(
                "Equipment you have. Repeat the parameter for several items. "
                "Only exercises that need nothing outside this list are returned."
            )
        ),
    ] = None,
    search: Annotated[
        str | None, Query(max_length=100, description="Text to find in the name.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    statement = select(Exercise)
    if muscle_group is not None:
        statement = statement.where(Exercise.muscle_group == muscle_group)
    if equipment:
        statement = statement.where(Exercise.equipment.contained_by(equipment))
    if search:
        statement = statement.where(Exercise.name.icontains(search, autoescape=True))
    statement = statement.order_by(Exercise.name).limit(limit).offset(offset)
    return (await db.scalars(statement)).all()
