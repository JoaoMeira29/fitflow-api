import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, profile

# Uvicorn's own logger, so the lines below show up with its startup messages.
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The app does not know the host and port it is served on, only the paths.
    logger.info("Swagger UI available at %s", app.docs_url)
    logger.info("ReDoc available at %s", app.redoc_url)
    yield


app = FastAPI(
    title="FitFlow-API",
    description=(
        "FitFlow generates personalised weekly workout plans and daily meal "
        "plans from a user's profile. Calorie and macro targets are computed "
        "in code; an LLM picks exercises and foods from FitFlow's own catalogs, "
        "and every plan is validated before it is stored."
    ),
    version="1.0.0",
    docs_url="/docs",  # SWAGGER UI
    redoc_url="/redoc",  # REDOC UI
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(profile.router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Welcome to the FitFlow API!",
        "docs": app.docs_url,
        "redoc": app.redoc_url,
    }


@app.get("/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy"}
