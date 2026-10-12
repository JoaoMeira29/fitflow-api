import asyncio
import logging

import pytest
from sqlalchemy.exc import OperationalError

from app.core.database import get_db
from app.main import app, lifespan


async def test_startup_logs_the_docs_paths(caplog):
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        async with lifespan(app):
            pass

    assert "Swagger UI available at /docs" in caplog.messages
    assert "ReDoc available at /redoc" in caplog.messages


async def test_root_returns_welcome_message_and_docs_links(client):
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Welcome to the FitFlow API!",
        "docs": "/docs",
        "redoc": "/redoc",
    }


async def test_health_returns_healthy(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_ready_when_the_database_answers(client):
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


class BrokenSession:
    """A database session whose queries fail in the given way."""

    def __init__(self, failure):
        self.failure = failure

    async def execute(self, statement):
        if isinstance(self.failure, Exception):
            raise self.failure
        await self.failure()


@pytest.fixture
def break_database(client):
    """Make the app's database session fail. `client` is requested first so
    this override replaces the one the client fixture installs."""

    def install(failure):
        async def broken_get_db():
            yield BrokenSession(failure)

        app.dependency_overrides[get_db] = broken_get_db

    return install


@pytest.mark.parametrize(
    "failure",
    [
        OperationalError("SELECT 1", {}, Exception("connection refused")),
        ConnectionRefusedError("connection refused"),
    ],
    ids=["database error", "connection refused"],
)
async def test_not_ready_when_the_database_fails(client, break_database, failure):
    break_database(failure)

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


async def test_not_ready_when_the_database_hangs(client, break_database, monkeypatch):
    monkeypatch.setattr("app.main.READINESS_TIMEOUT_SECONDS", 0.05)
    break_database(lambda: asyncio.sleep(5))

    response = await client.get("/health/ready")

    assert response.status_code == 503


async def test_health_still_answers_when_the_database_fails(client, break_database):
    break_database(ConnectionRefusedError("connection refused"))

    response = await client.get("/health")

    assert response.status_code == 200
