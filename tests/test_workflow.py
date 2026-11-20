import io
import json
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.__main__ import import_directory
from app.ai import AIError, AIResponse, ExtractionResult, TranscriptionResult
from app.archive import parse_identifiers
from app.config import Settings
from app.db import Correction, Entry, Job, Page, ProcessingRun, Source, Transcription
from app.web import create_app


EXAMPLE_URL = "https://arkivalieronline.rigsarkivet.dk/da/billedviser?epid=17128504#173399,29159223"
SOURCE = {
    "archive": "Rigsarkivet", "parish": "Aaby", "reference": "Kirkebog", "start_year": "1860",
    "end_year": "1880", "category": "baptism", "url": EXAMPLE_URL, "license": "Test fixture",
}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "two_records.json").read_text())


def scan(number=7, image_format="PNG"):
    image = Image.new("RGB", (640, 380), "#faedcc")
    drawing = ImageDraw.Draw(image)
    drawing.line([(22, 61), (611, 61)], fill="#769090")
    for offset, row in enumerate(FIXTURE["transcription"]["rows"]):
        drawing.text((29, 66 + offset * 65), row["raw_text"].replace("7.", f"{number}."), fill="#28343e")
    output = io.BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


class MockProvider:
    provider = "fixture"

    def __init__(self):
        self.transcriptions = 0
        self.extractions = 0
        self.fail_once = False
        self.extract_entered = Event()
        self.proceed = Event()
        self.proceed.set()

    def transcribe(self, image, media_type, category):
        assert image.startswith(b"\xff\xd8")
        assert media_type == "image/jpeg"
        assert category == "baptism"
        self.transcriptions += 1
        return AIResponse(TranscriptionResult.model_validate(FIXTURE["transcription"]),
                          usage={"input_tokens": 50, "output_tokens": 10})

    def extract(self, transcription):
        assert len(transcription.rows) == 2
        self.extractions += 1
        self.extract_entered.set()
        assert self.proceed.wait(4), "Worker should be released by test."
        if self.fail_once:
            self.fail_once = False
            raise AIError("Temporary test failure.", usage={"input_tokens": 12})
        return AIResponse(ExtractionResult.model_validate(FIXTURE["extraction"]),
                          usage={"input_tokens": 25, "output_tokens": 15})


def open_client(app):
    return TestClient(app, base_url="http://localhost", follow_redirects=False)


def create_source(client, **source):
    response = client.post("/sources", data={**SOURCE, **source})
    assert response.status_code == 303, response.text
    return response.headers["location"]


def import_scan(client, source, image=None):
    response = client.post(f"{source}/pages", files={"files": ("original.png", image or scan(), "image/png")})
    assert response.status_code == 303, response.text
    return response


def queue_pages(client, *pages, **extra):
    response = client.post("/jobs", data={"page_ids": [str(page) for page in pages], **extra})
    assert response.status_code == 303, response.text
    return response


def test_url_parser_keeps_non_sequential_ids_opaque():
    assert parse_identifiers(EXAMPLE_URL) == {"epid": "17128504", "book_id": "173399", "image_id": "29159223"}
    assert parse_identifiers(EXAMPLE_URL.split("#")[0] + "#0088,0000003")["image_id"] == "0000003"
    assert parse_identifiers("https://unrelated.example/?epid=8#3,2") == {}
    assert parse_identifiers("https://arkivalieronline.rigsarkivet.dk/#unsupported") == {}


def test_import_multiple_invalid_duplicates_and_directory(tmp_path):
    app = create_app(Settings(tmp_path / "data"), start_workers=False)
    with open_client(app) as client:
        source = create_source(client)
        assert "Aaby" in client.get("/sources?q=Åby&year=1872").text
        assert "Aaby" not in client.get("/sources?q=&year=1800").text
        response = client.post(f"{source}/pages", files=[
            ("files", ("første.png", scan(), "image/png")),
            ("files", ("second.png", scan(9), "image/png")),
            ("files", ("duplicate.png", scan(), "image/png")),
            ("files", ("bad.png", b"not an image", "image/png")),
        ])
        assert response.status_code == 422
        assert "Importeret: 2. Dubletter: 1." in response.text
        assert "bad.png" in response.text
        assert client.get("/pages/1/image").headers["content-type"] == "image/jpeg"
        assert client.get("/pages/1/thumbnail").status_code == 200
        assert client.get("/pages/1/original").content == scan()

        local = tmp_path / "local"
        local.mkdir()
        (local / "repeat.png").write_bytes(scan(9))
        (local / "third.png").write_bytes(scan(10))
        assert import_directory(app.state.database, 1, local) == 0
        with app.state.database.session() as session:
            assert [page.filename for page in session.scalars(select(Page).order_by(Page.id))] == [
                "første.png", "second.png", "third.png"
            ]
            stored = session.get(Source, 1)
            assert stored.url == EXAMPLE_URL
            assert stored.identifiers["image_id"] == "29159223"


