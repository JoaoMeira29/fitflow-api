import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.core.progress import ProgressSummary, progress_prompt, summarize_progress
from app.models.progress import WeightLog, WorkoutLog
from app.models.user import User
from app.models.workout_plan import WorkoutPlan, WorkoutPlanDay

TODAY = date.today()


def days_ago(days: int) -> date:
    return TODAY - timedelta(days=days)


PROFILE = {
    "goal": "lose_weight",
    "birth_date": "1995-06-15",
    "sex": "male",
    "height_cm": 180.0,
    "weight_kg": 90.0,
    "activity_level": "moderate",
    "training_days_per_week": 4,
    "equipment": ["bodyweight"],
    "allergies": [],
    "dietary_restrictions": [],
}


@pytest.fixture
async def profile(client, auth_headers):
    await client.put("/profile", json=PROFILE, headers=auth_headers)


async def add_plan_day(db_session, email: str) -> uuid.UUID:
    """Give the user a workout plan with one day, and return that day's id."""
    user = await db_session.scalar(select(User).where(User.email == email))
    plan = WorkoutPlan(
        user_id=user.id,
        profile_snapshot={},
        model="test-model",
        days=[WorkoutPlanDay(day_index=1, focus="Full body")],
    )
    db_session.add(plan)
    await db_session.commit()
    return plan.days[0].id


@pytest.fixture
async def plan_day_id(auth_headers, db_session) -> uuid.UUID:
    return await add_plan_day(db_session, "ana@example.com")


async def log_weight(client, headers, weight_kg: float, logged_on: date | None = None):
    body = {"weight_kg": weight_kg}
    if logged_on is not None:
        body["logged_on"] = logged_on.isoformat()
    return await client.post("/progress/weight", json=body, headers=headers)


async def log_workout(client, headers, day_id, completed_on: date | None = None):
    body = {"plan_day_id": str(day_id)}
    if completed_on is not None:
        body["completed_on"] = completed_on.isoformat()
    return await client.post("/progress/workouts", json=body, headers=headers)


async def count(db_session, model) -> int:
    return await db_session.scalar(select(func.count()).select_from(model))


# --- summarize_progress ----------------------------------------------------

D = date(2026, 3, 31)


def summarize(weights=(), workouts=(), window_days=28, training_days=4):
    return summarize_progress(
        weights,
        workouts,
        today=D,
        window_days=window_days,
        training_days_per_week=training_days,
    )


def test_summary_of_no_logs():
    summary = summarize()

    assert summary == ProgressSummary(
        window_days=28,
        start_weight_kg=None,
        current_weight_kg=None,
        weight_change_kg=None,
        workouts_completed=0,
        workouts_planned=16,
    )
    assert summary.has_data is False


def test_summary_weight_change_is_latest_minus_first():
    summary = summarize(
        weights=[
            (date(2026, 3, 20), 83.0),
            (date(2026, 3, 10), 84.5),
            (date(2026, 3, 30), 82.4),
        ]
    )

    assert summary.start_weight_kg == 84.5
    assert summary.current_weight_kg == 82.4
    assert summary.weight_change_kg == -2.1


def test_summary_with_one_weight_has_no_change():
    summary = summarize(weights=[(date(2026, 3, 30), 82.4)])

    assert summary.current_weight_kg == 82.4
    assert summary.weight_change_kg is None
    assert summary.has_data is True


def test_summary_ignores_logs_outside_the_window():
    # A 28-day window ending on 31 March starts on 4 March.
    summary = summarize(
        weights=[(date(2026, 3, 3), 99.0), (date(2026, 3, 4), 85.0)],
        workouts=[date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 31)],
    )

    assert summary.start_weight_kg == 85.0
    assert summary.workouts_completed == 2


def test_summary_ignores_logs_dated_after_today():
    summary = summarize(
        weights=[(date(2026, 4, 1), 70.0)], workouts=[date(2026, 4, 1)]
    )

    assert summary.has_data is False


