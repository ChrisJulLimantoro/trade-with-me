from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ats.config import settings
from ats.ingestion.freshness import upsert_heartbeat
from ats.logging import get_logger

log = get_logger(__name__)


async def pull_all(session: AsyncSession) -> None:
    """X/Twitter ingestion — disabled in M1. Enabled in M2 when X_BEARER_TOKEN is set."""
    if not settings.x_bearer_token:
        log.info("x_poller_disabled", reason="X_BEARER_TOKEN not set")
        await upsert_heartbeat(session, "media_x", "disabled", "X_BEARER_TOKEN not set")
        return
    # M2 will implement this path
    raise NotImplementedError("X ingestion requires M2 implementation")
