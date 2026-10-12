from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.enums import Allergen, DietaryRestriction
from app.models.food import Food
from app.schemas.food import FoodRead

router = APIRouter(prefix="/foods", tags=["Catalogs"])


@router.get("", response_model=list[FoodRead])
async def list_foods(
    current_user: CurrentUser,
    db: DbSession,
    diet: Annotated[
        DietaryRestriction | None,
        Query(description="Only foods suitable for this diet."),
    ] = None,
    exclude_allergen: Annotated[
        list[Allergen] | None,
        Query(
            description=(
                "Leave out foods containing this allergen. Repeat the "
                "parameter for several allergens."
            )
        ),
    ] = None,
    search: Annotated[
        str | None, Query(max_length=100, description="Text to find in the name.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    statement = select(Food)
    if diet is not None:
        statement = statement.where(Food.diet_tags.contains([diet]))
    if exclude_allergen:
        statement = statement.where(~Food.allergens.overlap(exclude_allergen))
    if search:
        statement = statement.where(Food.name.icontains(search, autoescape=True))
    statement = statement.order_by(Food.name).limit(limit).offset(offset)
    return (await db.scalars(statement)).all()
