from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

from app.calendars.colors import FALLBACK_COLOR


class Member(SQLModel, table=True):
    __tablename__ = "members"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    color: str = "#888888"
    avatar: str | None = None
    display_order: int = 0


class CalendarSource(SQLModel, table=True):
    __tablename__ = "calendar_sources"

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # "ics" | "caldav"
    name: str = ""
    color: str = FALLBACK_COLOR  # auto-assigned from the palette, by configured order
    display_order: int = 0
    url: str
    # Google identifies a calendar by address rather than URL. `url` still
    # holds that calendar's API endpoint so the row stays self-describing -
    # and so the column can stay NOT NULL, which on SQLite would otherwise
    # mean a full table rebuild to relax, with events.source_id pointing at it.
    calendar_id: str | None = None
    credentials_ref: str | None = None
    member_id: int | None = Field(default=None, foreign_key="members.id")
    enabled: bool = True
    last_synced_at: datetime | None = None
    # Opaque resume token owned by whichever adapter serves this kind: an
    # HTTP ETag for ICS, a sync-token for CalDAV and Google.
    sync_state: str | None = None


class Event(SQLModel, table=True):
    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="calendar_sources.id")
    uid: str
    raw_vevent: str
    etag: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EventInstance(SQLModel, table=True):
    __tablename__ = "event_instances"

    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="events.id")
    member_id: int | None = Field(default=None, foreign_key="members.id")
    starts_at: datetime = Field(index=True)
    ends_at: datetime
    all_day: bool = False
    title: str
    location: str | None = None


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str
