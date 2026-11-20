import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.ai import AIError, AIResponse, TranscriptionResult
from app.db import Database, Entry, Extraction, Job, Page, ProcessingRun, Source, Transcription
from app.metrics import account, load_pricing, operations


def pricing_row(**overrides):
    return {
        "provider": "test-provider",
        "model": "test-model",
        "version": "fixture-1",
        "effective_from": "2025-01-01",
        "currency": "DKK",
        "input_per_million": "2",
        "output_per_million": "8",
        **overrides,
    }


def pricing_file(tmp_path, rows):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_estimates_from_effective_snapshot_for_resolved_model_on_failed_request(tmp_path):
    pricing = load_pricing(pricing_file(tmp_path, [
        pricing_row(),
        pricing_row(model="test-model-revision", cached_input_per_million="1"),
        pricing_row(
            model="test-model-revision", version="fixture-2",
            effective_from="2025-03-01", input_per_million="100",
        ),
    ]))
    run = ProcessingRun(
        provider="test-provider", model="test-model",
        started_at=datetime(2025, 3, 1, 0, 20, tzinfo=timezone(timedelta(hours=1))),
    )
    account(run, AIError(
        "request failed", resolved_model="test-model-revision",
        usage={"input_tokens": 1_000_000, "cached_tokens": 250_000, "output_tokens": 500_000},
    ), pricing)

    assert Decimal(run.cost) == Decimal("5.75")
    assert run.currency == "DKK"
    assert run.cost_origin == "estimated"
    assert run.pricing["version"] == "fixture-1"
    assert run.pricing["effective_from"] == "2025-01-01"
    assert run.pricing["cached_input_per_million"] == "1"
    assert run.resolved_model == "test-model-revision"
    assert run.usage["cached_tokens"] == 250_000


def test_reported_cost_precedes_pricing_and_alias_is_fallback(tmp_path):
    pricing = load_pricing(pricing_file(tmp_path, [pricing_row()]))
    run = ProcessingRun(
        provider="test-provider", model="test-model", started_at=datetime(2025, 2, 1),
    )
    result = TranscriptionResult(rows=[])
    account(run, AIResponse(
        result, cost=Decimal(0), currency="usd", resolved_model="unpriced-revision",
    ), pricing)
    assert run.usage is None
    assert (run.cost, run.currency, run.cost_origin, run.pricing) == ("0", "USD", "reported", None)

    account(run, AIResponse(
        result, usage={"input_tokens": 1_000_000, "output_tokens": 0},
        resolved_model="unpriced-revision",
    ), pricing)
    assert (run.cost, run.currency, run.cost_origin) == ("2", "DKK", "estimated")
    assert run.pricing["model"] == "test-model"
    assert run.resolved_model == "unpriced-revision"


@pytest.mark.parametrize("usage", [
    None,
    {"input_tokens": 100},
    {"input_tokens": 100, "output_tokens": 5},
    {"input_tokens": 100, "output_tokens": 5, "cached_tokens": 101},
])
def test_unknown_usage_leaves_discounted_cost_unknown(tmp_path, usage):
    pricing = load_pricing(pricing_file(tmp_path, [pricing_row(cached_input_per_million="1")]))
    run = ProcessingRun(
        provider="test-provider", model="test-model", started_at=datetime(2025, 2, 1),
    )
    account(run, AIResponse(TranscriptionResult(rows=[]), usage=usage), pricing)
    assert (run.cost, run.currency, run.cost_origin, run.pricing) == (None, None, None, None)
    assert run.usage == usage


@pytest.mark.parametrize("rows", [
    [pricing_row(), pricing_row(version="fixture-2")],
    [pricing_row(effective_from="2025-02-29")],
    [pricing_row(effective_from="20250201")],
    [pricing_row(input_per_million="-2")],
    [pricing_row(output_per_million="Infinity")],
    [pricing_row(currency="USDollars")],
    {"pricing": []},
])
def test_invalid_pricing_files_are_rejected(tmp_path, rows):
    with pytest.raises(ValueError, match="AI_PRICING_FILE"):
        load_pricing(pricing_file(tmp_path, rows))


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "db")
    database.migrate()
    yield database
    database.engine.dispose()


def test_empty_operations(database):
    with database.session() as session:
        totals, groups = operations(session)
    assert totals["runs"] == totals["pages_imported"] == totals["entries"] == 0
    assert totals["processing_seconds"] is None
    assert totals["seconds_per_processed_page"] is None
    assert totals["pages_per_processing_hour"] is None
    assert groups == []


