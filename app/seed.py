"""Load the exercise and food catalogs into the database.

Run with:  python -m app.seed

It is safe to run again: a row with the same name is updated and keeps its
ID. Rows that were removed from the JSON files are left in the database,
because stored plans may point at them.
"""

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, TypeAdapter
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, SessionLocal
from app.models.exercise import Exercise
from app.models.food import Food
from app.schemas.exercise import ExerciseBase
from app.schemas.food import FoodBase

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_catalog(filename: str, schema: type[BaseModel]) -> list[BaseModel]:
    """Read a JSON file and validate every row against its Pydantic schema,
    so a typo in the data fails here and not in the database."""
    raw = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))
    return TypeAdapter(list[schema]).validate_python(raw)


async def _upsert(
    session: AsyncSession, model: type[Base], rows: list[BaseModel]
) -> int:
    statement = insert(model)
    updated_columns = {
        name: statement.excluded[name] for name in type(rows[0]).model_fields
    }
    statement = statement.on_conflict_do_update(
        index_elements=[model.name], set_=updated_columns
    )
    await session.execute(statement, [row.model_dump() for row in rows])
    return len(rows)


async def seed_catalogs(session: AsyncSession) -> tuple[int, int]:
    """Insert or update both catalogs. Returns (exercises, foods) row counts."""
    exercises = await _upsert(
        session, Exercise, load_catalog("exercises.json", ExerciseBase)
    )
    foods = await _upsert(session, Food, load_catalog("foods.json", FoodBase))
    await session.commit()
    return exercises, foods


async def main() -> None:
    async with SessionLocal() as session:
        exercises, foods = await seed_catalogs(session)
    print(f"Seeded {exercises} exercises and {foods} foods.")


if __name__ == "__main__":
    asyncio.run(main())
