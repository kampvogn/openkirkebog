import csv
import io
import json
import re
import unicodedata

from sqlalchemy import select, text

from app.db import Entry, Extraction, Page, Source


DANISH_TYPES = {
    "birth": "fødsel fødsler", "baptism": "dåb døbte", "confirmation": "konfirmation konfirmerede",
    "marriage": "vielse vielser ægteskab", "death": "død døde dødsfald", "burial": "begravelse begravede",
}


def fold(text_value: str) -> str:
    value = unicodedata.normalize("NFKD", text_value.casefold().replace("aa", "å"))
    return "".join(char for char in value if not unicodedata.combining(char))


def index_entry(session, entry: Entry, source: Source):
    data = entry.data
    parts = [entry.raw_text, source.parish, source.category, data["event_type"],
             DANISH_TYPES.get(data["event_type"]), data.get("event_date"),
             data.get("event_date_original"), data.get("entry_number")]
    for person in data["persons"]:
        parts.extend(person.get(field) for field in (
            "name_original", "name_normalized", "occupation", "residence", "birth_date"
        ))
    values = {"id": entry.id, "content": fold(" ".join(value for value in parts if value))}
    session.execute(text("DELETE FROM entry_search WHERE rowid = :id"), values)
    session.execute(text("INSERT INTO entry_search(rowid, content) VALUES (:id, :content)"), values)


def search_entries(session, query: str):
    statement = (
        select(Entry, Page, Source).join(Extraction, Extraction.id == Entry.extraction_id)
        .join(Page, Page.active_extraction_id == Extraction.id)
        .join(Source, Source.id == Page.source_id).where(Entry.excluded.is_(False))
        .order_by(Page.id, Entry.id)
    )
    words = re.findall(r"[\w]+", fold(query[:250]))
    long_words = [word for word in words if len(word) >= 3]
    short_words = [word for word in words if len(word) < 3]
    if long_words or short_words:
        terms = ["entry_search MATCH :match"] if long_words else []
        params = {"match": " AND ".join(f'"{word}"' for word in long_words)}
        for index, word in enumerate(short_words):
            terms.append(f"instr(content, :short_{index}) > 0")
            params[f"short_{index}"] = word
        predicate = text("entries.id IN (SELECT rowid FROM entry_search WHERE " + " AND ".join(terms) + ")")
        statement = statement.where(predicate.bindparams(**{
            name: value for name, value in params.items() if name != "match" or long_words
        }))
    return [{"entry": entry, "page": page, "source": source}
            for entry, page, source in session.execute(statement)]


def export_record(result: dict):
    entry, page, source = (result[key] for key in ("entry", "page", "source"))
    return {
        "source_id": source.id, "archive": source.archive, "parish": source.parish,
        "district": source.district, "county": source.county, "reference": source.reference,
        "source_url": source.url, "source_identifiers": source.identifiers, "source_license": source.license,
        "page_id": page.id, "page_filename": page.filename, "page_sha256": page.sha256,
        "entry_id": entry.id, "entry_url": f"/pages/{page.id}#entry-{entry.id}",
        "extraction_id": entry.extraction_id, "revision": entry.revision, "status": entry.status,
        **entry.data, "raw_text": entry.raw_text, "uncertain_readings": entry.uncertain_readings,
        "bbox": entry.bbox,
    }


def csv_text(rows: list[dict], fields: list[str]) -> str:
    def escape(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
            return "'" + value
        return value

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: escape(value) for key, value in row.items()})
    return buffer.getvalue()
