import pytest
from sqlalchemy import func, select

from app.models.exercise import Exercise
from app.models.food import Food
from app.schemas.exercise import ExerciseBase
from app.schemas.food import FoodBase
from app.seed import load_catalog, seed_catalogs

# --- Catalog data files ----------------------------------------------------
# load_catalog validates every row against its schema, so a bad choice or an
# out-of-range number in the JSON fails these tests.


def test_exercise_data_is_valid_and_has_unique_names():
    exercises = load_catalog("exercises.json", ExerciseBase)

    names = [exercise.name for exercise in exercises]
    assert len(exercises) >= 80
    assert len(names) == len(set(names))


def test_food_data_is_valid_and_has_unique_names():
    foods = load_catalog("foods.json", FoodBase)

    names = [food.name for food in foods]
    assert len(foods) >= 150
    assert len(names) == len(set(names))


def test_food_calories_match_their_macros():
    for food in load_catalog("foods.json", FoodBase):
        from_macros = (
            4 * food.protein_per_100g
            + 4 * food.carbs_per_100g
            + 9 * food.fat_per_100g
        )
        difference = abs(food.kcal_per_100g - from_macros)
        # Fibre and rounding explain small gaps; anything larger is a typo.
        assert difference <= 20 or difference <= 0.15 * food.kcal_per_100g, food.name


def test_food_diet_tags_are_consistent_with_allergens():
    animal_allergens = {"fish", "crustaceans", "molluscs"}
    for food in load_catalog("foods.json", FoodBase):
        tags = set(food.diet_tags)
        allergens = set(food.allergens)
        # Each diet is a subset of the next: vegan < vegetarian < pescatarian.
        if "vegan" in tags:
            assert "vegetarian" in tags, food.name
        if "vegetarian" in tags:
            assert "pescatarian" in tags, food.name
        if allergens & animal_allergens:
            assert "vegetarian" not in tags, food.name
        if allergens & {"milk", "eggs"}:
            assert "vegan" not in tags, food.name


# --- Seed script -----------------------------------------------------------


async def test_seed_fills_both_catalogs(db_session):
    exercises, foods = await seed_catalogs(db_session)

    assert await db_session.scalar(select(func.count()).select_from(Exercise)) == exercises
    assert await db_session.scalar(select(func.count()).select_from(Food)) == foods


async def test_seed_can_run_twice_and_keeps_the_ids(db_session):
    await seed_catalogs(db_session)
    ids_before = set((await db_session.scalars(select(Food.id))).all())

    exercises, foods = await seed_catalogs(db_session)

    ids_after = set((await db_session.scalars(select(Food.id))).all())
    assert ids_after == ids_before
    assert await db_session.scalar(select(func.count()).select_from(Exercise)) == exercises
    assert await db_session.scalar(select(func.count()).select_from(Food)) == foods


async def test_seed_updates_a_row_that_changed(db_session):
    await seed_catalogs(db_session)
    food = await db_session.scalar(select(Food).where(Food.name == "Banana"))
    food.kcal_per_100g = 1
    await db_session.commit()

    await seed_catalogs(db_session)

    await db_session.refresh(food)
    assert food.kcal_per_100g == 89


# --- GET /exercises --------------------------------------------------------


@pytest.fixture
async def exercises(db_session):
    db_session.add_all(
        [
            Exercise(
                name="Push-up",
                muscle_group="chest",
                equipment=["bodyweight"],
                description="A push-up.",
            ),
            Exercise(
                name="Dumbbell bench press",
                muscle_group="chest",
                equipment=["dumbbells", "bench"],
                description="A press.",
            ),
            Exercise(
                name="Dumbbell curl",
                muscle_group="biceps",
                equipment=["dumbbells"],
                description="A curl.",
            ),
            Exercise(
                name="Pull-up",
                muscle_group="back",
                equipment=["pull_up_bar"],
                description="A pull-up.",
            ),
        ]
    )
    await db_session.commit()


def names(response) -> list[str]:
    return [item["name"] for item in response.json()]


async def test_exercises_are_listed_by_name(client, auth_headers, exercises):
    response = await client.get("/exercises", headers=auth_headers)

    assert response.status_code == 200
    assert names(response) == [
        "Dumbbell bench press",
        "Dumbbell curl",
        "Pull-up",
        "Push-up",
    ]
    assert response.json()[0] == {
        "id": response.json()[0]["id"],
        "name": "Dumbbell bench press",
        "muscle_group": "chest",
        "equipment": ["dumbbells", "bench"],
        "description": "A press.",
    }


async def test_exercises_filter_by_muscle_group(client, auth_headers, exercises):
    response = await client.get(
        "/exercises", params={"muscle_group": "chest"}, headers=auth_headers
    )

    assert names(response) == ["Dumbbell bench press", "Push-up"]


async def test_exercises_filter_by_equipment_needs_every_item(
    client, auth_headers, exercises
):
    # The bench press also needs a bench, so dumbbells alone are not enough.
    response = await client.get(
        "/exercises", params={"equipment": "dumbbells"}, headers=auth_headers
    )

    assert names(response) == ["Dumbbell curl"]


