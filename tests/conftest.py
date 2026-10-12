import asyncio

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.llm import LLMResponse, get_llm_client
from app.core.security import create_access_token
from app.main import app
from app.models.user import User

# Tests run against a separate database on the same PostgreSQL server, so
# they never touch development data.
_dev_url = make_url(settings.DATABASE_URL)
TEST_DATABASE_NAME = f"{_dev_url.database}_test"
TEST_DATABASE_URL = _dev_url.set(database=TEST_DATABASE_NAME)

# NullPool opens a fresh connection each time. pytest-asyncio gives every test
# its own event loop, and pooled connections cannot be shared across loops.
test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)


async def _create_test_database() -> None:
    dsn = _dev_url.set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DATABASE_NAME
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DATABASE_NAME}"')
    finally:
        await conn.close()


async def _reset_schema() -> None:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="session", autouse=True)
def prepare_database():
    """Create the test database (first run only) and rebuild its tables."""
    asyncio.run(_create_test_database())
    asyncio.run(_reset_schema())


@pytest.fixture(autouse=True)
async def clean_tables():
    """Empty every table after each test so tests stay independent."""
    yield
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture
async def db_session():
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def auth_headers_for(db_session):
    """Create a user directly in the database and return its auth headers.
    This skips the register and login endpoints, so no Argon2 hashing runs."""

    async def create(email: str) -> dict[str, str]:
        user = User(email=email, password_hash="not-a-real-hash")
        db_session.add(user)
        await db_session.commit()
        return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}

    return create


@pytest.fixture
async def auth_headers(auth_headers_for):
    return await auth_headers_for("ana@example.com")


class FakeLLM:
    """Stands in for LLMClient so tests never call a real model. It returns
    the queued replies in order and records the messages it was sent."""

    model = "fake-model"
    # Set to True in a test to act like a provider that supports tools.
    supports_tools = False

    def __init__(self):
        self.replies: list[str | Exception] = []
        self.calls: list[dict] = []

    async def generate(self, messages, json_schema=None, tools=None) -> LLMResponse:
        self.calls.append(
            {"messages": messages, "json_schema": json_schema, "tools": tools or []}
        )
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(
            content=reply,
            model=self.model,
            input_tokens=10,
            output_tokens=20,
            latency_ms=5,
        )


@pytest.fixture
def fake_llm():
    llm = FakeLLM()
    app.dependency_overrides[get_llm_client] = lambda: llm
    yield llm
    app.dependency_overrides.pop(get_llm_client, None)


@pytest.fixture
async def client():
    """HTTP client that calls the app in-process, using the test database."""

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()
