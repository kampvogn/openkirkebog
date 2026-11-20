"""Versioned AI cost accounting and aggregate operations metrics."""

import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import AIError, AIResponse
from app.db import Entry, Job, Page, ProcessingRun


MILLION = Decimal(1_000_000)
MIN_VERIFIED_ENTRIES = 5
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Rate = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class PricingRate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    provider: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1)
    effective_from: date = Field(strict=True)
    currency: Currency
    input_per_million: Rate
    output_per_million: Rate
    cached_input_per_million: Rate | None = None


type Pricing = tuple[PricingRate, ...]
type ModelPeriod = tuple[str, str, str]
type CostBucket = tuple[str | None, str | None]


def load_pricing(path: Path | None) -> Pricing:
    if path is None:
        return ()
    try:
        rates = TypeAdapter(Pricing).validate_json(path.read_bytes())
    except (OSError, ValidationError):
        raise ValueError("AI_PRICING_FILE must contain a valid JSON array of pricing rates.") from None
    keys = {(rate.provider, rate.model, rate.effective_from) for rate in rates}
    if len(keys) != len(rates):
        raise ValueError("AI_PRICING_FILE contains duplicate provider, model and effective-date rates.")
    return rates


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _rate_for_run(run: ProcessingRun, pricing: Pricing) -> PricingRate | None:
    if run.started_at is None:
        return None
    day = _utc(run.started_at).date()
    for model in dict.fromkeys((run.resolved_model, run.model)):
        rate = max(
            (
                row for row in pricing
                if row.provider == run.provider and row.model == model and row.effective_from <= day
            ),
            key=lambda row: row.effective_from,
            default=None,
        )
        if rate is not None:
            return rate
    return None


def _tokens(usage: dict | None, category: str) -> int | None:
    value = usage.get(category) if usage else None
    return value if type(value) is int and value >= 0 else None


def _decimal_string(amount: Decimal) -> str:
    return format(amount or Decimal(0), "f")


def account(run: ProcessingRun, response: AIResponse | AIError, pricing: Pricing) -> None:
    """Preserve the telemetry and the applicable rate snapshot for one request."""
    run.usage = dict(response.usage) if response.usage else None
    run.resolved_model = response.resolved_model
    run.cost = run.currency = run.cost_origin = run.pricing = None

    if response.cost is not None and response.cost.is_finite() and response.cost >= 0:
        run.cost = _decimal_string(response.cost)
        if response.currency and re.fullmatch(r"[a-zA-Z]{3}", response.currency):
            run.currency = response.currency.upper()
        run.cost_origin = "reported"
        return

    rate = _rate_for_run(run, pricing)
    input_tokens = _tokens(run.usage, "input_tokens")
    output_tokens = _tokens(run.usage, "output_tokens")
    if rate is None or input_tokens is None or output_tokens is None:
        return

    input_cost = input_tokens * rate.input_per_million
    if rate.cached_input_per_million is not None and rate.cached_input_per_million != rate.input_per_million:
        cached_tokens = _tokens(run.usage, "cached_tokens")
        if cached_tokens is None and input_tokens == 0:
            cached_tokens = 0
        if cached_tokens is None or cached_tokens > input_tokens:
            return
        input_cost -= cached_tokens * (rate.input_per_million - rate.cached_input_per_million)
    estimated_cost = (input_cost + output_tokens * rate.output_per_million) / MILLION
    run.cost = _decimal_string(estimated_cost)
    run.currency = rate.currency
    run.cost_origin = "estimated"
    run.pricing = rate.model_dump(mode="json")


def _successful_extraction(run: ProcessingRun) -> bool:
    return run.stage == "extraction" and run.status == "completed"


def _has_cost(run: ProcessingRun) -> bool:
    return run.cost is not None and bool(run.currency) and run.cost_origin in {"reported", "estimated"}


def _entry_counts(session: Session) -> tuple[Counter, Counter]:
    counts, verified_by_extraction = Counter(), Counter()
    rows = session.execute(
        select(Entry.extraction_id, Entry.status, func.count())
        .join(Page, Page.active_extraction_id == Entry.extraction_id)
        .group_by(Entry.extraction_id, Entry.status)
    )
    for extraction_id, status, count in rows:
        counts[status] += count
        if status == "verified":
            verified_by_extraction[extraction_id] = count
    return counts, verified_by_extraction


