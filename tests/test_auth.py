import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.login_attempt import LoginAttempt
from app.models.user import User

CREDENTIALS = {"email": "ana@example.com", "password": "correct horse battery"}


async def register(client, **overrides):
    return await client.post("/auth/register", json={**CREDENTIALS, **overrides})


async def login_token(client) -> str:
    response = await client.post("/auth/login", json=CREDENTIALS)
    return response.json()["access_token"]


# --- POST /auth/register ---------------------------------------------------


async def test_register_creates_user(client):
    response = await register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ana@example.com"
    assert uuid.UUID(body["id"])
    assert "created_at" in body


async def test_register_never_returns_the_password(client):
    response = await register(client)

    assert "password" not in response.json()
    assert "password_hash" not in response.json()


async def test_register_stores_a_hash_not_the_password(client, db_session):
    await register(client)

    user = await db_session.scalar(select(User))
    assert user.password_hash != CREDENTIALS["password"]
    assert user.password_hash.startswith("$argon2")


async def test_register_lowercases_the_email(client):
    response = await register(client, email="Ana@Example.COM")

    assert response.json()["email"] == "ana@example.com"


async def test_register_rejects_duplicate_email(client):
    await register(client)

    response = await register(client, email="ANA@example.com")

    assert response.status_code == 409
    assert response.json()["detail"] == "Email already registered"


async def test_register_rejects_invalid_email(client):
    response = await register(client, email="not-an-email")

    assert response.status_code == 422


async def test_register_rejects_short_password(client):
    response = await register(client, password="elevenchars")

    assert response.status_code == 422


async def test_register_accepts_a_password_of_minimum_length(client):
    response = await register(client, password="twelve chars")

    assert response.status_code == 201


async def test_register_rejects_too_long_password(client):
    response = await register(client, password="x" * 41)

    assert response.status_code == 422


async def test_register_accepts_a_password_of_maximum_length(client):
    response = await register(client, password="correct horse " * 2 + "battery here")

    assert response.status_code == 201


async def test_register_rejects_common_password(client):
    response = await register(client, password="Password1234")

    assert response.status_code == 422
    assert "too common" in response.json()["detail"][0]["msg"]


# --- POST /auth/login ------------------------------------------------------


async def test_login_returns_a_bearer_token(client):
    await register(client)

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_is_case_insensitive_on_email(client):
    await register(client)

    response = await client.post(
        "/auth/login", json={**CREDENTIALS, "email": "ANA@EXAMPLE.COM"}
    )

    assert response.status_code == 200


async def test_login_rejects_wrong_password(client):
    await register(client)

    response = await client.post(
        "/auth/login", json={**CREDENTIALS, "password": "wrong password"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


async def test_login_gives_the_same_error_for_unknown_email(client):
    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


# --- Login rate limit ------------------------------------------------------

WRONG = {**CREDENTIALS, "password": "definitely the wrong one"}


async def fail_login(client, times: int, email: str = CREDENTIALS["email"]):
    for _ in range(times):
        response = await client.post("/auth/login", json={**WRONG, "email": email})
        assert response.status_code == 401


async def test_login_is_refused_after_five_wrong_passwords(client):
    await register(client)
    await fail_login(client, 5)

    response = await client.post("/auth/login", json=WRONG)

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "Too many failed login attempts. Try again later."
    )
    # About 15 minutes, in seconds.
    assert 890 <= int(response.headers["Retry-After"]) <= 900


async def test_the_right_password_is_refused_too_while_locked(client):
    await register(client)
    await fail_login(client, 5)

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 429


async def test_four_wrong_passwords_do_not_lock_the_account(client):
    await register(client)
    await fail_login(client, 4)

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 200


async def test_a_successful_login_resets_the_count(client, db_session):
    await register(client)
    await fail_login(client, 4)
    await client.post("/auth/login", json=CREDENTIALS)

    # Four more failures would lock the account if the first four still counted.
    await fail_login(client, 4)
    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(LoginAttempt)) == 0


async def test_failures_older_than_the_window_do_not_count(client, db_session):
    await register(client)
    long_ago = datetime.now(UTC) - timedelta(minutes=16)
    db_session.add_all(
        LoginAttempt(email=CREDENTIALS["email"], attempted_at=long_ago)
        for _ in range(5)
    )
    await db_session.commit()

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 200


async def test_the_limit_is_per_email(client):
    await register(client)
    await fail_login(client, 5, email="someone-else@example.com")

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 200


async def test_an_unknown_email_is_limited_the_same_way(client):
    await fail_login(client, 5, email="nobody@example.com")

    response = await client.post(
        "/auth/login", json={**WRONG, "email": "nobody@example.com"}
    )

    assert response.status_code == 429


async def test_the_limit_ignores_the_case_of_the_email(client):
    await register(client)
    await fail_login(client, 5, email="ANA@example.com")

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 429


async def test_the_limit_comes_from_the_settings(client, monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 2)
    await register(client)
    await fail_login(client, 2)

    response = await client.post("/auth/login", json=CREDENTIALS)

    assert response.status_code == 429


# --- GET /auth/me ----------------------------------------------------------


async def test_me_returns_the_logged_in_user(client):
    await register(client)
    token = await login_token(client)

    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["email"] == "ana@example.com"


async def test_me_requires_a_token(client):
    response = await client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_me_rejects_a_malformed_token(client):
    response = await client.get(
        "/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert response.status_code == 401


async def test_me_rejects_a_token_for_a_deleted_user(client):
    token = create_access_token(str(uuid.uuid4()))

    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


async def test_me_rejects_a_token_whose_subject_is_not_a_uuid(client):
    token = create_access_token("not-a-uuid")

    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
