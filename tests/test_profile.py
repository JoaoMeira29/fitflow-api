from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.core.age import calculate_age
from app.core.config import settings
from app.models.profile import Profile
from app.models.user import User


def years_ago(years: int) -> date:
    """Today's date `years` years back (28 February if today is 29 February)."""
    today = date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


PROFILE = {
    "goal": "lose_weight",
    "birth_date": "1995-06-15",
    "sex": "female",
    "height_cm": 165.0,
    "weight_kg": 68.5,
    "activity_level": "moderate",
    "training_days_per_week": 4,
    "equipment": ["bodyweight", "dumbbells"],
    "allergies": ["peanuts"],
    "dietary_restrictions": ["vegetarian"],
}


async def put_profile(client, headers, **overrides):
    return await client.put("/profile", json={**PROFILE, **overrides}, headers=headers)


# --- calculate_age ---------------------------------------------------------


def test_age_on_the_birthday():
    assert calculate_age(date(2000, 5, 10), today=date(2018, 5, 10)) == 18


def test_age_the_day_before_the_birthday():
    assert calculate_age(date(2000, 5, 10), today=date(2018, 5, 9)) == 17


def test_age_for_a_leap_day_birth_date():
    assert calculate_age(date(2004, 2, 29), today=date(2022, 2, 28)) == 17
    assert calculate_age(date(2004, 2, 29), today=date(2022, 3, 1)) == 18


# --- PUT /profile ----------------------------------------------------------


async def test_put_creates_the_profile(client, auth_headers):
    response = await put_profile(client, auth_headers)

    assert response.status_code == 200
    assert response.json() == PROFILE


async def test_put_updates_the_existing_profile(client, auth_headers, db_session):
    await put_profile(client, auth_headers)

    response = await put_profile(
        client, auth_headers, weight_kg=66.0, equipment=["barbell"], allergies=[]
    )

    assert response.status_code == 200
    assert response.json()["weight_kg"] == 66.0
    assert response.json()["equipment"] == ["barbell"]
    assert response.json()["allergies"] == []
    assert await db_session.scalar(select(func.count()).select_from(Profile)) == 1


async def test_put_defaults_allergies_and_restrictions_to_empty(client, auth_headers):
    body = {
        key: value
        for key, value in PROFILE.items()
        if key not in ("allergies", "dietary_restrictions")
    }

    response = await client.put("/profile", json=body, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["allergies"] == []
    assert response.json()["dietary_restrictions"] == []


async def test_put_removes_duplicate_choices(client, auth_headers):
    response = await put_profile(
        client, auth_headers, equipment=["dumbbells", "bodyweight", "dumbbells"]
    )

    assert response.json()["equipment"] == ["dumbbells", "bodyweight"]


async def test_put_requires_a_token(client):
    response = await client.put("/profile", json=PROFILE)

    assert response.status_code == 401


async def test_put_accepts_a_user_who_turns_18_today(client, auth_headers):
    response = await put_profile(
        client, auth_headers, birth_date=years_ago(18).isoformat()
    )

    assert response.status_code == 200


async def test_put_rejects_a_user_under_18(client, auth_headers):
    almost_18 = years_ago(18) + timedelta(days=1)

    response = await put_profile(
        client, auth_headers, birth_date=almost_18.isoformat()
    )

    assert response.status_code == 422
    assert "at least 18 years old" in response.json()["detail"][0]["msg"]


async def test_put_rejects_a_birth_date_in_the_future(client, auth_headers):
    tomorrow = date.today() + timedelta(days=1)

    response = await put_profile(
        client, auth_headers, birth_date=tomorrow.isoformat()
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "field, value",
    [
        ("goal", "get_shredded"),
        ("sex", "unknown"),
        ("activity_level", "extreme"),
        ("equipment", ["trampoline"]),
        ("equipment", []),
        ("allergies", ["dust"]),
        ("dietary_restrictions", ["carnivore"]),
        ("height_cm", 119.9),
        ("height_cm", 230.1),
        ("weight_kg", 34.9),
        ("weight_kg", 300.1),
        ("training_days_per_week", 0),
        ("training_days_per_week", 8),
        ("birth_date", "1900-01-01"),
    ],
)
async def test_put_rejects_invalid_values(client, auth_headers, field, value):
    response = await put_profile(client, auth_headers, **{field: value})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:2] == ["body", field]


async def test_put_rejects_a_missing_field(client, auth_headers):
    body = {key: value for key, value in PROFILE.items() if key != "goal"}

    response = await client.put("/profile", json=body, headers=auth_headers)

    assert response.status_code == 422


# --- GET /profile ----------------------------------------------------------


async def test_get_returns_the_saved_profile(client, auth_headers):
    await put_profile(client, auth_headers)

    response = await client.get("/profile", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == PROFILE


async def test_get_returns_404_when_there_is_no_profile(client, auth_headers):
    response = await client.get("/profile", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found"


async def test_get_requires_a_token(client):
    response = await client.get("/profile")

    assert response.status_code == 401


async def test_each_user_only_sees_their_own_profile(client, auth_headers_for):
    ana = await auth_headers_for("ana@example.com")
    rui = await auth_headers_for("rui@example.com")
    await put_profile(client, ana, goal="lose_weight")
    await put_profile(client, rui, goal="gain_muscle")

    assert (await client.get("/profile", headers=ana)).json()["goal"] == "lose_weight"
    assert (await client.get("/profile", headers=rui)).json()["goal"] == "gain_muscle"


# --- GET /profile/targets --------------------------------------------------


async def test_targets_are_computed_from_the_profile(client, auth_headers):
    # A fixed age would drift as time passes, so the birth date is relative.
    await put_profile(
        client,
        auth_headers,
        goal="lose_weight",
        sex="male",
        birth_date=years_ago(28).isoformat(),
        height_cm=170,
        weight_kg=82.5,
        activity_level="moderate",
    )

    response = await client.get("/profile/targets", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "bmr": 1752,
        "tdee": 2716,
        "calories": 2173,
        "protein_g": 165,
        "carbs_g": 243,
        "fat_g": 60,
        "calorie_floor_applied": False,
        "weight_change_cap_applied": False,
    }


async def test_targets_change_when_the_profile_changes(client, auth_headers):
    await put_profile(client, auth_headers, goal="lose_weight")
    losing = (await client.get("/profile/targets", headers=auth_headers)).json()

    await put_profile(client, auth_headers, goal="gain_muscle")
    gaining = (await client.get("/profile/targets", headers=auth_headers)).json()

    assert gaining["calories"] > losing["calories"]
    assert gaining["tdee"] == losing["tdee"]


async def test_targets_use_the_configured_calorie_floor(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "MIN_DAILY_CALORIES_FEMALE", 5000)
    await put_profile(client, auth_headers, sex="female")

    response = await client.get("/profile/targets", headers=auth_headers)

    assert response.json()["calories"] == 5000
    assert response.json()["calorie_floor_applied"] is True


async def test_targets_return_404_when_there_is_no_profile(client, auth_headers):
    response = await client.get("/profile/targets", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found"


async def test_targets_require_a_token(client):
    response = await client.get("/profile/targets")

    assert response.status_code == 401


# --- Database --------------------------------------------------------------


async def test_deleting_a_user_deletes_their_profile(client, auth_headers, db_session):
    await put_profile(client, auth_headers)

    await db_session.execute(delete(User))
    await db_session.commit()

    assert await db_session.scalar(select(func.count()).select_from(Profile)) == 0
