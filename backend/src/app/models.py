from datetime import datetime, timezone

from sqlmodel import Field, SQLModel

from app.calendars.colors import FALLBACK_COLOR


class Member(SQLModel, table=True):
    """Unused. Phase 2 concluded that with one calendar per person,
    `CalendarSource` *is* the person, so nothing reads or writes this table and
    no API exposes it.

    The table and the two `member_id` columns are kept rather than dropped
    because removing `calendar_sources.member_id` on SQLite means a
    batch_alter_table rebuild of a table that `events.source_id` references -
    real risk to a family's data, in exchange for tidiness. See "Members, and
    event editing" in CLAUDE.md for what would bring this back.
    """

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
    # When instances were last rebuilt from scratch, as opposed to merely
    # polled. The materialization window rolls forward daily, so a source that
    # simply never changes still has to be re-expanded periodically or the far
    # end of the window slowly empties out.
    last_full_sync_at: datetime | None = None
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
