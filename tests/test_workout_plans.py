import json
import uuid

import pytest
from sqlalchemy import func, select

from app.core.llm import LLMError
from app.core.workout_plans import (
    available_equipment,
    doable_exercises,
    plan_json_schema,
    validate_workout_plan,
)
from app.models.exercise import Exercise
from app.models.workout_plan import WorkoutPlan, WorkoutPlanExercise
from app.schemas.workout import MEDICAL_NOTICE, WorkoutPlanDraft

PROFILE = {
    "goal": "gain_muscle",
    "birth_date": "1995-06-15",
    "sex": "male",
    "height_cm": 180.0,
    "weight_kg": 80.0,
    "activity_level": "moderate",
    "training_days_per_week": 2,
    "equipment": ["dumbbells"],
    "allergies": [],
    "dietary_restrictions": [],
}


def make_exercise(name: str, equipment: list[str], muscle_group="chest") -> Exercise:
    return Exercise(
        id=uuid.uuid4(),
        name=name,
        muscle_group=muscle_group,
        equipment=equipment,
        description=f"How to do a {name}.",
    )


@pytest.fixture
async def catalog(db_session) -> dict[str, Exercise]:
    """A small catalog: five exercises the test user can do and one they
    cannot (no barbell or bench)."""
    exercises = [
        make_exercise("Push-up", ["bodyweight"]),
        make_exercise("Plank", ["bodyweight"], "core"),
        make_exercise("Bodyweight squat", ["bodyweight"], "quadriceps"),
        make_exercise("Dumbbell curl", ["dumbbells"], "biceps"),
        make_exercise("Dumbbell shoulder press", ["dumbbells"], "shoulders"),
        make_exercise("Barbell bench press", ["barbell", "bench"]),
    ]
    db_session.add_all(exercises)
    await db_session.commit()
    return {exercise.name: exercise for exercise in exercises}


def day(catalog, *names: str, focus="Full body") -> dict:
    return {
        "focus": focus,
        "exercises": [
            {
                "exercise_id": str(catalog[name].id),
                "sets": 3,
                "reps": 10,
                "rest_seconds": 60,
            }
            for name in names
        ],
    }


def plan_json(*days: dict) -> str:
    return json.dumps({"days": list(days)})


def valid_plan(catalog) -> str:
    return plan_json(
        day(catalog, "Push-up", "Dumbbell curl", "Plank", focus="Upper body"),
        day(catalog, "Bodyweight squat", "Dumbbell shoulder press", "Plank"),
    )


def validate(catalog, plan: str, training_days=2, equipment=("dumbbells",)) -> list[str]:
    return validate_workout_plan(
        WorkoutPlanDraft.model_validate_json(plan),
        training_days=training_days,
        exercises_by_id={exercise.id: exercise for exercise in catalog.values()},
        equipment=available_equipment(equipment),
    )


@pytest.fixture
async def profile(client, auth_headers):
    await client.put("/profile", json=PROFILE, headers=auth_headers)


# --- Equipment -------------------------------------------------------------


def test_bodyweight_is_always_available():
    assert available_equipment(["dumbbells"]) == {"dumbbells", "bodyweight"}


def test_doable_exercises_need_every_item(catalog):
    doable = doable_exercises(catalog.values(), {"dumbbells", "bodyweight"})

    assert "Barbell bench press" not in [exercise.name for exercise in doable]
    assert len(doable) == 5


# --- validate_workout_plan -------------------------------------------------


def test_a_valid_plan_has_no_errors(catalog):
    assert validate(catalog, valid_plan(catalog)) == []


def test_wrong_number_of_days_is_an_error(catalog):
    errors = validate(catalog, valid_plan(catalog), training_days=3)

    assert errors == ["The plan has 2 days but must have exactly 3."]


def test_unknown_exercise_id_is_an_error(catalog):
    plan = json.loads(valid_plan(catalog))
    invented = str(uuid.uuid4())
    plan["days"][0]["exercises"][0]["exercise_id"] = invented

    errors = validate(catalog, json.dumps(plan))

    assert errors == [
        f"Day 1: exercise id {invented} is not in the list of available exercises."
    ]


