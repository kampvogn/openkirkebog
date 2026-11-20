"""Persistent local queue. Network requests run outside SQLite transactions."""

import logging
from collections.abc import Callable
from threading import Event, Thread
from time import monotonic

from sqlalchemy import select

from app.ai import (
    AIError, AIProvider, ExtractionResult, TranscriptionResult,
    EXTRACTION_PROMPT_VERSION, PIPELINE_VERSION, TRANSCRIPTION_PROMPT_VERSION,
)
from app.archive import image_path
from app.db import Database, Entry, Extraction, Job, Page, ProcessingRun, Source, Transcription, now
from app.metrics import account
from app.search import index_entry


logger = logging.getLogger(__name__)
ACTIVE = ("pending", "processing")


class Queue:
    def __init__(self, database: Database, provider: Callable[[str], AIProvider] | None, pricing):
        self.database = database
        self.provider = provider
        self.pricing = pricing
        self.stopping = Event()
        self.ready = Event()
        self.threads: list[Thread] = []

    def start(self, count: int):
        with self.database.write() as session:
            for run in session.scalars(select(ProcessingRun).where(ProcessingRun.status == "processing")):
                run.status = "failed"
                run.finished_at = now()
                run.error = "Interrupted by application shutdown. Usage is unknown."
            for job in session.scalars(select(Job).where(Job.state == "processing")):
                job.state = "failed"
                job.finished_at = now()
                job.error = "Interrupted by application shutdown. Retry to resume."
        self.stopping.clear()
        if self.provider is not None:
            for index in range(count):
                thread = Thread(target=self._loop, name=f"ai-worker-{index + 1}", daemon=True)
                thread.start()
                self.threads.append(thread)

    def stop(self):
        self.stopping.set()
        self.ready.set()
        for thread in self.threads:
            thread.join()
        self.threads.clear()

    def _loop(self):
        while not self.stopping.is_set():
            try:
                if self.work_once():
                    continue
            except Exception as error:
                logger.error("Queue worker interrupted by %s", type(error).__name__)
            self.ready.wait(0.5)
            self.ready.clear()

    def enqueue(self, page_ids: list[int], stage: str, model: str, transcription_id: int | None):
        if stage not in {"pipeline", "extraction"}:
            raise ValueError("Select a supported processing stage.")
        if not model or len(model) > 200:
            raise ValueError("Set a model for processing.")
        if self.provider is None:
            raise ValueError("Configure the AI provider before queuing jobs.")
        if not page_ids or (transcription_id is not None and (len(page_ids) != 1 or stage != "extraction")):
            raise ValueError("Select pages and, for one extraction, a compatible transcription.")
        queued = 0
        with self.database.write() as session:
            for page_id in sorted(set(page_ids)):
                page = session.get(Page, page_id)
                if page is None:
                    raise ValueError("A selected page no longer exists.")
                active_job = session.scalar(select(Job.id).where(Job.page_id == page.id, Job.state.in_(ACTIVE)))
                if active_job is not None:
                    continue
                chosen_transcription = None
                if stage == "extraction":
                    chosen_transcription = transcription_id or page.active_transcription_id
                    version = session.get(Transcription, chosen_transcription) if chosen_transcription else None
                    if version is None or version.page_id != page.id:
                        raise ValueError("Select a saved transcription from this page.")
                session.add(Job(page_id=page.id, stage=stage, state="pending", model=model,
                                provider="openai", transcription_id=chosen_transcription))
                queued += 1
        self.ready.set()
        return queued

    def change_state(self, job_id: int, state: str):
        with self.database.write() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise LookupError("Job not found.")
            if state == "pending" and job.state == "failed":
                if self.provider is None:
                    raise ValueError("Configure the AI provider before retrying.")
                active = session.scalar(select(Job.id).where(
                    Job.page_id == job.page_id, Job.state.in_(ACTIVE)
                ))
                if active is not None:
                    raise ValueError("There is already an active job for this page.")
                job.state = state
                job.error = None
                job.finished_at = None
            elif state == "cancelled" and job.state == "pending":
                job.state = state
                job.finished_at = now()
            else:
                raise ValueError("The job is no longer in a state that allows this action.")
            page_id = job.page_id
        self.ready.set()
        return page_id

    def work_once(self):
        if self.provider is None:
            return False
        # Idle polling does not acquire a database write lock.
        with self.database.session() as session:
            if session.scalar(select(Job.id).where(Job.state == "pending").limit(1)) is None:
                return False
        with self.database.write() as session:
            job = session.scalar(select(Job).where(Job.state == "pending").order_by(Job.id).limit(1))
            if job is None:
                return False
            job.state = "processing"
            job.started_at = now()
            job.attempts += 1
            page = session.get(Page, job.page_id)
            source = session.get(Source, page.source_id)

        run_id = None
        response = None
        started = monotonic()
        try:
            provider = self.provider(job.model)
            if job.stage == "pipeline" and job.transcription_id is None:
                run_id = self._start_run(job, page, provider.provider, "transcription")
                response = provider.transcribe(
                    image_path(self.database.directory, page, "view.jpg").read_bytes(), "image/jpeg",
                    source.category,
                )
                transcription = TranscriptionResult.model_validate(response.data, from_attributes=True)
                with self.database.write() as session:
                    run = session.get(ProcessingRun, run_id)
                    version = Transcription(page_id=page.id, origin="ai", data=transcription.model_dump(mode="json"))
                    session.add(version)
                    session.flush()
                    fresh_page = session.get(Page, page.id)
                    active = session.get(Transcription, fresh_page.active_transcription_id) if fresh_page.active_transcription_id else None
                    if active is None or active.origin == "ai":
                        fresh_page.active_transcription_id = version.id
                    session.get(Job, job.id).transcription_id = version.id
                    run.transcription_id = version.id
                    self._finish(run, response, "completed", started)
                job.transcription_id = version.id

            run_id = None
            response = None
            started = monotonic()
            run_id = self._start_run(job, page, provider.provider, "extraction")
            with self.database.session() as session:
                version = session.get(Transcription, job.transcription_id)
                transcription = TranscriptionResult.model_validate(version.data)
            response = provider.extract(transcription)
            extraction = ExtractionResult.model_validate(response.data, from_attributes=True)
            if any(entry.row_index >= len(transcription.rows) for entry in extraction.entries):
                raise ValueError("Extraction refers to a transcription row that does not exist.")
            self._save_extraction(run_id, response, extraction, transcription, job, page, source, started)
        except Exception as error:
            if isinstance(error, AIError):
                message = str(error)
                response = error
            elif isinstance(error, OSError):
                message = "An imported image or its preview could not be read."
            elif isinstance(error, ValueError):
                message = "Processing returned invalid data. Check the model or try again."
            else:
                message = "Processing stopped because of an internal error."
                logger.error("Job %s stopped with %s", job.id, type(error).__name__)
            with self.database.write() as session:
                if run_id is not None:
                    run = session.get(ProcessingRun, run_id)
                    self._finish(run, response, "failed", started, message)
                failed = session.get(Job, job.id)
                failed.state = "failed"
                failed.error = message
                failed.finished_at = now()
        return True

    def _start_run(self, job: Job, page: Page, provider: str, stage: str):
        with self.database.write() as session:
            run = ProcessingRun(
                page_id=page.id, source_id=page.source_id, job_id=job.id, stage=stage, provider=provider,
                model=job.model, transcription_id=job.transcription_id, attempt=job.attempts,
                prompt_version=(TRANSCRIPTION_PROMPT_VERSION if stage == "transcription" else EXTRACTION_PROMPT_VERSION),
                pipeline_version=PIPELINE_VERSION,
            )
            session.add(run)
            session.flush()
            return run.id

    def _finish(self, run: ProcessingRun, response, status: str, started: float, error: str | None = None):
        run.status = status
        run.finished_at = now()
        run.runtime_ms = round((monotonic() - started) * 1000)
        run.error = error
        if response is not None:
            account(run, response, self.pricing)

    def _save_extraction(self, run_id, response, result, transcription, job, page, source, started):
        with self.database.write() as session:
            fresh_page = session.get(Page, page.id)
            current = session.get(Extraction, fresh_page.active_extraction_id) if fresh_page.active_extraction_id else None
            prior = list(session.scalars(select(Entry).where(Entry.extraction_id == current.id))) if current else []
            protected_rows = {entry.data["row_index"] for entry in prior if entry.excluded}
            version = Extraction(page_id=page.id, transcription_id=job.transcription_id)
            session.add(version)
            session.flush()
            for result_entry in result.entries:
                row = transcription.rows[result_entry.row_index]
                excluded = bool(protected_rows) and (
                    current.transcription_id != version.transcription_id or result_entry.row_index in protected_rows
                )
                entry = Entry(
                    extraction_id=version.id, data=result_entry.model_dump(mode="json"), raw_text=row.raw_text,
                    uncertain_readings=row.uncertain_readings, bbox=row.bbox.model_dump() if row.bbox else None,
                    excluded=excluded,
                )
                session.add(entry)
                session.flush()
                index_entry(session, entry, source)
            if current is None or (
                fresh_page.active_transcription_id == job.transcription_id
                and all(entry.revision == 1 for entry in prior)
            ):
                fresh_page.active_extraction_id = version.id
            run = session.get(ProcessingRun, run_id)
            run.extraction_id = version.id
            run.entries_found = len(result.entries)
            run.entries_reviewed = 0
            run.entries_verified = 0
            self._finish(run, response, "completed", started)
            completed = session.get(Job, job.id)
            completed.state = "completed"
            completed.finished_at = now()