@pytest.mark.parametrize(
    "window_days, training_days, expected",
    [(28, 4, 16), (7, 3, 3), (30, 4, 17), (28, None, None)],
)
def test_summary_planned_workouts(window_days, training_days, expected):
    summary = summarize(window_days=window_days, training_days=training_days)

    assert summary.workouts_planned == expected


# --- progress_prompt -------------------------------------------------------


def test_prompt_is_empty_without_data():
    assert progress_prompt(None) == ""
    assert progress_prompt(summarize()) == ""


def test_prompt_shows_weight_change_and_adherence():
    summary = summarize(
        weights=[(date(2026, 3, 10), 84.5), (date(2026, 3, 30), 82.0)],
        workouts=[date(2026, 3, 12), date(2026, 3, 14)],
    )

    assert progress_prompt(summary) == (
        "Recent progress (last 28 days):\n"
        "- Weight: 84.5 kg -> 82 kg (-2.5 kg)\n"
        "- Workouts completed: 2 of about 16 planned\n"
        "Take this into account when you build the plan."
    )


def test_prompt_shows_a_weight_gain_with_a_plus_sign():
    summary = summarize(weights=[(date(2026, 3, 10), 70.0), (date(2026, 3, 30), 71.5)])

    assert "- Weight: 70 kg -> 71.5 kg (+1.5 kg)" in progress_prompt(summary)


def test_prompt_with_a_single_weight():
    summary = summarize(weights=[(date(2026, 3, 30), 82.0)])

    assert "- Weight: 82 kg\n" in progress_prompt(summary)


# --- POST /progress/weight -------------------------------------------------


async def test_weight_is_logged_for_today_by_default(client, auth_headers):
    response = await log_weight(client, auth_headers, 88.5)

    assert response.status_code == 201
    body = response.json()
    assert body["weight_kg"] == 88.5
    assert body["logged_on"] == TODAY.isoformat()
    assert uuid.UUID(body["id"])


async def test_weight_can_be_logged_for_a_past_date(client, auth_headers):
    response = await log_weight(client, auth_headers, 89.0, days_ago(3))

    assert response.json()["logged_on"] == days_ago(3).isoformat()


async def test_logging_twice_on_one_day_replaces_the_weight(
    client, auth_headers, db_session
):
    first = await log_weight(client, auth_headers, 88.5)
    second = await log_weight(client, auth_headers, 88.1)

    assert second.status_code == 201
    assert second.json()["weight_kg"] == 88.1
    assert second.json()["id"] == first.json()["id"]
    assert await count(db_session, WeightLog) == 1


