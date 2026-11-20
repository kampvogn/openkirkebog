from collections.abc import Callable
from contextlib import asynccontextmanager
from copy import deepcopy
import fcntl
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.ai import AIProvider, ExtractedEntry, OpenAIProvider, TranscriptionResult
from app.archive import (
    CATEGORIES, MAX_IMAGE_BYTES, SourceInput, image_path, import_image, parse_identifiers, validation_message,
)
from app.config import Settings
from app.db import Correction, Database, Entry, Extraction, Job, Page, ProcessingRun, Source, Transcription
from app.jobs import Queue
from app.metrics import load_pricing, operations
from app.search import csv_text, export_record, fold, index_entry, search_entries


APP_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=APP_ROOT / "templates")
EXPORT_FIELDS = [
    "source_id", "archive", "parish", "district", "county", "reference", "source_url",
    "source_identifiers", "source_license", "page_id", "page_filename", "page_sha256",
    "entry_id", "entry_url", "extraction_id", "revision", "status", "row_index", "entry_number",
    "event_type", "event_date", "event_date_original", "date_uncertain", "persons",
    "relationships", "attributes", "raw_text", "uncertain_readings", "bbox",
]


def redirect(path, notice=None):
    if notice:
        path += ("&" if "?" in path else "?") + urlencode({"notice": notice})
    return RedirectResponse(path, status_code=303)


def render(request, name, status_code=200, **context):
    context.setdefault("notice", request.query_params.get("notice"))
    return TEMPLATES.TemplateResponse(request=request, name=f"{name}.html", context=context, status_code=status_code)


def require(session, model, key):
    result = session.get(model, key)
    if result is None:
        raise HTTPException(404, "Posten findes ikke.")
    return result


def validate_source(values):
    try:
        return SourceInput.model_validate(values)
    except ValidationError as error:
        raise HTTPException(422, validation_message(error)) from error


async def source_form(request):
    async with request.form() as form:
        values = {
            key: str(form[key]) if key == "url" else str(form[key]).strip()
            for key in SourceInput.model_fields if key in form
        }
    return {key: value for key, value in values.items() if value}


def source_context(database, source_id):
    with database.session() as session:
        source = require(session, Source, source_id)
        pages = list(session.scalars(select(Page).where(Page.source_id == source.id).order_by(Page.id)))
        jobs = session.scalars(select(Job).join(Page).where(Page.source_id == source.id).order_by(Job.id))
        return {"source": source, "pages": pages, "job_by_page": {job.page_id: job for job in jobs},
                "categories": CATEGORIES}


def uncertainty(value):
    return [line.strip() for line in value.splitlines() if line.strip()]


def entry_snapshot(entry):
    return {field: getattr(entry, field) for field in (
        "data", "raw_text", "uncertain_readings", "status", "excluded", "revision"
    )}


def export_response(format_name, name, data, csv_rows, fields):
    headers = {"Content-Disposition": f'attachment; filename="{name}.{format_name}"'}
    if format_name == "json":
        return JSONResponse(data, headers=headers)
    if format_name == "csv":
        return Response(csv_text(csv_rows, fields), media_type="text/csv", headers=headers)
    raise HTTPException(404, "Ukendt eksportformat.")


