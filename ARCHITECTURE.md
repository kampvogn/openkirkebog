# OpenKirkebog architecture

The PoC runs FastAPI, template rendering and background workers in one Python process. SQLite and image files share a local data directory. Docker Compose publishes the service to the host loopback interface and mounts one persistent data volume. Alembic applies schema migrations before serving. Keep one application process per volume.

## Source to searchable entry

1. **Source and image import.** A source holds archive attribution and the original URL alongside parsed identifiers where available. Local upload and directory import validate image files with Pillow and retain original bytes and filenames alongside an oriented viewing image and thumbnail. A content hash makes repeat imports of the same bytes into a church book idempotent. Source-scoped deduplication preserves separate attribution when the same bytes belong to different sources.
2. **Persistent processing jobs.** HTTP handlers queue work in SQLite and return. Background threads take jobs up to the configured concurrency limit. Job and attempt records make errors and retries inspectable. The worker records model, prompt and pipeline provenance for the relevant stage.
3. **Two-stage AI provider contract.** The vision stage transcribes the page, including layout, entries and uncertainty. Extraction takes the transcription and maps it to typed entries, events, person mentions and stated relationships. Pydantic validates the provider result before persistence. Stage outputs are versioned so extraction can reuse an existing transcription.
4. **Audit and review.** The page viewer keeps the scan available beside the reading. A reviewed correction preserves the corresponding original AI artifact and records a human revision. Names as written and normalized values serve different purposes. Missing or ambiguous values retain their uncertainty. Location data is optional, bounded to the source image and shown only when available. Person mentions stay entry-scoped.
5. **Search and export.** SQLite FTS5 serves textual search over the current entry readings. Each result carries source and page provenance and links to its specific entry. JSON and CSV exports preserve source and verification information. Restricted records are excluded from search and record exports, and remain available in the local page viewer.

SQLAlchemy keeps the core model relational. SQLite FTS5, queue coordination and filesystem storage are the main deployment-specific seams. Event-specific details can evolve in structured JSON. Archive retrieval has a separate extension point for a future permitted importer. Imports in this release start from files supplied by the user.

## Arkivalieronline page references

In the observed listing for Århus Sank Lukas Sogn, Århus Amt, the unit `1952 K - 1961 K` expands to 295 image entries. `K` stands for *konfirmerede*. Each entry opens a scan, which can contain several church register entries. [Arkivalieronline parish register guide](https://arkivalieronline.rigsarkivet.dk/da/geo/geo-collection/5)

| Reference | Observed value | Meaning |
| --- | --- | --- |
| Physical page (*side*) | `164` | Page number in the original church book |
| Viewer position (*opslag*) | `167` | Position in the image list for this unit |
| Viewer identifiers | `17128504`, `173417`, `29164900` | The `epid` query value, the unit ID in the fragment, and the image ID |
| Viewer URL | [Open opslag 167](https://arkivalieronline.rigsarkivet.dk/da/billedviser?epid=17128504#173417,29164900) | Opens the scan in its archival context |
| Download URL | [Image 29164900](https://api.rigsarkivet.dk/ao/v1/images/29164900) | Opens the individual image file, using the same image ID |

The viewer counts scans of the title page and instructions. The book's physical pagination leaves them unnumbered. Cite this example as `1952 K - 1961 K, side 164, opslag 167` and keep the physical page and viewer position separate. Record each value where known, preserve the full viewer URL including its fragment, and treat the archive identifiers as opaque. The observed difference between the numbers applies to this example only.

The PoC stores the viewer URL on the church book source and links search results to the specific scan in OpenKirkebog. Separate page-level fields for the physical page, viewer position, and exact viewer URL are pending. For manually downloaded test scans, keep the full viewer URL alongside the filename.

The direct image URL comes from the viewer's download button. Confirm supported integration options with Rigsarkivet before implementing a downloader. Rigsarkivet permits individual downloads through the viewer and requires permission for larger downloads. [Viewer instructions](https://www.rigsarkivet.dk/vejledning/saadan-bruger-du-billedviseren-paa-arkivalieronline/), [download terms](https://www.rigsarkivet.dk/arkivalieronline/#betingelser-for-download)

## Cost and outcomes

Provider usage and request timing belong to the processing history, including retry and failure paths when data is available. Preserve provider/model identity and the pricing provenance used for an estimate. The pricing configuration is optional and effective-dated. Provider-reported cost and rate-based estimates should stay distinguishable in any report. Missing token counts or prices remain unknown. The local dashboard and aggregated exports expose AI usage and outcomes without exporting transcriptions or API credentials. Hosting, storage, backups and bandwidth are separate operating costs.

## A future volunteer worker boundary

A distributed architecture would have to restrict each worker to the smallest authorized image or crop and require explicit redistribution permission from the archive and any other rights holder. Prefer delivering nonsensitive tasks with short-lived access, authenticated workers and signed, expiring job manifests. Validate task identity and result schemas, bind reported outputs to the job and input checksum, and quarantine suspect or incomplete batches. Track model weights/version, inference settings and prompt version. Independently reprocess a sample and compare with human review before trusting a worker's results. Use isolated execution and event logs that avoid private record content. Centrally managed AI keys stay on centrally managed infrastructure. Evaluate inference quality, throughput, electricity and administrative costs alongside commercial inference on a labeled test set.

Future finances and public reporting are described in [ROADMAP.md](ROADMAP.md).
