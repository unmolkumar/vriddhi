"""
Module 1 Career Intelligence & Forecasting Engine Application.
FastAPI REST API server.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import router as career_router, occupations_router, meta_router

app = FastAPI(
    title="Vriddhi Career Intelligence & Forecasting Engine",
    description="Module 1: Historical job analysis, current demand scoring, task-level AI exposure, and 5-year career forecasting.",
    version="2.2.0"
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
app.include_router(occupations_router)
app.include_router(meta_router)


import os
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse

# Mount temporary UI static directory if it exists
ui_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ui")
if os.path.exists(ui_dir):
    app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "module": "module-1-career-intelligence",
        "version": "2.2.0"
    }


@app.get("/", include_in_schema=False)
async def root():
    if os.path.exists(ui_dir):
        return RedirectResponse(url="/ui/")
    return {
        "engine": "Vriddhi Career Intelligence & Forecasting Engine",
        "module": "module-1-career-intelligence",
        "version": "2.2.0",
        "docs_url": "/docs",
        "health_check": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8001, reload=True)
