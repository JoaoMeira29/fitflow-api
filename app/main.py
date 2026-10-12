from fastapi import FastAPI

from app.api import auth

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
)

app.include_router(auth.router)


@app.get("/", tags=["Root"])
async def root():
    return {"message": "Welcome to the FitFlow API!"}


@app.get("/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy"}