async def test_exercises_filter_by_several_equipment_items(
    client, auth_headers, exercises
):
    response = await client.get(
        "/exercises",
        params={"equipment": ["dumbbells", "bench", "bodyweight"]},
        headers=auth_headers,
    )

    assert names(response) == ["Dumbbell bench press", "Dumbbell curl", "Push-up"]


async def test_exercises_search_ignores_case(client, auth_headers, exercises):
    response = await client.get(
        "/exercises", params={"search": "PUSH"}, headers=auth_headers
    )

    assert names(response) == ["Push-up"]


async def test_exercises_search_treats_wildcards_as_text(
    client, auth_headers, exercises
):
    response = await client.get(
        "/exercises", params={"search": "%"}, headers=auth_headers
    )

    assert names(response) == []


async def test_exercises_combine_filters(client, auth_headers, exercises):
    response = await client.get(
        "/exercises",
        params={"muscle_group": "chest", "equipment": "bodyweight"},
        headers=auth_headers,
    )

    assert names(response) == ["Push-up"]


async def test_exercises_limit_and_offset(client, auth_headers, exercises):
    response = await client.get(
        "/exercises", params={"limit": 2, "offset": 1}, headers=auth_headers
    )

    assert names(response) == ["Dumbbell curl", "Pull-up"]


async def test_exercises_reject_an_unknown_muscle_group(client, auth_headers):
    response = await client.get(
        "/exercises", params={"muscle_group": "wings"}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_exercises_reject_a_limit_over_the_maximum(client, auth_headers):
    response = await client.get(
        "/exercises", params={"limit": 201}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_exercises_require_a_token(client):
    response = await client.get("/exercises")

    assert response.status_code == 401


# --- GET /foods ------------------------------------------------------------


@pytest.fixture
async def foods(db_session):
    def food(name, allergens, diet_tags):
        return Food(
            name=name,
            kcal_per_100g=100,
            protein_per_100g=10,
            carbs_per_100g=10,
            fat_per_100g=2,
            allergens=allergens,
            diet_tags=diet_tags,
        )

    db_session.add_all(
        [
            food("Chicken breast", [], []),
            food("Salmon", ["fish"], ["pescatarian"]),
            food("Greek yogurt", ["milk"], ["vegetarian", "pescatarian"]),
            food("Lentils", [], ["vegetarian", "vegan", "pescatarian"]),
            food("Peanut butter", ["peanuts"], ["vegetarian", "vegan", "pescatarian"]),
        ]
    )
    await db_session.commit()


async def test_foods_are_listed_by_name(client, auth_headers, foods):
    response = await client.get("/foods", headers=auth_headers)

    assert response.status_code == 200
    assert names(response) == [
        "Chicken breast",
        "Greek yogurt",
        "Lentils",
        "Peanut butter",
        "Salmon",
    ]
    assert response.json()[4] == {
        "id": response.json()[4]["id"],
        "name": "Salmon",
        "kcal_per_100g": 100,
        "protein_per_100g": 10,
        "carbs_per_100g": 10,
        "fat_per_100g": 2,
        "allergens": ["fish"],
        "diet_tags": ["pescatarian"],
    }


@pytest.mark.parametrize(
    "diet, expected",
    [
        ("vegan", ["Lentils", "Peanut butter"]),
        ("vegetarian", ["Greek yogurt", "Lentils", "Peanut butter"]),
        ("pescatarian", ["Greek yogurt", "Lentils", "Peanut butter", "Salmon"]),
    ],
)
async def test_foods_filter_by_diet(client, auth_headers, foods, diet, expected):
    response = await client.get("/foods", params={"diet": diet}, headers=auth_headers)

    assert names(response) == expected


async def test_foods_exclude_one_allergen(client, auth_headers, foods):
    response = await client.get(
        "/foods", params={"exclude_allergen": "milk"}, headers=auth_headers
    )

    assert names(response) == ["Chicken breast", "Lentils", "Peanut butter", "Salmon"]


async def test_foods_exclude_several_allergens(client, auth_headers, foods):
    response = await client.get(
        "/foods",
        params={"exclude_allergen": ["milk", "fish", "peanuts"]},
        headers=auth_headers,
    )

    assert names(response) == ["Chicken breast", "Lentils"]


async def test_foods_combine_diet_and_allergens(client, auth_headers, foods):
    response = await client.get(
        "/foods",
        params={"diet": "vegan", "exclude_allergen": "peanuts"},
        headers=auth_headers,
    )

    assert names(response) == ["Lentils"]


async def test_foods_search_ignores_case(client, auth_headers, foods):
    response = await client.get(
        "/foods", params={"search": "YOG"}, headers=auth_headers
    )

    assert names(response) == ["Greek yogurt"]


async def test_foods_limit_and_offset(client, auth_headers, foods):
    response = await client.get(
        "/foods", params={"limit": 2, "offset": 3}, headers=auth_headers
    )

    assert names(response) == ["Peanut butter", "Salmon"]


async def test_foods_reject_an_unknown_allergen(client, auth_headers):
    response = await client.get(
        "/foods", params={"exclude_allergen": "dust"}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_foods_require_a_token(client):
    response = await client.get("/foods")

    assert response.status_code == 401