def test_retry_review_versions_search_and_restart(tmp_path):
    mock = MockProvider()
    mock.fail_once = True
    settings = Settings(tmp_path, model="fixture-v1")
    app = create_app(settings, provider_factory=lambda model: mock, start_workers=False)
    queue = app.state.queue

    with open_client(app) as client:
        assert client.get("/").status_code == 200
        import_scan(client, create_source(client))
        queue_pages(client, 1)
        assert "k%C3%B8en%3A+0" in queue_pages(client, 1).headers["location"]
        assert queue.work_once()
        with app.state.database.session() as session:
            assert session.get(Job, 1).state == "failed"
            assert session.get(Job, 1).transcription_id == 1
        assert "Temporary test failure." in client.get("/pages/1").text
        assert client.post("/jobs/1/retry").status_code == 303
        assert queue.work_once()
        assert mock.transcriptions == 1 and mock.extractions == 2

        results = client.get("/search?q=eder+Dåb+Åby+1872")
        assert results.status_code == 200, results.text
        assert "/pages/1#entry-1" in results.text
        assert "/pages/1#entry-2" not in results.text
        assert "/pages/1#entry-1" in client.get("/search?q=Gårdmand").text
        assert "/pages/1#entry-2" in client.get("/search?q=Ni").text
        page = client.get("/pages/1")
        assert page.status_code == 200, page.text
        assert "[Kirsten?]" in page.text
        assert "data-x=\"0.045\"" in page.text

        edit = {
            "revision": 1, "raw_text": "Jens Pedersen, døbt. Sikker korrigeret tekst.", "event_date": "1872-05",
            "event_date_original": "Mai 1872", "date_uncertain": True, "status": "verified",
            "person_original": ["Jens Pedersen", "Peder Jensen"], "person_normalized": ["Jens Pedersen", "Peder Jensen"],
        }
        assert client.post("/entries/1/review", data=edit).status_code == 303
        assert client.post("/entries/1/review", data=edit).status_code == 409
        assert "/pages/1#entry-1" in client.get("/search?q=korrigeret").text
        assert client.get("/export/json?q=korrigeret").json()[0]["status"] == "verified"
        assert EXAMPLE_URL in client.get("/export/csv").text

        change = client.post("/pages/1/transcription", data={
            "transcription_id": 1, "raw_text": ["Manuel læsning af første række", "8. [Kirsten?] Nielsdatter"],
            "uncertain_readings": ["", "[Kirsten?]"],
        })
        assert change.status_code == 303, change.text
        assert "Manuel læsning" in client.get("/pages/1").text
        assert "Gaardmand" in client.get("/pages/1?transcription_id=1").text
        queue_pages(client, 1, stage="extraction", transcription_id=2, model="fixture-v2")
        assert queue.work_once()
        assert mock.transcriptions == 1 and mock.extractions == 3
        assert "/pages/1#entry-1" in client.get("/search?q=korrigeret").text
        assert "Manuel læsning" in client.get("/pages/1?extraction_id=2").text

    restarted = create_app(settings, provider_factory=lambda model: mock, start_workers=False)
    with open_client(restarted) as client:
        assert "Sikker korrigeret tekst" in client.get("/pages/1").text
        assert "Manuel læsning" in client.get("/pages/1").text
        assert "Drift og AI-forbrug" in client.get("/operations").text
        usage = client.get("/operations/export/json").json()
        assert usage["totals"]["runs"] == 4
        assert usage["totals"]["failures"] == 1
        assert usage["totals"]["retries"] == 1
        assert usage["totals"]["missing_cost"] == 4
        assert "monthly_model" in client.get("/operations/export/csv").text
        with restarted.state.database.session() as session:
            assert session.get(Correction, 1).before["raw_text"].startswith("7. Jens")
            assert session.get(Transcription, 1).origin == "ai"
            assert session.get(ProcessingRun, 2).usage == {"input_tokens": 12}
            assert session.get(ProcessingRun, 3).entries_verified == 1
            assert session.get(ProcessingRun, 4).model == "fixture-v2"
        assert client.post("/pages/1/extraction", data={"extraction_id": 2}).status_code == 303
        assert "/pages/1#entry-3" in client.get("/search?q=Manuel").text
        assert "Sikker korrigeret tekst" in client.get("/pages/1?extraction_id=1").text


def test_persistent_pending_cancel_and_async_processing(tmp_path):
    mock = MockProvider()
    mock.proceed.clear()
    settings = Settings(tmp_path, model="fixture-v1")
    first = create_app(settings, provider_factory=lambda model: mock, start_workers=False)
    with open_client(first) as client:
        source = create_source(client)
        import_scan(client, source)
        import_scan(client, source, scan(9))
        queue_pages(client, 1, 2)
        assert client.post("/jobs/2/cancel").status_code == 303
        assert mock.transcriptions == 0
    second = create_app(settings, provider_factory=lambda model: mock)
    with open_client(second) as client:
        try:
            assert mock.extract_entered.wait(4)
            assert client.get("/").status_code == 200
            assert "status-processing" in client.get("/pages/1").text
            assert mock.extractions == 1
        finally:
            mock.proceed.set()

    with second.state.database.session() as session:
        assert session.get(Job, 1).state == "completed"
        assert session.get(Job, 2).state == "cancelled"
        assert len(list(session.scalars(select(Entry)))) == 2


