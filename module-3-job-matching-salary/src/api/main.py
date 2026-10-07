"""Module 3 API. Run from module-3-job-matching-salary/: uvicorn src.api.main:app --port 8003

Port 8003 (module 1: 8001, module 2: 8002). OpenAPI docs at /docs.
"""
from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import VERSION, error, router
from src.api.routes_v2 import router_v2

PORT = 8003

load_dotenv()
app = FastAPI(
    title="Vriddhi Module 3: Job Matching & Salary Intelligence",
    version=VERSION,
    description="Live Indian job listings (Adzuna, JSearch, cached snapshot), candidate-job matching, "
                "salary ranges and negotiation guidance.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(router_v2)


@app.exception_handler(RequestValidationError)
async def _invalid(_: Request, exc: RequestValidationError):
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}" for e in exc.errors())
    return error(422, "INVALID_REQUEST", problems or "Invalid request.")


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception):
    return error(500, "INTERNAL_ERROR", "Something went wrong while handling the request.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.main:app", host="0.0.0.0", port=PORT, reload=False)
