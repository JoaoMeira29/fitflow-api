import logging

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
