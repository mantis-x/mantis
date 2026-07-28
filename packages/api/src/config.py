"""Environment-driven config for the api worker."""
from __future__ import annotations

import os


def api_tier_enabled() -> bool:
    """When false, authenticated routes 404 (don't advertise the surface).
    /v1/health stays up regardless. Read live so it can be flipped without a
    code change, same pattern as BYREAL_DRY_RUN / PRO_TIER_PAYMENTS_ENABLED."""
    return os.getenv("API_TIER_ENABLED", "false").lower() == "true"


# Railway injects PORT for the web-facing process; default matches its convention.
PORT = int(os.getenv("PORT", "8080"))

# Hard cap on a single page, regardless of the client's requested limit.
MAX_PAGE_LIMIT = int(os.getenv("API_MAX_PAGE_LIMIT", "100"))
DEFAULT_PAGE_LIMIT = int(os.getenv("API_DEFAULT_PAGE_LIMIT", "25"))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
