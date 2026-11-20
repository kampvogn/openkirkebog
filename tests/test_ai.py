import json as jsonlib

import httpx
import pytest
from pydantic import ValidationError

from app.ai import (
    AIError,
    BoundingBox,
    ExtractedEntry,
    OpenAIProvider,
    Person,
    TranscribedRow,
    TranscriptionResult,
    validate_date,
)


@pytest.mark.parametrize("value", [None, "1888", "1888-02", "1888-02-29"])
def test_normalized_dates_keep_source_precision(value):
    assert validate_date(value) == value


@pytest.mark.parametrize("value", ["0000", "1900-02-29", "1888-02-30", "1888-2", "1888-13", "?"])
def test_invalid_normalized_dates_are_rejected(value):
    with pytest.raises(ValidationError):
        ExtractedEntry(row_index=0, event_type="birth", event_date=value)


def test_uncertainty_prevents_false_precision():
    entry = ExtractedEntry(
        row_index=0,
        event_type="birth",
        event_date="1888-02",
        event_date_original="[1?] Februar 1888",
        date_uncertain=True,
    )
    assert entry.event_date == "1888-02"
    assert entry.event_date_original == "[1?] Februar 1888"
    with pytest.raises(ValidationError):
        ExtractedEntry(row_index=0, event_type="birth", event_date="1888-02-01", date_uncertain=True)
    with pytest.raises(ValidationError):
        Person(name_original="[Hans?]", name_normalized="Hans", name_uncertain=True)


def test_validates_image_regions_and_cell_labels():
    assert BoundingBox(x=0, y=0, width=1, height=1).width == 1
    with pytest.raises(ValidationError):
        BoundingBox(x=0.5, y=0, width=0.51, height=1)
    with pytest.raises(ValidationError):
        BoundingBox(x=0, y=0, width=0, height=1)
    with pytest.raises(ValidationError):
        TranscriptionResult(
            columns=["Navn"],
            rows=[TranscribedRow(raw_text="Margrethe", cells={"Ukendt": "Margrethe"})],
        )


def test_relationships_must_reference_people_in_the_entry():
    with pytest.raises(ValidationError):
        ExtractedEntry(
            row_index=0,
            event_type="baptism",
            persons=[Person(name_original="Ane Kirstine")],
            relationships=[{"subject_index": 1, "object_index": 0, "relationship_type": "parent"}],
        )


def test_event_attributes_must_be_serializable_json():
    with pytest.raises(ValidationError):
        ExtractedEntry(
            row_index=0,
            event_type="baptism",
            attributes={"additional": [{"invalid": float("nan")}]},
        )


@pytest.fixture
def transcription():
    return {
        "category": "birth",
        "columns": ["Barnets Navn", "Fødselsdag", "Daab"],
        "rows": [
            {
                "raw_text": "1. Ane Kirstine, 3 Februar 1888, 5 Marts",
                "cells": {"Barnets Navn": "Ane Kirstine", "Fødselsdag": "3 Februar 1888", "Daab": "5 Marts"},
            },
            {
                "raw_text": "2. [Jens?] Pedersen, [1?] Marts 1888",
                "uncertain_readings": ["[Jens?]", "[1?]"],
                "cells": {"Barnets Navn": "[Jens?] Pedersen", "Fødselsdag": "[1?] Marts 1888", "Daab": None},
            },
        ],
    }


@pytest.fixture
def provider():
    return OpenAIProvider(api_key="test-key", model="test-model")


def response_payload(text):
    return {
        "status": "completed",
        "model": "test-model-version",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {
            "input_tokens": 52,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 23,
            "output_tokens_details": {"reasoning_tokens": 8},
            "total_tokens": 75,
        },
    }


def test_provider_uses_separate_requests_and_preserves_multiple_events(monkeypatch, provider, transcription):
    extraction = {
        "entries": [
            {"row_index": 0, "event_type": "birth", "event_date": "1888-02-03"},
            {
                "row_index": 0,
                "event_type": "baptism",
                "event_date": "1888-03-05",
                "attributes": {"baptism_place": {"name": "Kirken", "additional": None}},
            },
            {
                "row_index": 1,
                "event_type": "birth",
                "event_date": "1888-03",
                "date_uncertain": True,
                "persons": [{"name_original": "[Jens?] Pedersen", "name_uncertain": True}],
            },
        ]
    }
    requests = []

    def post(url, *, headers, json, timeout):
        assert headers["Authorization"] == "Bearer test-key"
        assert url == "https://api.openai.com/v1/responses"
        requests.append(json)
        result = transcription if len(requests) == 1 else extraction
        return httpx.Response(200, json=response_payload(jsonlib.dumps(result)))

    monkeypatch.setattr(httpx, "post", post)
    stage_a = provider.transcribe(b"test-image", "image/jpeg", "birth")
    stage_b = provider.extract(stage_a.data)
    assert len(requests) == 2
    assert all(req["store"] is False for req in requests)
    assert all(req["text"]["format"] == {"type": "json_object"} for req in requests)
    assert requests[0]["input"][0]["content"][1]["image_url"] == "data:image/jpeg;base64,dGVzdC1pbWFnZQ=="
    stage_b_content = requests[1]["input"][0]["content"]
    assert len(stage_b_content) == 1
    assert jsonlib.loads(stage_b_content[0]["text"])["rows"][1]["uncertain_readings"] == ["[Jens?]", "[1?]"]
    assert [entry.row_index for entry in stage_b.data.entries] == [0, 0, 1]
    assert stage_b.usage == {
        "input_tokens": 52,
        "output_tokens": 23,
        "total_tokens": 75,
        "cached_tokens": 0,
        "reasoning_tokens": 8,
    }
    assert stage_b.resolved_model == "test-model-version"
    assert stage_b.cost is None


@pytest.mark.parametrize(
    ("status_code", "payload", "message"),
    [
        (200, response_payload("private invalid result"), "did not pass validation"),
        (200, response_payload('{"rows": [{"raw_text": 123}]}'), "did not pass validation"),
        (200, {**response_payload("{}"), "status": "incomplete"}, "did not complete"),
        (200, {**response_payload("{}"), "output": "private text"}, "Invalid response output"),
        (429, {**response_payload("{}"), "error": {"message": "private text"}}, "HTTP 429"),
    ],
)
def test_malformed_and_failed_requests_keep_safe_metadata(monkeypatch, provider, status_code, payload, message):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(status_code, json=payload)

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(AIError, match=message) as exc:
        provider.transcribe(b"test-image", "image/png", "birth")
    assert exc.value.usage["output_tokens"] == 23
    assert exc.value.resolved_model == "test-model-version"
    assert "private" not in str(exc.value)
    assert calls == [1]


def test_extraction_rejects_nonexistent_rows(monkeypatch, provider):
    payload = response_payload('{"entries": [{"row_index": 0, "event_type": "death"}]}')
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(200, json=payload))
    with pytest.raises(AIError, match="nonexistent") as exc:
        provider.extract(TranscriptionResult(rows=[]))
    assert exc.value.usage["input_tokens"] == 52
