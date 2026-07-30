"""
Mantis API worker — the institutional read feed ($299/mo API tier, Phase A).
Run: python -m src.main (from packages/api/)

A FastAPI app served by uvicorn on $PORT, exposed via the Railway public
domain. Reads the durable Postgres `signals` table. Authenticated routes are
gated by API_TIER_ENABLED (see auth.py) — off by default, so this ships inert
and serves only /v1/health until the tier is turned on. See
docs/api_tier_build_plan.md.
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".env"
))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mantis.api")

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from src.config import PORT, api_tier_enabled  # noqa: E402
from src.routes import access, billing, health, signals, webhooks  # noqa: E402

# Browser origins allowed to call the API (the marketing site's access form).
# Scoped, not "*" — comma-separated override via API_ALLOW_ORIGINS.
_ALLOW_ORIGINS = [
    o.strip() for o in os.getenv(
        "API_ALLOW_ORIGINS", "https://mantis.baiq.tech,https://www.mantis.baiq.tech"
    ).split(",") if o.strip()
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mantis Signal API",
        version="1.0.0",
        description=(
            "Institutional feed of on-chain whale/accumulation signals with a "
            "back-tested track record. Authenticate with `Authorization: Bearer <key>`. "
            "Request a key at [mantis.baiq.tech](https://mantis.baiq.tech/#api) "
            "or DM [@mantis2026](https://x.com/mantis2026)."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOW_ORIGINS,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(signals.router)
    app.include_router(webhooks.router)
    app.include_router(billing.router)
    app.include_router(access.router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    log.info("=" * 55)
    log.info("  Mantis API — Institutional Feed (Phase A)")
    log.info("  API_TIER_ENABLED=%s  port=%d", api_tier_enabled(), PORT)
    log.info("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=os.getenv("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