@pytest.mark.parametrize("weight_kg", [34.9, 300.1, "heavy"])
async def test_weight_out_of_range_is_rejected(client, auth_headers, weight_kg):
    response = await client.post(
        "/progress/weight", json={"weight_kg": weight_kg}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_weight_cannot_be_logged_in_the_future(client, auth_headers):
    response = await log_weight(client, auth_headers, 88.0, TODAY + timedelta(days=1))

    assert response.status_code == 422


async def test_logging_weight_requires_a_token(client):
    response = await client.post("/progress/weight", json={"weight_kg": 88.0})

    assert response.status_code == 401


async def test_weight_can_be_logged_without_a_profile(client, auth_headers):
    response = await log_weight(client, auth_headers, 88.0)

    assert response.status_code == 201


async def test_the_newest_weight_updates_the_profile_and_the_targets(
    client, auth_headers, profile
):
    before = (await client.get("/profile/targets", headers=auth_headers)).json()

    await log_weight(client, auth_headers, 85.0)

    saved = (await client.get("/profile", headers=auth_headers)).json()
    after = (await client.get("/profile/targets", headers=auth_headers)).json()
    assert saved["weight_kg"] == 85.0
    assert after["bmr"] == before["bmr"] - 50  # 10 kcal per kg, 5 kg lighter
    assert after["protein_g"] == 170  # 2.0 g per kg of 85 kg


async def test_an_older_weight_does_not_change_the_profile(
    client, auth_headers, profile
):
    await log_weight(client, auth_headers, 85.0, days_ago(1))

    await log_weight(client, auth_headers, 95.0, days_ago(10))

    saved = (await client.get("/profile", headers=auth_headers)).json()
    assert saved["weight_kg"] == 85.0


# --- POST /progress/workouts -----------------------------------------------


async def test_workout_is_logged_for_today_by_default(
    client, auth_headers, plan_day_id
):
    response = await log_workout(client, auth_headers, plan_day_id)

    assert response.status_code == 201
    body = response.json()
    assert body["plan_day_id"] == str(plan_day_id)
    assert body["completed_on"] == TODAY.isoformat()


async def test_workout_can_be_logged_for_a_past_date(
    client, auth_headers, plan_day_id
):
    response = await log_workout(client, auth_headers, plan_day_id, days_ago(2))

    assert response.json()["completed_on"] == days_ago(2).isoformat()


async def test_logging_the_same_workout_twice_changes_nothing(
    client, auth_headers, plan_day_id, db_session
):
    first = await log_workout(client, auth_headers, plan_day_id)
    second = await log_workout(client, auth_headers, plan_day_id)

    assert second.status_code == 201
    assert second.json() == first.json()
    assert await count(db_session, WorkoutLog) == 1


async def test_the_same_day_can_be_completed_on_different_dates(
    client, auth_headers, plan_day_id, db_session
):
    await log_workout(client, auth_headers, plan_day_id, days_ago(7))
    await log_workout(client, auth_headers, plan_day_id)

    assert await count(db_session, WorkoutLog) == 2


async def test_workout_for_an_unknown_plan_day_is_rejected(client, auth_headers):
    response = await log_workout(client, auth_headers, uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["detail"] == "Workout plan day not found"


async def test_workout_for_another_users_plan_day_is_rejected(
    client, auth_headers, auth_headers_for, db_session
):
    await auth_headers_for("rui@example.com")
    ruis_day = await add_plan_day(db_session, "rui@example.com")

    response = await log_workout(client, auth_headers, ruis_day)

    assert response.status_code == 404
    assert await count(db_session, WorkoutLog) == 0


async def test_workout_cannot_be_logged_in_the_future(
    client, auth_headers, plan_day_id
):
    response = await log_workout(
        client, auth_headers, plan_day_id, TODAY + timedelta(days=1)
    )

    assert response.status_code == 422


async def test_logging_a_workout_requires_a_token(client):
    response = await client.post(
        "/progress/workouts", json={"plan_day_id": str(uuid.uuid4())}
    )

    assert response.status_code == 401


# --- GET /progress ---------------------------------------------------------


async def test_progress_is_empty_for_a_new_user(client, auth_headers):
    response = await client.get("/progress", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "summary": {
            "window_days": 30,
            "start_weight_kg": None,
            "current_weight_kg": None,
            "weight_change_kg": None,
            "workouts_completed": 0,
            "workouts_planned": None,
        },
        "weight_logs": [],
        "workout_logs": [],
    }


async def test_progress_returns_the_logs_newest_first_with_a_summary(
    client, auth_headers, profile, plan_day_id
):
    await log_weight(client, auth_headers, 90.0, days_ago(20))
    await log_weight(client, auth_headers, 88.5, days_ago(10))
    await log_weight(client, auth_headers, 87.2)
    await log_workout(client, auth_headers, plan_day_id, days_ago(5))
    await log_workout(client, auth_headers, plan_day_id, days_ago(1))

    body = (await client.get("/progress", headers=auth_headers)).json()

    assert body["summary"] == {
        "window_days": 30,
        "start_weight_kg": 90.0,
        "current_weight_kg": 87.2,
        "weight_change_kg": -2.8,
        "workouts_completed": 2,
        "workouts_planned": 17,  # 4 a week over 30 days
    }
    assert [log["weight_kg"] for log in body["weight_logs"]] == [87.2, 88.5, 90.0]
    assert [log["completed_on"] for log in body["workout_logs"]] == [
        days_ago(1).isoformat(),
        days_ago(5).isoformat(),
    ]


async def test_progress_covers_only_the_requested_days(
    client, auth_headers, plan_day_id
):
    await log_weight(client, auth_headers, 90.0, days_ago(20))
    await log_weight(client, auth_headers, 87.2, days_ago(2))
    await log_workout(client, auth_headers, plan_day_id, days_ago(20))

    body = (await client.get("/progress?days=7", headers=auth_headers)).json()

    assert body["summary"]["window_days"] == 7
    assert [log["weight_kg"] for log in body["weight_logs"]] == [87.2]
    assert body["workout_logs"] == []


@pytest.mark.parametrize("days", [0, 366, "week"])
async def test_progress_rejects_an_invalid_number_of_days(client, auth_headers, days):
    response = await client.get(f"/progress?days={days}", headers=auth_headers)

    assert response.status_code == 422


async def test_each_user_only_sees_their_own_progress(
    client, auth_headers, auth_headers_for
):
    rui = await auth_headers_for("rui@example.com")
    await log_weight(client, auth_headers, 88.0)
    await log_weight(client, rui, 70.0)

    body = (await client.get("/progress", headers=rui)).json()

    assert [log["weight_kg"] for log in body["weight_logs"]] == [70.0]


async def test_progress_requires_a_token(client):
    response = await client.get("/progress")

    assert response.status_code == 401


# --- Regeneration ----------------------------------------------------------
# The fake model's reply is rejected, which is fine here: these tests only
# look at the prompt it was sent.


async def test_a_new_workout_plan_is_told_about_recent_progress(
    client, auth_headers, profile, plan_day_id, fake_llm
):
    await log_weight(client, auth_headers, 90.0, days_ago(14))
    await log_weight(client, auth_headers, 88.0)
    await log_workout(client, auth_headers, plan_day_id, days_ago(3))
    fake_llm.replies = ["not JSON"] * 3

    await client.post("/plans/workout", headers=auth_headers)

    prompt = fake_llm.calls[0]["messages"][1]["content"]
    assert "Recent progress (last 28 days):" in prompt
    assert "- Weight: 90 kg -> 88 kg (-2 kg)" in prompt
    assert "- Workouts completed: 1 of about 16 planned" in prompt


async def test_a_new_diet_plan_is_told_about_recent_progress(
    client, auth_headers, profile, fake_llm
):
    await log_weight(client, auth_headers, 90.0, days_ago(14))
    await log_weight(client, auth_headers, 88.0)
    fake_llm.replies = ["not JSON"] * 3

    await client.post("/plans/diet", headers=auth_headers)

    prompt = fake_llm.calls[0]["messages"][1]["content"]
    assert "- Weight: 90 kg -> 88 kg (-2 kg)" in prompt
    assert "- Workouts completed: 0 of about 16 planned" in prompt


async def test_a_plan_prompt_has_no_progress_section_without_logs(
    client, auth_headers, profile, fake_llm
):
    fake_llm.replies = ["not JSON"] * 3

    await client.post("/plans/workout", headers=auth_headers)

    assert "Recent progress" not in fake_llm.calls[0]["messages"][1]["content"]


async def test_a_new_diet_plan_uses_targets_from_the_latest_weight(
    client, auth_headers, profile, fake_llm
):
    await log_weight(client, auth_headers, 80.0)
    targets = (await client.get("/profile/targets", headers=auth_headers)).json()
    fake_llm.replies = ["not JSON"] * 3

    await client.post("/plans/diet", headers=auth_headers)

    prompt = fake_llm.calls[0]["messages"][1]["content"]
    assert targets["protein_g"] == 160  # 2.0 g per kg of 80 kg, not of 90 kg
    assert f"Daily calorie target: {targets['calories']} kcal" in prompt
    assert "Daily protein target: at least 160 g" in prompt


# --- Database --------------------------------------------------------------


async def test_deleting_a_user_deletes_their_logs(
    client, auth_headers, plan_day_id, db_session
):
    await log_weight(client, auth_headers, 88.0)
    await log_workout(client, auth_headers, plan_day_id)

    user = await db_session.scalar(select(User))
    await db_session.delete(user)
    await db_session.commit()

    assert await count(db_session, WeightLog) == 0
    assert await count(db_session, WorkoutLog) == 0
