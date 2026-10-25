"""Entry point. Run with: uvicorn app.main:app --reload"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import bitcoin
from app.config import get_settings

app = FastAPI(title="Payments Intelligence API", version="0.1.0")

# Allow the React dev server (Milestone 2) to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(bitcoin.router)


@app.get("/api/health")
def health() -> dict:
    """Says the API process is running. It does not check external providers."""
    return {"status": "ok", "service": "payments-intelligence-api", "version": app.version}