def test_exclusion_and_local_request_protection(tmp_path):
    mock = MockProvider()
    app = create_app(Settings(tmp_path, model="test"), provider_factory=lambda model: mock, start_workers=False)
    with open_client(app) as client:
        assert client.get("/", headers={"Host": "not.localhost"}).status_code == 400
        assert client.post("/sources", data=SOURCE, headers={"Origin": "https://other.example"}).status_code == 403
        import_scan(client, create_source(client))
        queue_pages(client, 1)
        assert app.state.queue.work_once()
        response = client.post("/entries/2/review", data={
            "revision": 1, "raw_text": "Uncertain name", "person_original": "[Kirsten?] Nielsdatter",
            "person_normalized": "", "person_uncertain": "0", "event_date": "1872-06",
            "excluded": "true", "status": "reviewed",
        })
        assert response.status_code == 303, response.text
        assert "/pages/1#entry-2" not in client.get("/search?q=Niels").text
        assert len(client.get("/export/json").json()) == 1
        assert "Uncertain name" in client.get("/pages/1").text


def test_import_and_process_six_distinct_pages(tmp_path):
    mock = MockProvider()
    app = create_app(Settings(tmp_path, model="test"), provider_factory=lambda model: mock, start_workers=False)
    with open_client(app) as client:
        source = create_source(client)
        images = [("files", (f"page-{index}.jpg", scan(index, "JPEG"), "image/jpeg")) for index in range(1, 7)]
        response = client.post(f"{source}/pages", files=images)
        assert response.status_code == 303, response.text
        queue_pages(client, *range(1, 7))
        for _ in images:
            assert app.state.queue.work_once()
        assert not app.state.queue.work_once()
        assert mock.transcriptions == mock.extractions == 6
        rows = client.get("/export/json").json()
        assert len(rows) == 12
        assert len({row["page_sha256"] for row in rows}) == 6
        assert client.get("/operations/export/json").json()["totals"]["pages_successful"] == 6


def test_provisional_source_without_remote_archive_access(tmp_path):
    app = create_app(Settings(tmp_path), start_workers=False)
    with open_client(app) as client:
        response = client.post("/sources", data={"parish": "Århus Sank Lukas Sogn", "county": "Århus Amt"})
        assert response.status_code == 303, response.text
        source = response.headers["location"]
        import_scan(client, source)
        assert client.get(source).status_code == 200
        with app.state.database.session() as session:
            draft = session.get(Source, 1)
            assert draft.category == "unknown"
            assert draft.url is None and draft.start_year is None and draft.reference is None
        response = client.post(source + "/complete", data={
            "archive": "Rigsarkivet", "reference": "Kirkebog", "start_year": "1860", "end_year": "1880",
            "url": EXAMPLE_URL, "category": "baptism",
        })
        assert response.status_code == 303, response.text
        assert client.post(source + "/complete", data={"url": "https://invalid.example/"}).status_code == 303
        with app.state.database.session() as session:
            completed = session.get(Source, 1)
            assert completed.url == EXAMPLE_URL
            assert completed.identifiers["image_id"] == "29159223"
            assert completed.start_year == 1860 and completed.category == "baptism"


def test_interrupted_run_is_recoverable_and_usage_remains_unknown(tmp_path):
    mock = MockProvider()
    settings = Settings(tmp_path, model="test")
    app = create_app(settings, provider_factory=lambda model: mock, start_workers=False)
    with open_client(app) as client:
        import_scan(client, create_source(client))
        queue_pages(client, 1)
        with app.state.database.write() as session:
            job = session.get(Job, 1)
            job.state = "processing"
            job.attempts = 1
            session.add(ProcessingRun(
                job_id=1, page_id=1, source_id=1, provider="fixture", model="test", stage="transcription",
                prompt_version="1", pipeline_version="1", attempt=1,
            ))

    reopened = create_app(settings, provider_factory=lambda model: mock, start_workers=False)
    with open_client(reopened) as client:
        with reopened.state.database.session() as session:
            run = session.get(ProcessingRun, 1)
            job = session.get(Job, 1)
            assert job.state == run.status == "failed"
            assert run.finished_at and run.runtime_ms is None and run.usage is None
        assert client.post("/jobs/1/retry").status_code == 303
        assert reopened.state.queue.work_once()
        totals = client.get("/operations/export/json").json()["totals"]
        assert (totals["runs"], totals["retries"], totals["missing_usage"]) == (3, 1, 1)
