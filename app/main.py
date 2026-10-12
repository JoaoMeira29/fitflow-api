import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import auth, diets, exercises, foods, profile, progress, workouts
from app.api.deps import DbSession

READINESS_TIMEOUT_SECONDS = 3

# Show INFO logs from the app's own modules, such as the LLM wrapper's line
# with latency and token usage. Uvicorn only configures its own loggers.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")

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
app.include_router(exercises.router)
app.include_router(foods.router)
app.include_router(workouts.router)
app.include_router(diets.router)
app.include_router(progress.router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Welcome to the FitFlow API!",
        "docs": app.docs_url,
        "redoc": app.redoc_url,
    }


@app.get("/health", tags=["Health Check"])
async def health_check():
    """The process is up. It says nothing about the database."""
    return {"status": "healthy"}


@app.get("/health/ready", tags=["Health Check"])
async def readiness_check(db: DbSession):
    """The app can serve requests: the database answers a trivial query.
    Kubernetes uses this to decide whether to send the pod traffic."""
    try:
        # A dead database can leave a connection attempt hanging, so give up
        # after a few seconds instead of keeping the caller waiting.
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            await db.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from None
    return {"status": "ready"}