def _summary(
    period: ModelPeriod,
    bucket: CostBucket,
    runs: list[ProcessingRun],
    single_cost_basis: bool,
    verified_by_extraction: Counter,
) -> dict:
    successful_pages = {run.page_id for run in runs if _successful_extraction(run)}
    verified_extractions = {
        run.extraction_id for run in runs if _successful_extraction(run) and run.extraction_id is not None
    }
    verified = sum(verified_by_extraction[extraction_id] for extraction_id in verified_extractions)
    input_counts, output_counts = [
        [count for run in runs if (count := _tokens(run.usage, name)) is not None]
        for name in ("input_tokens", "output_tokens")
    ]
    costs = [Decimal(run.cost) for run in runs if _has_cost(run)]
    cost = sum(costs, start=Decimal(0)) if costs else None
    averageable = single_cost_basis and cost is not None and len(costs) == len(runs)
    provider, model, month = period
    currency, origin = bucket
    return {
        "provider": provider,
        "model": model,
        "period": month,
        "currency": currency,
        "cost_origin": origin,
        "runs": len(runs),
        "input_tokens": sum(input_counts) if input_counts else None,
        "output_tokens": sum(output_counts) if output_counts else None,
        "input_tokens_known_runs": len(input_counts),
        "output_tokens_known_runs": len(output_counts),
        "costed_runs": len(costs),
        "cost": _decimal_string(cost) if cost is not None else None,
        "successful_pages": len(successful_pages),
        "verified_entries": verified,
        "average_cost_per_page": (
            _decimal_string(cost / len(successful_pages)) if averageable and successful_pages else None
        ),
        "average_cost_per_verified_entry": (
            _decimal_string(cost / verified) if averageable and verified >= MIN_VERIFIED_ENTRIES else None
        ),
    }


def operations(session: Session) -> tuple[dict, list[dict]]:
    """Summarize completed inference and current review outcomes for a local installation."""
    all_runs = session.scalars(select(ProcessingRun)).all()
    completed = [run for run in all_runs if run.finished_at is not None]
    successful = {run.page_id for run in completed if _successful_extraction(run)}
    timed_runs = [run for run in completed if run.runtime_ms is not None and run.runtime_ms >= 0]
    pages_timed = len({run.page_id for run in timed_runs})
    seconds = sum(run.runtime_ms for run in timed_runs) / 1000 if timed_runs else None
    entry_counts, verified_by_extraction = _entry_counts(session)
    attempts = session.scalars(select(Job.attempts).where(Job.attempts > 1)).all()
    totals = {
        "pages_imported": session.scalar(select(func.count()).select_from(Page)),
        "pages_processed": len({run.page_id for run in completed}),
        "pages_successful": len(successful),
        "entries": sum(entry_counts.values()),
        "entries_reviewed": entry_counts["reviewed"] + entry_counts["verified"],
        "entries_verified": entry_counts["verified"],
        "runs": len(all_runs),
        "failures": sum(run.status == "failed" for run in completed),
        "retries": sum(attempt - 1 for attempt in attempts),
        "timed_runs": len(timed_runs),
        "pages_timed": pages_timed,
        "processing_seconds": seconds,
        "seconds_per_processed_page": round(seconds / pages_timed, 3) if pages_timed else None,
        "pages_per_processing_hour": round(pages_timed * 3600 / seconds, 2) if seconds else None,
        "missing_usage": sum(
            any(_tokens(run.usage, category) is None for category in ("input_tokens", "output_tokens"))
            for run in completed
        ),
        "missing_cost": sum(not _has_cost(run) for run in completed),
    }
    cohorts: dict[ModelPeriod, dict[CostBucket, list[ProcessingRun]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for run in completed:
        period = _utc(run.started_at).strftime("%Y-%m")
        cohort = (run.provider, run.resolved_model or run.model, period)
        cohorts[cohort][(run.currency, run.cost_origin)].append(run)
    groups = []
    for period in sorted(cohorts, key=lambda key: (key[2], key[0], key[1]), reverse=True):
        buckets = cohorts[period]
        pages_by_bucket = {key: {run.page_id for run in runs} for key, runs in buckets.items()}
        bucket_counts = Counter(page_id for pages in pages_by_bucket.values() for page_id in pages)
        for bucket in sorted(buckets, key=lambda key: (key[0] or "", key[1] or "")):
            single_cost_basis = all(bucket_counts[page_id] == 1 for page_id in pages_by_bucket[bucket])
            groups.append(_summary(period, bucket, buckets[bucket], single_cost_basis, verified_by_extraction))
    return totals, groups
