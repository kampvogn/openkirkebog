# OpenKirkebog

OpenKirkebog is a local proof of concept for transcribing, searching and reviewing Danish historical church records. It is designed for a first batch of 5–10 scans downloaded by hand. Search results link to the original page and its archival source, and human review preserves the initial AI output.

## Start with Docker Compose

On Linux with Docker and Docker Compose installed, run the following from the project directory.

```sh
cp .env.example .env
mkdir -p imports
docker compose up -d --build
```

Open [OpenKirkebog](http://127.0.0.1:8000). The service binds to the host's loopback interface and starts with a migrated SQLite database. Set `HOST_PORT` in `.env` if port 8000 is occupied. The Compose volume holds the database, scans and thumbnails. `docker compose down` stops the service and retains the volume. Back up the volume before deleting it or moving to a new host.

For AI processing, set `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env` before starting the service. Use the identifier of a vision-capable OpenAI model available to your account. If you set these after starting, run `docker compose up -d` again to apply them. Keep the key private. Import, manual review and search use local storage. Running AI processing sends the page image or transcription to OpenAI and can incur API charges.

## Import and process the first pages

1. Create a source in the web interface. For local test scans, start with the parish and any other details you know. Add the archive, church book reference, years, category, original archive URL and licensing information as you establish them.
2. Upload one or more scans to that source. Supported single-page files are JPEG, PNG, TIFF, WebP and GIF, up to 40 MiB and 40 million pixels per file. For directory import, copy scans into the local `imports/` directory and run the command below using the source ID shown in the source page URL. Ensure Docker can read those files.
3. Select one or more imported pages and queue processing. Start with one page to verify the configuration. Check the job list for progress and retry failed jobs after addressing the error.
4. Open a processed page to compare entries with the scan. Correct the transcription and structured fields where needed, record uncertainties and mark checked entries as reviewed.
5. Search for a name or transcription term and follow the result back to its entry. Use the record exports when you need JSON or CSV with provenance and review status.

```sh
docker compose exec app python -m app import-dir SOURCE_ID /imports
```

Directory import reads JPEG, PNG, TIFF, WebP, and GIF files directly in the chosen folder. To import a subfolder, point the command at that folder, for example `/imports/aarhus-sank-lukas-sogn`. Archive access is unnecessary for importing scans already on disk.

For scans with known provenance, keep the full Arkivalieronline viewer URL alongside the filename. Record the page number in the physical book and the position in the viewer separately. [Arkivalieronline page reference example](ARCHITECTURE.md#arkivalieronline-page-references)

From a page, use **Behandl siden** to run both stages again or **Kør kun udtræk** to reuse the selected transcription. The optional model field overrides the default for that job. Earlier transcription and extraction versions remain available in the page viewer.

## Develop locally

Use Python 3.12 or newer with SQLite FTS5. Run these commands from the project directory.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m app migrate
python -m app serve
```

Run `python -m pytest` in the activated environment to run the automated tests. They use mocked AI and can run without API access or credentials. The native server uses `127.0.0.1:8000` by default. Set `APP_DATA_DIR` for an alternate database and image directory. The native default is `./data`. To import a local directory, run `python -m app import-dir SOURCE_ID DIRECTORY`.

## Usage and pricing

The local operations dashboard and aggregated JSON/CSV exports report processing outcomes, timing, token usage and AI cost where available. Cost per verified entry requires at least five verified entries in the group. Keep development quota, inference charges and hosting expenses separate. `AI_WORKERS` controls concurrency and defaults to one.

Optional `AI_PRICING_FILE` points to a JSON array of price records. Each record has `provider`, `model`, `version`, `effective_from` (YYYY-MM-DD), `currency`, `input_per_million` and `output_per_million`. Include `cached_input_per_million` if a separate cached-input rate applies. Source the rates from the provider for the specific model and effective date. With Compose, put the file in the data volume and set the in-container path, for example `/data/pricing.json`. Use `docker compose cp pricing.json app:/data/pricing.json` with the service running and recreate the service to load the environment setting. Usage or cost stays unknown when the required inputs are missing.

## Recognition quality

Actual handwriting recognition accuracy remains unmeasured here because the project does not include a manually labeled set of archival scans. Test model and prompt versions on 5–10 lawfully usable pages before relying on the output. Build a diplomatic transcription and mark ambiguous ground truth separately. Compare matched entries for character error rate, report missed and extra entries, and score names and dates separately with field-level accuracy and counts of unsupported guesses on unknown fields. Record the sampled record categories, page identifiers, denominators, model and prompt versions, and the review method so the result can be reproduced. Report image-region accuracy only where reliable reference boxes exist.

## Archival terms and privacy

The PoC is intended for single-user local operation and relies on loopback access for isolation. Keep the host and volume private, restrict access to exports and review provider data-handling terms before sending archival material to an external service. Verify Rigsarkivet's terms, applicable data protection rules and the rights to each source before bulk processing, redistribution or public publication. Preserve source attribution and review status when exporting. The restriction control removes entries from search and record exports while keeping them available in the local page viewer.

The application code is released under the [MIT License](LICENSE). Archival scans and extracted records are subject to their own rights and reuse terms.

See [ARCHITECTURE.md](ARCHITECTURE.md) for design and [ROADMAP.md](ROADMAP.md) for future development and governance.
