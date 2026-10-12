async def test_root_returns_welcome_message(client):
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to the FitFlow API!"}


async def test_health_returns_healthy(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
