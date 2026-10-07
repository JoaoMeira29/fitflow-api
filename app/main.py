from fastapi import FastAPI

app = FastAPI(
    title="FitFlow-API",
    description="FitFlow is a fitness tracking application that allows users to monitor their workouts, nutrition, and overall health. The API provides endpoints for managing user profiles, logging exercises, tracking nutrition intake, and generating progress reports.",
    version="1.0.0",
    docs_url="/docs",  # SWAGGER UI
    redoc_url="/redoc",  # REDOC UI
)


@app.get("/", tags=["Root"])
async def root():
    return {"message": "Welcome to the FitFlow API!"}


@app.get("/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy"}
