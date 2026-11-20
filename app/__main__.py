import argparse
from pathlib import Path
import sys

import uvicorn

from app.archive import MAX_IMAGE_BYTES, import_image
from app.config import Settings
from app.db import Database, Source
from app.web import create_app


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".jfif"}


def import_directory(database: Database, source_id: int, directory: Path) -> int:
    if not directory.is_dir():
        print("Choose an existing import directory.", file=sys.stderr)
        return 1
    with database.session() as session:
        if session.get(Source, source_id) is None:
            print(f"Source {source_id} does not exist.", file=sys.stderr)
            return 1
    imported = duplicates = rejected = 0
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            with path.open("rb") as stream:
                contents = stream.read(MAX_IMAGE_BYTES + 1)
            if import_image(database, source_id, path.name, contents):
                imported += 1
            else:
                duplicates += 1
        except (OSError, ValueError) as error:
            print(f"Skipped {path.name!r}: {error}", file=sys.stderr)
            rejected += 1
    print(f"Imported {imported}; duplicates {duplicates}; rejected {rejected}.")
    return int(rejected > 0)


def main():
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local web application and worker.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("migrate", help="Apply pending database migrations.")
    import_dir = commands.add_parser("import-dir", help="Import files from a local directory into a source.")
    import_dir.add_argument("source_id", type=int)
    import_dir.add_argument("directory", type=Path)
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.command == "serve":
        uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
        return 0
    database = Database(settings.directory)
    database.migrate()
    if args.command == "import-dir":
        return import_directory(database, args.source_id, args.directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