def add_page(session):
    source = Source(
        archive="Test", parish="Test", reference="Test", start_year=1850, end_year=1899,
        category="birth", url="https://example.test", identifiers={},
    )
    session.add(source)
    session.flush()
    page = Page(source_id=source.id, filename="page.png", sha256="a" * 64,
                media_type="image/png", width=100, height=100)
    session.add(page)
    session.flush()
    return page


def add_extraction(session, page, statuses):
    transcription = Transcription(page_id=page.id, origin="ai", data={"rows": []})
    session.add(transcription)
    session.flush()
    extraction = Extraction(page_id=page.id, transcription_id=transcription.id)
    session.add(extraction)
    session.flush()
    session.add_all([
        Entry(extraction_id=extraction.id, data={}, raw_text="…", uncertain_readings=[], status=status)
        for status in statuses
    ])
    return extraction


def add_run(session, job, page, **overrides):
    record = {
        "job_id": job.id, "page_id": page.id, "source_id": page.source_id,
        "provider": "test-provider", "model": "test-model",
        "stage": "transcription", "status": "completed", "attempt": 1,
        "prompt_version": "1", "pipeline_version": "1",
        "started_at": datetime(2025, 2, 1, tzinfo=UTC),
        "finished_at": datetime(2025, 2, 1, 0, 0, 1, tzinfo=UTC),
        "runtime_ms": 1000, "usage": {"input_tokens": 100, "output_tokens": 0},
        "cost": "0.01", "currency": "DKK", "cost_origin": "estimated",
    }
    session.add(ProcessingRun(**{**record, **overrides}))


def test_aggregates_include_failed_runs_and_count_current_review_and_job_retry_once(database):
    with database.session.begin() as session:
        page = add_page(session)
        add_page(session)
        old = add_extraction(session, page, ["verified"])
        active = add_extraction(session, page, ["verified"] * 5 + ["reviewed", "unreviewed"])
        page.active_extraction_id = active.id
        job = Job(page_id=page.id, stage="pipeline", state="completed", attempts=2,
                  provider="test-provider", model="test-model")
        session.add(job)
        session.flush()
        add_run(session, job, page, status="failed")
        add_run(session, job, page, attempt=2)
        add_run(session, job, page, attempt=2, stage="extraction", extraction_id=active.id)
        add_run(session, job, page, stage="extraction", extraction_id=old.id)
        add_run(session, job, page, status="processing", finished_at=None, runtime_ms=None, usage=None, cost=None)

    with database.session() as session:
        totals, groups = operations(session)
    assert totals == {
        "pages_imported": 2, "pages_processed": 1, "pages_successful": 1, "entries": 7,
        "entries_reviewed": 6, "entries_verified": 5, "runs": 5, "failures": 1,
        "retries": 1, "timed_runs": 4, "pages_timed": 1, "processing_seconds": 4.0,
        "seconds_per_processed_page": 4.0, "pages_per_processing_hour": 900.0,
        "missing_usage": 0, "missing_cost": 0,
    }
    assert len(groups) == 1
    group = groups[0]
    assert group["period"] == "2025-02"
    assert group["runs"] == 4
    assert group["input_tokens"] == 400
    assert group["output_tokens"] == 0
    assert group["cost"] == "0.04"
    assert group["successful_pages"] == 1
    assert group["verified_entries"] == 5
    assert group["average_cost_per_page"] == "0.04"
    assert group["average_cost_per_verified_entry"] == "0.008"


def test_mixed_cost_and_unknown_usage_do_not_create_complete_averages(database):
    with database.session.begin() as session:
        page = add_page(session)
        job = Job(page_id=page.id, stage="pipeline", provider="test-provider", model="test-model")
        session.add(job)
        session.flush()
        add_run(session, job, page, stage="extraction", cost_origin="reported")
        add_run(session, job, page, cost="0.02")
        add_run(session, job, page, status="failed", usage={"input_tokens": 23},
                cost=None, currency=None, cost_origin=None, runtime_ms=None)
    with database.session() as session:
        totals, groups = operations(session)
    assert totals["missing_cost"] == totals["missing_usage"] == 1
    by_origin = {group["cost_origin"]: group for group in groups}
    assert by_origin["reported"]["cost"] == "0.01"
    assert by_origin["reported"]["average_cost_per_page"] is None
    assert by_origin["estimated"]["cost"] == "0.02"
    assert by_origin[None]["cost"] is None
    assert by_origin[None]["input_tokens"] == 23
    assert by_origin[None]["output_tokens"] is None
    assert by_origin[None]["output_tokens_known_runs"] == 0
