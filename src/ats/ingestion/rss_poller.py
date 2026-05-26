from __future__ import annotations

import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import feedparser
import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ats.ingestion.freshness import upsert_heartbeat
from ats.logging import get_logger

log = get_logger(__name__)

_SEEDS_DIR = Path(__file__).parents[3] / "seeds"
_MAX_BODY_BYTES = 10_240  # 10 KB


def _load_feeds() -> list[dict[str, str]]:
    path = _SEEDS_DIR / "rss_feeds.yaml"
    data: dict[str, list[dict[str, str]]] = yaml.safe_load(path.read_text())
    return data["feeds"]


def _parse_published(entry: dict[str, Any]) -> datetime:
    for field in ("published", "updated"):
        raw = entry.get(field)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(UTC)
            except Exception:
                pass
    return datetime.now(tz=UTC)


def _entry_id(entry: dict[str, Any]) -> str:
    return entry.get("id") or entry.get("link") or str(uuid.uuid4())


async def pull_all(session: AsyncSession, feeds: list[dict[str, str]] | None = None) -> int:
    if feeds is None:
        feeds = _load_feeds()
    total = 0
    for feed_cfg in feeds:
        url = feed_cfg["url"]
        account = feed_cfg.get("account", url)
        try:
            parsed = feedparser.parse(url)
            count = 0
            for entry in parsed.entries:
                source_id = _entry_id(entry)
                raw_body = entry.get("summary") or entry.get("content", [{}])[0].get("value") or ""
                body = raw_body.strip()
                body = body[:_MAX_BODY_BYTES]
                title = (entry.get("title") or "").strip()[:500]
                link = entry.get("link")
                published = _parse_published(entry)
                item_id = uuid.uuid5(uuid.NAMESPACE_URL, f"rss:{source_id}")

                await session.execute(
                    text("""
                        INSERT INTO media_items
                          (id, source, source_id, account, published_at, url, title, body)
                        VALUES
                          (:id, 'rss', :source_id, :account, :published_at, :url, :title, :body)
                        ON CONFLICT (source, source_id) DO NOTHING
                    """),
                    {
                        "id": item_id,
                        "source_id": source_id,
                        "account": account,
                        "published_at": published,
                        "url": link,
                        "title": title,
                        "body": body,
                    },
                )
                count += 1
            await session.commit()
            total += count
            log.info("rss_pulled", account=account, entries=count)
        except Exception as exc:
            log.warning("rss_failed", url=url, error=str(exc))

    await upsert_heartbeat(session, "media_rss", "ok")
    return total
