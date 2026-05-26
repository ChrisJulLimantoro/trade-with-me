from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Candle(Base):
    __tablename__ = "candles"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    timeframe: Mapped[str] = mapped_column(String, primary_key=True)
    open_time: Mapped[datetime] = mapped_column(primary_key=True)
    open: Mapped[float] = mapped_column(Numeric, nullable=False)
    high: Mapped[float] = mapped_column(Numeric, nullable=False)
    low: Mapped[float] = mapped_column(Numeric, nullable=False)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[float] = mapped_column(Numeric, nullable=False)
    quote_volume: Mapped[float] = mapped_column(Numeric, nullable=False)
    taker_buy_vol: Mapped[float] = mapped_column(Numeric, nullable=False)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class FundingRate(Base):
    __tablename__ = "funding_rates"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    funding_time: Mapped[datetime] = mapped_column(primary_key=True)
    rate: Mapped[float] = mapped_column(Numeric, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class OpenInterest(Base):
    __tablename__ = "open_interest"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[datetime] = mapped_column(primary_key=True)
    oi: Mapped[float] = mapped_column(Numeric, nullable=False)
    oi_value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class FundingRateXVenue(Base):
    __tablename__ = "funding_rates_xvenue"
    __table_args__ = (
        Index("idx_xvenue_funding_lookup", "symbol", "funding_time"),
    )

    exchange: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    funding_time: Mapped[datetime] = mapped_column(primary_key=True)
    rate: Mapped[float] = mapped_column(Numeric, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Basis(Base):
    __tablename__ = "basis"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[datetime] = mapped_column(primary_key=True)
    mark_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    index_price: Mapped[float] = mapped_column(Numeric, nullable=False)
    premium_index: Mapped[float] = mapped_column(Numeric, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Liquidation(Base):
    __tablename__ = "liquidations"
    __table_args__ = (Index("idx_liq_symbol_ts", "symbol", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    ts: Mapped[datetime] = mapped_column(nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Numeric, nullable=False)
    qty: Mapped[float] = mapped_column(Numeric, nullable=False)
    notional: Mapped[float] = mapped_column(Numeric, nullable=False)


class MarkPrice1m(Base):
    __tablename__ = "mark_prices_1m"

    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    ts: Mapped[datetime] = mapped_column(primary_key=True)
    mark_open: Mapped[float] = mapped_column(Numeric, nullable=False)
    mark_close: Mapped[float] = mapped_column(Numeric, nullable=False)
    mark_high: Mapped[float] = mapped_column(Numeric, nullable=False)
    mark_low: Mapped[float] = mapped_column(Numeric, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False)


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (Index("idx_media_published", "published_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    account: Mapped[str] = mapped_column(String, nullable=False)
    published_at: Mapped[datetime] = mapped_column(nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    data_class: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
