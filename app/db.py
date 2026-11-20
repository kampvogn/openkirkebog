from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, create_engine, event, text,
)
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    archive: Mapped[str | None] = mapped_column(String(200))
    parish: Mapped[str] = mapped_column(String(160), index=True)
    district: Mapped[str | None] = mapped_column(String(160))
    county: Mapped[str | None] = mapped_column(String(160))
    reference: Mapped[str | None] = mapped_column(String(300))
    start_year: Mapped[int | None]
    end_year: Mapped[int | None]
    category: Mapped[str] = mapped_column(String(40))
    url: Mapped[str | None] = mapped_column(Text)
    identifiers: Mapped[dict] = mapped_column(JSON)
    license: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("source_id", "sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(60))
    width: Mapped[int]
    height: Mapped[int]
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    active_transcription_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcriptions.id", name="fk_page_transcription", use_alter=True)
    )
    active_extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extractions.id", name="fk_page_extraction", use_alter=True)
    )


class Transcription(Base):
    __tablename__ = "transcriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("transcriptions.id"))
    origin: Mapped[str] = mapped_column(String(12))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    transcription_id: Mapped[int] = mapped_column(ForeignKey("transcriptions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[int] = mapped_column(ForeignKey("extractions.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    raw_text: Mapped[str] = mapped_column(Text)
    uncertain_readings: Mapped[list] = mapped_column(JSON)
    bbox: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="unreviewed")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    entry_id: Mapped[int | None] = mapped_column(ForeignKey("entries.id"))
    transcription_id: Mapped[int | None] = mapped_column(ForeignKey("transcriptions.id"))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("uq_jobs_active_page", "page_id", unique=True,
              sqlite_where=text("state IN ('pending', 'processing')"),
              postgresql_where=text("state IN ('pending', 'processing')")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    stage: Mapped[str] = mapped_column(String(12))
    state: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(200))
    transcription_id: Mapped[int | None] = mapped_column(ForeignKey("transcriptions.id"))
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    transcription_id: Mapped[int | None] = mapped_column(ForeignKey("transcriptions.id"))
    extraction_id: Mapped[int | None] = mapped_column(ForeignKey("extractions.id"))
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(200))
    resolved_model: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(15))
    prompt_version: Mapped[str] = mapped_column(String(40))
    pipeline_version: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int]
    status: Mapped[str] = mapped_column(String(12), default="processing")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    finished_at: Mapped[datetime | None]
    runtime_ms: Mapped[int | None]
    usage: Mapped[dict | None] = mapped_column(JSON)
    cost: Mapped[str | None] = mapped_column(String(60))
    currency: Mapped[str | None] = mapped_column(String(10))
    cost_origin: Mapped[str | None] = mapped_column(String(12))
    pricing: Mapped[dict | None] = mapped_column(JSON)
    entries_found: Mapped[int | None]
    entries_reviewed: Mapped[int | None]
    entries_verified: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)


class Database:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(URL.create("sqlite", database=str(directory / "openkirkebog.db")))

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(connection, _):
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode = WAL")
            connection.commit()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    @contextmanager
    def write(self):
        with self.session.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            yield session
