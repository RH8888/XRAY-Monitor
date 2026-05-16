from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class TimestampMixin:
    """Database-managed creation and update timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RawLog(TimestampMixin, Base):
    """Unparsed log line captured from an Xray/3x-ui source."""

    __tablename__ = "raw_logs"
    __table_args__ = (
        UniqueConstraint("line_hash", name="uq_raw_logs_line_hash"),
        Index("ix_raw_logs_line_hash", "line_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line: Mapped[str] = mapped_column(Text, nullable=False)
    line_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    events: Mapped[list["Event"]] = relationship(back_populates="raw_log")


class User(TimestampMixin, Base):
    """Known Xray user/client identity."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("client_id", name="uq_users_client_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    events: Mapped[list["Event"]] = relationship(back_populates="user")
    watchlist_entries: Mapped[list["Watchlist"]] = relationship(back_populates="user")


class Event(TimestampMixin, Base):
    """Normalized network event parsed from a raw log."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_timestamp", "timestamp"),
        Index("ix_events_user_id", "user_id"),
        Index("ix_events_domain", "domain"),
        Index("ix_events_ip_address", "ip_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_log_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    inbound_tag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="events")
    raw_log: Mapped[RawLog | None] = relationship(back_populates="events")
    watch_hits: Mapped[list["WatchHit"]] = relationship(back_populates="event")


class Watchlist(TimestampMixin, Base):
    """User/client watch configuration used to flag notable events."""

    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "label", name="uq_watchlists_user_id_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    domain_pattern: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_pattern: Mapped[str | None] = mapped_column(String(45), nullable=True)
    limit_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    user: Mapped[User] = relationship(back_populates="watchlist_entries")
    hits: Mapped[list["WatchHit"]] = relationship(back_populates="watchlist")


class WatchHit(TimestampMixin, Base):
    """Occurrence where an event matched a watchlist entry."""

    __tablename__ = "watch_hits"
    __table_args__ = (Index("ix_watch_hits_hit_at", "hit_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
    )
    hit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    watchlist: Mapped[Watchlist] = relationship(back_populates="hits")
    event: Mapped[Event | None] = relationship(back_populates="watch_hits")