def test_exercise_needing_missing_equipment_is_an_error(catalog):
    plan = plan_json(
        day(catalog, "Push-up", "Barbell bench press", "Plank"),
        day(catalog, "Bodyweight squat", "Dumbbell curl", "Plank"),
    )

    errors = validate(catalog, plan)

    assert errors == [
        "Day 1: 'Barbell bench press' needs equipment the user does not have "
        "(barbell, bench)."
    ]


def test_repeated_exercise_in_a_day_is_an_error(catalog):
    plan = plan_json(
        day(catalog, "Push-up", "Push-up", "Plank"),
        day(catalog, "Bodyweight squat", "Dumbbell curl", "Plank"),
    )

    assert validate(catalog, plan) == ["Day 1: 'Push-up' appears more than once."]


def test_the_same_exercise_on_different_days_is_allowed(catalog):
    assert validate(catalog, valid_plan(catalog)) == []  # Plank is on both days


def test_every_problem_is_reported_at_once(catalog):
    plan = plan_json(day(catalog, "Push-up", "Push-up", "Barbell bench press"))

    assert len(validate(catalog, plan)) == 3


# --- plan_json_schema ------------------------------------------------------


def test_schema_only_allows_the_offered_exercises_and_day_count():
    ids = [uuid.uuid4(), uuid.uuid4()]

    schema = plan_json_schema(training_days=4, allowed_ids=ids)

    exercise_id = schema["$defs"]["WorkoutExerciseDraft"]["properties"]["exercise_id"]
    assert exercise_id["enum"] == [str(ids[0]), str(ids[1])]
    assert schema["properties"]["days"]["minItems"] == 4
    assert schema["properties"]["days"]["maxItems"] == 4


# --- POST /plans/workout ---------------------------------------------------


