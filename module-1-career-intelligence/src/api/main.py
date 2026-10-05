"""
Module 1 Career Intelligence & Forecasting Engine Application.
FastAPI REST API server.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import router as career_router

app = FastAPI(
    title="Vriddhi Career Intelligence & Forecasting Engine",
    description="Module 1: Historical job analysis, current demand scoring, task-level AI exposure, and 5-year career forecasting.",
    version="1.0.0"
)

# Enable CORS for internal cross-module and integration UI communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(career_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "module": "module-1-career-intelligence",
        "version": "1.0.0"
    }


@app.get("/", include_in_schema=False)
async def root():
    return {
        "engine": "Vriddhi Career Intelligence & Forecasting Engine",
        "module": "module-1-career-intelligence",
        "version": "1.0.0",
        "docs_url": "/docs",
        "health_check": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8001, reload=True)
