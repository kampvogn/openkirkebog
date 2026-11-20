"""Local source management and an interface for future permitted retrieval."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageOps
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select

from app.db import Database, Page


CATEGORIES = ("birth", "baptism", "birth/baptism", "confirmation", "marriage",
              "death", "burial", "death/burial", "mixed", "unknown")
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


def validate_url(value: str):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or any(character.isspace() for character in value)):
        raise ValueError("Enter an original HTTP or HTTPS archive URL.")
    return parsed


def parse_identifiers(url: str) -> dict[str, str]:
    parsed = validate_url(url)
    if parsed.hostname != "arkivalieronline.rigsarkivet.dk":
        return {}
    identifiers = {}
    epid = parse_qs(parsed.query).get("epid", [""])[0]
    if re.fullmatch(r"[0-9]+", epid):
        identifiers["epid"] = epid
    fragment = re.fullmatch(r"([0-9]+)(?:,([0-9]+))?", parsed.fragment)
    if fragment:
        identifiers["book_id"] = fragment[1]
        if fragment[2]:
            identifiers["image_id"] = fragment[2]
    return identifiers


class SourceInput(BaseModel):
    archive: str | None = Field(default=None, min_length=1, max_length=200)
    parish: str = Field(min_length=1, max_length=160)
    district: str | None = Field(default=None, max_length=160)
    county: str | None = Field(default=None, max_length=160)
    reference: str | None = Field(default=None, min_length=1, max_length=300)
    start_year: int | None = Field(default=None, ge=1, le=9999)
    end_year: int | None = Field(default=None, ge=1, le=9999)
    category: str = "unknown"
    url: str | None = Field(default=None, max_length=3000)
    license: str | None = Field(default=None, max_length=3000)

    @model_validator(mode="after")
    def check_source(self):
        if self.url is not None:
            validate_url(self.url)
        if self.start_year is not None and self.end_year is not None and self.start_year > self.end_year:
            raise ValueError("The end year must be at or after the start year.")
        if self.category not in CATEGORIES:
            raise ValueError("Select a supported record category.")
        return self


class ArchiveDownloader(Protocol):
    """Extension point only. Import uses user-supplied local files."""

    def fetch_permitted_image(self, url: str) -> bytes: ...


def image_path(directory: Path, page: Page, kind: str) -> Path:
    return directory / "images" / page.sha256[:2] / f"{page.sha256}.{kind}"


def write_file(path: Path, data: bytes):
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.flush()
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def jpeg(image: Image.Image, quality: int) -> bytes:
    output = BytesIO()
    image.save(output, "JPEG", quality=quality)
    return output.getvalue()


def import_image(database: Database, source_id: int, filename: str, contents: bytes) -> bool:
    """Return True for an imported page, False for a duplicate in the same source."""
    if not contents or len(contents) > MAX_IMAGE_BYTES:
        raise ValueError("Images must be between 1 byte and 40 MiB.")
    digest = sha256(contents).hexdigest()
    duplicate = select(Page.id).where(Page.source_id == source_id, Page.sha256 == digest)
    with database.session() as session:
        if session.scalar(duplicate):
            return False
    try:
        with Image.open(BytesIO(contents), formats=["JPEG", "PNG", "TIFF", "WEBP", "GIF"]) as original:
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ValueError("Images must contain at most 40 million pixels.")
            if getattr(original, "n_frames", 1) != 1:
                raise ValueError("Import each scanned page as a separate image.")
            upright = ImageOps.exif_transpose(original)
            if "A" in upright.getbands() or "transparency" in upright.info:
                rgba = upright.convert("RGBA")
                display = Image.new("RGB", rgba.size, "white")
                display.paste(rgba, mask=rgba.getchannel("A"))
            else:
                display = upright.convert("RGB")
            viewer = jpeg(display, 95)
            page = Page(
                source_id=source_id, filename=filename, sha256=digest,
                width=display.width, height=display.height, media_type=Image.MIME[original.format],
            )
            display.thumbnail((400, 400), Image.Resampling.LANCZOS)
            thumbnail = jpeg(display, 82)
    except (OSError, SyntaxError, EOFError, Image.DecompressionBombError) as error:
        raise ValueError("The file is not a supported, readable image.") from error

    with database.write() as session:
        if session.scalar(duplicate):
            return False
        for kind, data in (("original", contents), ("view.jpg", viewer), ("thumb.jpg", thumbnail)):
            write_file(image_path(database.directory, page, kind), data)
        session.add(page)
    return True


def validation_message(error: ValidationError) -> str:
    detail = error.errors(include_url=False, include_input=False)[0]
    location = ".".join(str(part) for part in detail["loc"])
    message = detail["msg"].removeprefix("Value error, ")
    return f"{location}: {message}" if location else message