async def test_post_creates_and_returns_the_plan(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    response = await client.post("/plans/workout", headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["is_active"] is True
    assert body["model"] == "fake-model"
    assert body["notice"] == MEDICAL_NOTICE
    assert [d["day_index"] for d in body["days"]] == [1, 2]
    assert body["days"][0]["focus"] == "Upper body"
    first = body["days"][0]["exercises"][0]
    assert first["position"] == 1
    assert first["sets"] == 3
    assert first["reps"] == 10
    assert first["rest_seconds"] == 60
    assert first["exercise"]["name"] == "Push-up"
    assert first["exercise"]["id"] == str(catalog["Push-up"].id)


async def test_post_only_offers_exercises_the_user_can_do(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]

    await client.post("/plans/workout", headers=auth_headers)

    call = fake_llm.calls[0]
    prompt = call["messages"][1]["content"]
    assert "Dumbbell curl" in prompt
    assert "Push-up" in prompt
    assert "Barbell bench press" not in prompt
    assert "Training days per week: 2" in prompt
    allowed = call["json_schema"]["$defs"]["WorkoutExerciseDraft"]["properties"][
        "exercise_id"
    ]["enum"]
    assert str(catalog["Barbell bench press"].id) not in allowed
    assert len(allowed) == 5


async def test_post_stores_a_snapshot_of_the_profile(
    client, auth_headers, catalog, profile, fake_llm, db_session
):
    fake_llm.replies = [valid_plan(catalog)]

    await client.post("/plans/workout", headers=auth_headers)

    plan = await db_session.scalar(select(WorkoutPlan))
    assert plan.profile_snapshot == PROFILE


async def test_post_retries_with_the_validation_errors(
    client, auth_headers, catalog, profile, fake_llm
):
    invalid = plan_json(day(catalog, "Push-up", "Dumbbell curl", "Plank"))
    fake_llm.replies = [invalid, valid_plan(catalog)]

    response = await client.post("/plans/workout", headers=auth_headers)

    assert response.status_code == 201
    assert len(fake_llm.calls) == 2
    retry_messages = fake_llm.calls[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": invalid}
    assert retry_messages[-1]["role"] == "user"
    assert "The plan has 1 days but must have exactly 2." in retry_messages[-1]["content"]


async def test_post_retries_when_the_reply_does_not_match_the_schema(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = ["this is not JSON", valid_plan(catalog)]

    response = await client.post("/plans/workout", headers=auth_headers)

    assert response.status_code == 201
    assert len(fake_llm.calls) == 2


async def test_post_gives_up_after_two_retries(
    client, auth_headers, catalog, profile, fake_llm, db_session
):
    invalid = plan_json(day(catalog, "Push-up", "Dumbbell curl", "Plank"))
    fake_llm.replies = [invalid, invalid, invalid, valid_plan(catalog)]

    response = await client.post("/plans/workout", headers=auth_headers)

    assert response.status_code == 502
    assert len(fake_llm.calls) == 3
    assert response.json()["detail"]["errors"] == [
        "The plan has 1 days but must have exactly 2."
    ]
    assert await db_session.scalar(select(func.count()).select_from(WorkoutPlan)) == 0


async def test_post_returns_503_when_the_llm_is_unavailable(
    client, auth_headers, catalog, profile, fake_llm, db_session
):
    fake_llm.replies = [LLMError("could not connect")]

    response = await client.post("/plans/workout", headers=auth_headers)

    assert response.status_code == 503
    assert await db_session.scalar(select(func.count()).select_from(WorkoutPlan)) == 0


async def test_post_requires_a_profile(client, auth_headers, catalog, fake_llm):
    response = await client.post("/plans/workout", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Complete your profile before generating a plan"
    assert fake_llm.calls == []


async def test_post_requires_a_token(client, fake_llm):
    response = await client.post("/plans/workout")

    assert response.status_code == 401


async def test_a_new_plan_deactivates_the_previous_one(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog), valid_plan(catalog)]

    first = (await client.post("/plans/workout", headers=auth_headers)).json()
    second = (await client.post("/plans/workout", headers=auth_headers)).json()

    plans = (await client.get("/plans/workout", headers=auth_headers)).json()
    active = {plan["id"]: plan["is_active"] for plan in plans}
    assert active == {first["id"]: False, second["id"]: True}


# --- GET /plans/workout ----------------------------------------------------


async def test_list_returns_the_newest_plan_first(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog), valid_plan(catalog)]
    first = (await client.post("/plans/workout", headers=auth_headers)).json()
    second = (await client.post("/plans/workout", headers=auth_headers)).json()

    response = await client.get("/plans/workout", headers=auth_headers)

    assert response.status_code == 200
    assert [plan["id"] for plan in response.json()] == [second["id"], first["id"]]
    assert "days" not in response.json()[0]


async def test_list_is_empty_without_plans(client, auth_headers):
    response = await client.get("/plans/workout", headers=auth_headers)

    assert response.json() == []


async def test_list_requires_a_token(client):
    response = await client.get("/plans/workout")

    assert response.status_code == 401


# --- GET /plans/workout/{id} -----------------------------------------------


async def test_get_returns_the_full_plan(
    client, auth_headers, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]
    created = (await client.post("/plans/workout", headers=auth_headers)).json()

    response = await client.get(
        f"/plans/workout/{created['id']}", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == created


async def test_get_returns_404_for_an_unknown_plan(client, auth_headers):
    response = await client.get(
        f"/plans/workout/{uuid.uuid4()}", headers=auth_headers
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workout plan not found"


async def test_get_returns_404_for_another_users_plan(
    client, auth_headers, auth_headers_for, catalog, profile, fake_llm
):
    fake_llm.replies = [valid_plan(catalog)]
    created = (await client.post("/plans/workout", headers=auth_headers)).json()
    rui = await auth_headers_for("rui@example.com")

    response = await client.get(f"/plans/workout/{created['id']}", headers=rui)

    assert response.status_code == 404


async def test_get_rejects_an_id_that_is_not_a_uuid(client, auth_headers):
    response = await client.get("/plans/workout/not-a-uuid", headers=auth_headers)

    assert response.status_code == 422


# --- Database --------------------------------------------------------------


async def test_deleting_a_plan_deletes_its_days_and_exercises(
    client, auth_headers, catalog, profile, fake_llm, db_session
):
    fake_llm.replies = [valid_plan(catalog)]
    await client.post("/plans/workout", headers=auth_headers)

    plan = await db_session.scalar(select(WorkoutPlan))
    await db_session.delete(plan)
    await db_session.commit()

    count = select(func.count()).select_from(WorkoutPlanExercise)
    assert await db_session.scalar(count) == 0