def create_app(
    settings: Settings,
    *,
    provider_factory: Callable[[str], AIProvider] | None = None,
    start_workers: bool = True,
):
    database = Database(settings.directory)
    if provider_factory is None and settings.api_key:
        def provider_factory(model):
            return OpenAIProvider(settings.api_key, model)

    queue = Queue(database, provider_factory, load_pricing(settings.pricing_file))

    @asynccontextmanager
    async def lifespan(_app):
        with (settings.directory / "server.lock").open("a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Another OpenKirkebog server is using this data directory.") from error
            database.migrate()
            queue.start(settings.workers if start_workers else 0)
            try:
                yield
            finally:
                await run_in_threadpool(queue.stop)
                database.engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.database = database
    app.state.queue = queue
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")

    @app.middleware("http")
    async def protect_local_data(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            local = urlsplit(f"http://{host}").hostname in {"localhost", "127.0.0.1", "::1"}
        except ValueError:
            local = False
        if not local:
            return PlainTextResponse("Only localhost hosts are accepted.", status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if ((origin is not None and origin.lower() != str(request.base_url).rstrip("/").lower())
                    or request.headers.get("sec-fetch-site") == "cross-site"):
                return PlainTextResponse("Cross-origin writes are disabled.", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(HTTPException)
    async def display_error(request, error):
        return render(request, "base", status_code=error.status_code, error=error.detail)

    @app.get("/")
    def dashboard(request: Request):
        with database.session() as session:
            active = select(func.count(Entry.id)).join(Page, Page.active_extraction_id == Entry.extraction_id)
            counts = {
                "sources": session.scalar(select(func.count(Source.id))),
                "pages": session.scalar(select(func.count(Page.id))),
                "processed": session.scalar(select(func.count(Page.active_extraction_id))),
                "entries": session.scalar(active),
                "awaiting": session.scalar(active.where(Entry.status == "unreviewed")),
                "errors": session.scalar(select(func.count(Job.id)).where(Job.state == "failed")),
            }
        return render(request, "dashboard", counts=counts)

    @app.get("/sources")
    def sources(request: Request, q: str = "", year: str = "", category: str = ""):
        with database.session() as session:
            statement = select(Source).order_by(Source.parish, Source.start_year, Source.id)
            if year.isdigit() and len(year) <= 4:
                statement = statement.where(Source.start_year <= int(year), Source.end_year >= int(year))
            if category in CATEGORIES:
                statement = statement.where(Source.category == category)
            books = [source for source in session.scalars(statement) if fold(q.strip()) in fold(source.parish)]
        return render(request, "sources", sources=books, categories=CATEGORIES, q=q, year=year)

    @app.post("/sources")
    async def create_source(request: Request):
        data = validate_source(await source_form(request))

        def save():
            with database.write() as session:
                source = Source(**data.model_dump(), identifiers=parse_identifiers(data.url) if data.url else {})
                session.add(source)
                session.flush()
                return source.id

        source_id = await run_in_threadpool(save)
        return redirect(f"/sources/{source_id}")

    @app.get("/sources/{source_id}")
    def source_detail(request: Request, source_id: int):
        return render(request, "source", **source_context(database, source_id))

    @app.post("/sources/{source_id}/complete")
    async def complete_source(request: Request, source_id: int):
        values = await source_form(request)

        def save():
            with database.write() as session:
                source = require(session, Source, source_id)
                data = {key: getattr(source, key) for key in SourceInput.model_fields}
                for key, value in values.items():
                    if data[key] is None or (key == "category" and data[key] == "unknown"):
                        data[key] = value
                details = validate_source(data)
                category_changed = details.category != source.category
                for key, value in details.model_dump().items():
                    setattr(source, key, value)
                if source.url and not source.identifiers:
                    source.identifiers = parse_identifiers(source.url)
                if category_changed:
                    entries = session.scalars(select(Entry).join(Extraction).join(
                        Page, Page.id == Extraction.page_id
                    ).where(Page.source_id == source.id))
                    for entry in entries:
                        index_entry(session, entry, source)

        await run_in_threadpool(save)
        return redirect(f"/sources/{source_id}", "Kildeoplysningerne er gemt.")

    @app.post("/sources/{source_id}/pages")
    async def upload(request: Request, source_id: int):
        def check_source():
            with database.session() as session:
                require(session, Source, source_id)

        await run_in_threadpool(check_source)
        imported, duplicates, errors = 0, 0, []
        async with request.form(max_files=50) as form:
            files = [item for item in form.getlist("files") if isinstance(item, UploadFile) and item.filename]
            if not files:
                raise HTTPException(422, "Vælg mindst én billedfil.")
            for file in files:
                filename = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
                try:
                    contents = await file.read(MAX_IMAGE_BYTES + 1)
                    if await run_in_threadpool(import_image, database, source_id, filename, contents):
                        imported += 1
                    else:
                        duplicates += 1
                except ValueError as error:
                    errors.append(f"{filename}: {error}")
        notice = f"Importeret: {imported}. Dubletter: {duplicates}."
        if errors:
            context = await run_in_threadpool(source_context, database, source_id)
            return render(request, "source", status_code=422, **context, notice=notice, error=" ".join(errors))
        return redirect(f"/sources/{source_id}", notice)

    @app.get("/pages/{page_id}")
    def page_view(request: Request, page_id: int, transcription_id: int | None = None, extraction_id: int | None = None):
        with database.session() as session:
            page = require(session, Page, page_id)
            source = require(session, Source, page.source_id)
            versions = list(session.scalars(
                select(Transcription).where(Transcription.page_id == page.id).order_by(Transcription.id.desc())
            ))
            extractions = list(session.scalars(
                select(Extraction).where(Extraction.page_id == page.id).order_by(Extraction.id.desc())
            ))
            selected_transcription = transcription_id or page.active_transcription_id
            selected_extraction = extraction_id or page.active_extraction_id
            transcription = next((version for version in versions if version.id == selected_transcription), None)
            extraction = next((version for version in extractions if version.id == selected_extraction), None)
            if (transcription_id is not None and transcription is None) or (extraction_id is not None and extraction is None):
                raise HTTPException(404, "Denne version findes ikke på siden.")
            entries = list(session.scalars(select(Entry).where(Entry.extraction_id == selected_extraction)
                                           .order_by(Entry.id))) if extraction else []
            jobs = list(session.scalars(select(Job).where(Job.page_id == page.id).order_by(Job.id.desc())))
            runs = list(session.scalars(select(ProcessingRun).where(ProcessingRun.page_id == page.id)
                                         .order_by(ProcessingRun.id.desc())))
            corrections = list(session.scalars(select(Correction).where(Correction.page_id == page.id)
                                                .order_by(Correction.id.desc())))
        return render(
            request, "page", page=page, source=source, entries=entries, jobs=jobs, runs=runs, corrections=corrections,
            transcription=transcription.data if transcription else None, transcription_id=page.active_transcription_id,
            selected_transcription_id=selected_transcription, version_options=versions,
            can_extract=transcription is not None, extractions=extractions, selected_extraction_id=selected_extraction,
            active_extraction_id=page.active_extraction_id,
            extraction_transcription_id=extraction.transcription_id if extraction else None,
        )

    @app.get("/pages/{page_id}/{kind}")
    def serve_image(page_id: int, kind: str):
        kinds = {"image": "view.jpg", "thumbnail": "thumb.jpg", "original": "original"}
        if kind not in kinds:
            raise HTTPException(404, "Billedet findes ikke.")
        with database.session() as session:
            page = require(session, Page, page_id)
        path = image_path(settings.directory, page, kinds[kind])
        if not path.is_file():
            raise HTTPException(404, "Billedfilen mangler.")
        if kind == "original":
            return FileResponse(path, media_type=page.media_type, filename=page.filename)
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/pages/{page_id}/transcription")
    def correct_transcription(
        page_id: int, transcription_id: int = Form(), raw_text: list[str] = Form(),
        uncertain_readings: list[str] = Form(),
    ):
        with database.write() as session:
            page = require(session, Page, page_id)
            if page.active_transcription_id != transcription_id:
                raise HTTPException(409, "Transskriptionen har ændret sig. Åbn siden igen.")
            original = require(session, Transcription, transcription_id)
            after = deepcopy(original.data)
            rows = after["rows"]
            if len(rows) != len(raw_text) or len(rows) != len(uncertain_readings):
                raise HTTPException(422, "Redigeringen skal omfatte de eksisterende rækker.")
            for row, wording, doubts in zip(rows, raw_text, uncertain_readings, strict=True):
                if wording != row["raw_text"]:
                    row["cells"] = {}
                row["raw_text"] = wording
                row["uncertain_readings"] = uncertainty(doubts)
            try:
                TranscriptionResult.model_validate(after)
            except ValidationError as error:
                raise HTTPException(422, validation_message(error)) from error
            if after != original.data:
                version = Transcription(page_id=page.id, parent_id=original.id, origin="human", data=after)
                session.add(version)
                session.flush()
                page.active_transcription_id = version.id
                session.add(Correction(page_id=page.id, transcription_id=version.id, before=original.data, after=after))
        return redirect(f"/pages/{page_id}", "Transskriptionen er gemt.")

    @app.post("/pages/{page_id}/extraction")
    def activate_extraction(page_id: int, extraction_id: int = Form()):
        with database.write() as session:
            page = require(session, Page, page_id)
            extraction = require(session, Extraction, extraction_id)
            if extraction.page_id != page.id:
                raise HTTPException(404, "Udtrækket tilhører en anden side.")
            prior = page.active_extraction_id
            if prior != extraction.id:
                page.active_extraction_id = extraction.id
                session.add(Correction(page_id=page.id, before={"active_extraction_id": prior},
                                       after={"active_extraction_id": extraction.id}))
        return redirect(f"/pages/{page_id}", "Det valgte udtræk er aktivt i søgning.")

    @app.post("/entries/{entry_id}/review")
    def correct_entry(
        entry_id: int, revision: int = Form(), raw_text: str = Form(default=""),
        uncertain_readings: str = Form(default=""), event_date: str = Form(default=""),
        event_date_original: str | None = Form(default=None), date_uncertain: bool = Form(default=False),
        person_original: list[str] = Form(default=[]), person_normalized: list[str] = Form(default=[]),
        person_uncertain: list[int] = Form(default=[]), status: str = Form(), excluded: bool = Form(default=False),
    ):
        if status not in {"unreviewed", "reviewed", "verified"}:
            raise HTTPException(422, "Vælg en gyldig gennemgangsstatus.")
        if not raw_text.strip():
            raise HTTPException(422, "Transskriptionen må ikke være tom.")
        with database.write() as session:
            entry = require(session, Entry, entry_id)
            if entry.revision != revision:
                raise HTTPException(409, "Indførslen har ændret sig. Åbn siden igen.")
            extraction = require(session, Extraction, entry.extraction_id)
            page = require(session, Page, extraction.page_id)
            source = require(session, Source, page.source_id)
            before = entry_snapshot(entry)
            data = deepcopy(entry.data)
            people = data["persons"]
            if len(people) != len(person_original) or len(people) != len(person_normalized):
                raise HTTPException(422, "Redigeringen skal omfatte de eksisterende personer.")
            for index, person in enumerate(people):
                person["name_original"] = person_original[index].strip() or None
                person["name_normalized"] = person_normalized[index].strip() or None
                person["name_uncertain"] = index in person_uncertain
            data["event_date"] = event_date.strip() or None
            if event_date_original is not None:
                data["event_date_original"] = event_date_original.strip() or None
            data["date_uncertain"] = date_uncertain
            try:
                data = ExtractedEntry.model_validate(data).model_dump(mode="json")
            except ValidationError as error:
                raise HTTPException(422, validation_message(error)) from error
            entry.data = data
            entry.raw_text = raw_text
            entry.uncertain_readings = uncertainty(uncertain_readings)
            entry.status = status
            entry.excluded = excluded
            if entry_snapshot(entry) != before:
                entry.revision += 1
                session.add(Correction(page_id=page.id, entry_id=entry.id,
                                       before=before, after=entry_snapshot(entry)))
                index_entry(session, entry, source)
                run = session.scalar(select(ProcessingRun).where(ProcessingRun.extraction_id == extraction.id))
                if run is not None:
                    run.entries_reviewed = session.scalar(select(func.count(Entry.id)).where(
                        Entry.extraction_id == extraction.id, Entry.status.in_(("reviewed", "verified"))
                    ))
                    run.entries_verified = session.scalar(select(func.count(Entry.id)).where(
                        Entry.extraction_id == extraction.id, Entry.status == "verified"
                    ))
            link = f"/pages/{page.id}" if page.active_extraction_id == extraction.id else (
                f"/pages/{page.id}?extraction_id={extraction.id}"
            )
        return redirect(f"{link}#entry-{entry_id}")

    @app.post("/jobs")
    def enqueue(
        page_ids: list[int] = Form(), stage: str = Form(default="pipeline"), model: str = Form(default=""),
        transcription_id: int | None = Form(default=None),
    ):
        try:
            count = queue.enqueue(page_ids, stage, model.strip() or settings.model,
                                  transcription_id if stage == "extraction" else None)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        with database.session() as session:
            page = require(session, Page, page_ids[0])
        path = f"/pages/{page.id}" if len(page_ids) == 1 else f"/sources/{page.source_id}"
        return redirect(path, f"Tilføjet til køen: {count}.")

    @app.post("/jobs/{job_id}/{action}")
    def change_job(job_id: int, action: str):
        if action not in {"retry", "cancel"}:
            raise HTTPException(404, "Ukendt handling.")
        try:
            page_id = queue.change_state(job_id, "pending" if action == "retry" else "cancelled")
        except LookupError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return redirect(f"/pages/{page_id}")

    @app.get("/search")
    def search(request: Request, q: str = ""):
        with database.session() as session:
            results = search_entries(session, q) if q.strip() else []
        return render(request, "search", q=q, results=results)

    @app.get("/export/{format_name}")
    def export(format_name: str, q: str = ""):
        with database.session() as session:
            rows = [export_record(result) for result in search_entries(session, q)]
        return export_response(format_name, "openkirkebog-records", rows, rows, EXPORT_FIELDS)

    @app.get("/operations")
    def usage_dashboard(request: Request):
        with database.session() as session:
            totals, groups = operations(session)
        return render(request, "operations", totals=totals, groups=groups)

    @app.get("/operations/export/{format_name}")
    def usage_export(format_name: str):
        with database.session() as session:
            totals, groups = operations(session)
        rows = [{"scope": "total", **totals}, *({"scope": "monthly_model", **group} for group in groups)]
        fields = list(dict.fromkeys(key for row in rows for key in row))
        return export_response(format_name, "openkirkebog-usage", {"totals": totals, "groups": groups}, rows, fields)

    return app
