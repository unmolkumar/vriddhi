"""Module 2 API. Run from module-2-skill-gap/: uvicorn src.api.main:app --port 8002

Port 8002 (module 1 uses 8001). OpenAPI docs at /docs.
"""
from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import VERSION, error, router
from src.api.routes_v2 import router_v2

PORT = 8002

load_dotenv()
app = FastAPI(
    title="Vriddhi Module 2: Skill Gap & Resume Intelligence",
    version=VERSION,
    description="Resume parsing, evidence-based skill profiles, gap analysis against module 1 targets, "
                "and prerequisite-ordered learning roadmaps.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(router_v2)   # general engine; v1 above is unchanged


@app.exception_handler(RequestValidationError)
async def _invalid(_: Request, exc: RequestValidationError):
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}" for e in exc.errors())
    return error(422, "INVALID_REQUEST", problems or "Invalid request.")


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception):
    return error(500, "INTERNAL_ERROR", "Something went wrong while analysing the request.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.api.main:app", host="0.0.0.0", port=PORT, reload=False)
