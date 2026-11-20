# Codex Prompt: OpenKirkebog – AI-powered Danish Church Record Search

## Role

Act as a senior Python architect, full-stack developer, and AI engineer.

Build a working proof-of-concept application called **OpenKirkebog**, an open-source system for transforming scanned Danish historical church records (*kirkebøger*) into structured, searchable data using AI-powered handwriting recognition.

The long-term vision is to create a collaborative, community-driven platform that makes historical Danish church records searchable while preserving links to the original archival sources.

For now, build a fully functional local proof of concept capable of processing approximately 5–10 scanned church record pages.

Do not build a mockup. Implement actual working functionality.

## 1. Core principles

The architecture must follow these principles:

- Open source by design.
- Self-hostable on Linux.
- Docker-based deployment.
- Modular architecture with clear separation of concerns.
- Provider-independent AI processing.
- No vendor lock-in.
- Structured and reproducible data extraction.
- Human verification and correction.
- Preserve original source references.
- Designed for eventual large-scale and distributed processing.
- Privacy and archival terms compliance.

Avoid unnecessary complexity in the initial implementation while keeping sensible extension points for future development.

## 2. Technology stack

Use:

| Component | Technology |
|---|---|
| Backend | Python 3.12+ / FastAPI |
| Frontend | FastAPI templates, Jinja2, HTMX and lightweight JavaScript |
| Database | SQLite with FTS5 |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| AI processing | Pluggable provider architecture |
| Image handling | Pillow |
| Background jobs | Persistent SQLite-backed job queue |
| Deployment | Docker Compose |
| Configuration | Environment variables |

Use PostgreSQL-compatible SQLAlchemy models wherever practical so the database can later be migrated without redesigning the application.

Do not introduce Redis, Celery, Kubernetes or distributed infrastructure in the initial PoC.

## 3. Source management

Users must be able to create archival sources.

A source should contain:

- Archive name.
- Parish (*sogn*).
- District (*herred*), if known.
- County (*amt*), if known.
- Church book reference.
- Start and end year.
- Record category.
- Original Arkivalieronline URL.
- Source identifiers.
- Import date.
- Copyright/licensing information when known.

Example source URL:

https://arkivalieronline.rigsarkivet.dk/da/billedviser?epid=17128504#173399,29159223

The application should parse relevant identifiers from the URL when possible.

Do not assume that source identifiers are sequential or that image IDs can be calculated.

Preserve the exact original URL.

### Image import

Implement:

1. Upload of individual images.
2. Upload of multiple images.
3. Local directory import.
4. Association of uploaded images with a source.
5. Automatic image hashing to avoid duplicates.
6. Preservation of original filenames.
7. Thumbnail generation.

Prepare a separate archival download interface, but do not enable bulk downloading by default.

Any automated retrieval must be implemented as a clearly isolated, opt-in component with configurable rate limiting, no concurrent requests by default, retry/backoff, and explicit documentation about permission requirements.

Do not attempt to bypass access controls, scraping restrictions or anti-bot mechanisms.

For the initial PoC, manually downloaded images must be sufficient.

## 4. AI handwriting recognition

Implement a pluggable AI provider interface.

The initial implementation should support OpenAI's vision-capable API.

Make the model configurable through environment variables rather than hardcoding model identifiers.

Design the interfaces so additional providers can be added later, such as:

- Local vision-language models.
- Transkribus or other handwriting recognition services.
- Other cloud-based multimodal models.

All provider implementations must return a common structured result.

### Processing workflow

For every image:

1. Load and validate the image.
2. Identify the layout.
3. Detect table columns where possible.
4. Determine the record category.
5. Identify individual entries.
6. Transcribe handwritten text.
7. Extract structured fields.
8. Preserve the original transcription.
9. Record uncertain readings.
10. Save the results to the database.

Never silently invent missing information.

Use `null` for unknown structured values.

Preserve historical spellings in the literal transcription.

Normalized names and dates should be stored in separate fields.

### Two-stage processing

Prefer two stages when practical:

**Stage A – Transcription**

Produce a faithful transcription of the original source, preserving uncertain words and the association between text and table cells.

**Stage B – Structured extraction**

Convert the transcription into structured person records and events.

Keep the stages independently repeatable so that improvements to structured extraction do not require repeating the vision analysis.

The application should allow reprocessing using a different model or prompt version.

## 5. Church record types

The data model must support at least:

- Births and baptisms.
- Confirmations.
- Marriages.
- Deaths and burials.

Different church books use different layouts and columns.

Avoid a rigid schema that only works with one book type.

Implement common structured fields and allow additional event-specific attributes in JSON.

### Suggested entities

**Source**

Metadata about a church book.

**Page**

Individual scanned image and its source reference.

**Transcription**

AI-generated or manually corrected text associated with a page.

**Entry**

A particular registration within a page.

**PersonMention**

A person mentioned within an entry.

**Event**

Birth, baptism, confirmation, marriage, death or burial.

**RelationshipMention**

A relationship explicitly stated in a source, such as parent, spouse or child.

**ProcessingRun**

Information about the AI model, prompt version, processing date and result.

**Correction**

An auditable human correction to an AI-generated result.

Important: Do not assume that two person mentions refer to the same historical person.

Cross-source identity resolution is outside the initial PoC.

## 6. Structured extraction format

Use Pydantic schemas to validate the AI output.

An example record:

```json
{
  "entry_number": "42",
  "event_type": "death",
  "event_date": "1923-11-24",
  "persons": [
    {
      "name_original": "Peter Petersen",
      "name_normalized": "Peter Petersen",
      "role": "deceased",
      "birth_date": null,
      "occupation": null,
      "residence": null
    }
  ],
  "transcription": {
    "raw_text": "...",
    "uncertain_readings": []
  },
  "review_status": "unreviewed"
}
```

This is an illustrative schema, not a verified transcription.

Support multiple persons per entry.

Support uncertain dates and partially known dates, including situations where an exact ISO date cannot be established.

Do not automatically turn uncertain historical readings into apparently certain normalized values.

## 7. Image regions and provenance

The system should preserve the location of extracted information within the original image whenever the model or processing method can provide reliable coordinates.

Use normalized bounding boxes:

```json
{
  "x": 0.12,
  "y": 0.31,
  "width": 0.74,
  "height": 0.08
}
```

Coordinates must be relative to the image dimensions.

Validate coordinates before storing them.

If precise coordinates are unavailable, store `null`. Do not invent bounding boxes.

Associate entries with their original page.

Ideally, associate individual text fragments with image regions.

This will support highlighting the relevant handwritten text when a user opens a search result.

## 8. Web interface

Build a simple, functional, responsive web application.

### Dashboard

Display:

- Number of sources.
- Number of imported pages.
- Number of processed pages.
- Number of extracted entries.
- Number of entries awaiting review.
- Number of processing errors.

### Source browser

Allow users to:

- Browse church books.
- View imported pages.
- Filter by parish, period and record category.
- Inspect processing status.

### Page viewer

This is a critical component.

Create a split-screen interface.

**Left side:**

Original scanned church book page.

Support:

- Zoom.
- Pan.
- Fit to width.
- Highlighting image regions when available.

**Right side:**

AI transcription and structured records.

Support:

- Editing transcription.
- Correcting names and dates.
- Marking uncertain words.
- Saving corrections.
- Marking entries as reviewed.

The original image should remain visible while editing.

### Search interface

Implement full-text search using SQLite FTS5.

Searchable fields should include:

- Person name.
- Original transcription.
- Parish.
- Year or date.
- Record type.
- Occupation.
- Residence.

Support partial names, and implement basic normalization for common historical spelling variations where practical.

Search results must link back to their exact source page and entry.

Never present AI-generated readings as verified facts unless reviewed.

## 9. Processing jobs

AI processing must happen asynchronously relative to HTTP requests.

Implement a simple persistent job queue.

Job states:

- Pending.
- Processing.
- Completed.
- Failed.
- Cancelled.

Requirements:

- Jobs survive application restarts.
- Processing errors are recorded.
- Failed jobs can be retried.
- Duplicate jobs are avoided where practical.
- Users can trigger processing for one or several pages.
- Users can see progress and error messages.

Include a configurable concurrency limit.

Default to one AI-processing worker.

Record API usage and processing time where available.

Provide an estimated processing cost when the selected AI provider exposes sufficient usage and pricing data. Do not hardcode potentially outdated pricing.

## 10. Privacy and archival compliance

Historical church records may contain information about living people or sensitive personal circumstances.

Implement privacy-aware defaults.

Requirements:

- Do not automatically publish extracted data to the internet.
- Keep the PoC local-only by default.
- Do not distribute original images by default.
- Preserve source attribution.
- Allow records to be excluded from public search.
- Preserve corrections and processing history.
- Do not assume that publicly viewable records can automatically be republished as structured datasets.

Document the need to verify the Danish National Archives' terms and applicable data protection rules before bulk processing or public publication.

## 11. Data export

Implement export to:

- JSON.
- CSV.

Exports should retain source references and verification status.

Keep the data structures suitable for future publication as open datasets.

Do not assume that source data may be released under a particular open license without checking the relevant rights.

## 12. Testing

Include automated tests for:

- URL parsing.
- Image import.
- Duplicate detection.
- Database operations.
- AI output schema validation.
- Date normalization.
- Handling uncertain readings.
- Search.
- Correction persistence.
- Job processing and retries.

Use mocked AI responses in tests.

Include at least one fixture demonstrating multiple handwritten records on the same page.

The entire test suite must run without requiring an external API key.

## 13. Deployment

Provide:

- `Dockerfile`
- `compose.yaml`
- `.env.example`
- `README.md`
- Database migration setup
- Persistent volumes for database and uploaded images

A user should be able to start the application with:

```bash
docker compose up -d --build
```

Document how to configure an API key and launch the first transcription job.

## 14. Future architecture

Document, but do not implement yet:

- PostgreSQL migration.
- Distributed AI workers.
- Volunteer GPU processing.
- Multi-user authentication and permissions.
- Community corrections and peer review.
- Dataset releases and versioning.
- Automatic matching of historical persons.
- Integration with Link-Lives datasets.
- Integration with Rigsarkivet APIs or permitted bulk exports.
- Model benchmarking and fine-tuning.
- Internationalization and support for other countries.

Avoid premature implementation of these features.

## 15. Deliverables

Produce:

1. Working application source code.
2. Docker deployment.
3. Database schema and migrations.
4. AI provider abstraction and initial working provider.
5. Image upload and source management.
6. AI processing pipeline.
7. Page viewer and correction interface.
8. Search functionality.
9. JSON and CSV export.
10. Automated tests.
11. Technical documentation.

Add `ARCHITECTURE.md` explaining the major design decisions.

Add `ROADMAP.md` describing how the PoC can evolve into a community-driven open-source platform.

## 16. Implementation approach

Implement the application incrementally.

Prioritize a complete vertical slice:

**Import image → AI transcription → structured extraction → database → search → review.**

The initial application must work end-to-end.

Avoid producing only scaffolding, placeholder functions or non-functional UI components.

Choose practical defaults, implement functionality, and run the available tests.

If some functionality cannot be completed, document the limitation clearly instead of presenting unfinished functionality as complete.

## 17. Initial acceptance criteria

The PoC is successful when:

- A user can create a church book source.
- A user can import 5–10 scanned pages.
- The application can process those pages using a configured vision model.
- Individual handwritten entries are extracted into structured records.
- Original transcriptions remain available.
- A user can search across the extracted information.
- Search results link to the correct original page.
- A user can correct an AI transcription.
- Corrections persist after restarting the application.
- Processing failures can be inspected and retried.
- The application runs entirely through Docker Compose.
- Automated tests pass without external API access.

The actual quality of handwriting recognition must be measured and reported, not assumed.

18. Community funding and financial sustainability
OpenKirkebog is intended to be a public-benefit, community-driven open-source project. The long-term operational model should support voluntary donations to cover infrastructure, AI inference/token costs, storage, backups and other documented operating expenses.

Fundamental principles
- Keep access to publicly releasable transcriptions, search and open-source code free for everyone, regardless of donations.
- Donations must never give contributors exclusive access to historical records or priority over legal and ethical constraints.
- Publish understandable operating-cost and project-progress information, using verified measurements rather than estimates presented as facts.
- Distinguish donated funds, expenditure, remaining funds, and in-kind contributions (for example donated hardware or volunteer time).
- Do not claim charitable/tax-deductible status without establishing an appropriate legal entity and verifying applicable rules.
- Handle donor privacy carefully: no public names or donation amounts without explicit consent.
18.1 PoC scope: prepare for funding, do not implement payments yet
For the initial proof of concept, do not integrate Stripe, PayPal, MobilePay, crowdfunding platforms, bank APIs or any payment gateway. Do not collect donor payment details.

Instead, implement the minimum reliable instrumentation needed to calculate what it costs to digitize a page and what outcomes that spending produces.

At minimum, persist the following per AI processing run, where available:
- Processing run ID, image/page ID, source ID, model provider and model identifier.
- Prompt and pipeline version.
- Start/end timestamps, runtime and success/failure status.
- Input/output token counts (and additional token categories only when reported by the provider).
- Provider-reported cost if available, including currency and provenance.
- Otherwise, a clearly labeled estimated cost based on configurable, versioned pricing; never silently assume current prices.
- Retry count; do not hide the cost of unsuccessful or retried requests.
- Number of entries found, number reviewed and number verified.
Missing provider usage data should remain null or unknown, not zero.

Use a separate, versioned pricing configuration or pricing table with effective dates. Keep historical cost calculations reproducible even if provider pricing changes later. API keys, credentials and sensitive request data must never appear in public reports or logs.
18.2 Metrics and transparency
Add a basic Operations / Usage dashboard (local/admin-only in the PoC), showing:
- Total pages imported, processed and successfully processed.
- Number of extracted entries, reviewed entries and verified entries.
- Processing runs, failures and retries.
- Total reported/estimated AI cost, split by model/provider and period.
- Average AI cost per successfully processed page.
- Average AI cost per verified entry only when a meaningful number of entries have been verified.
- Processing time per page and throughput.
- Unavailable or incomplete metrics clearly identified.
Include an export of anonymized, aggregated processing metrics as JSON/CSV so a future public transparency page can consume them.

It must be possible to distinguish inference cost from hosting, storage, backup, bandwidth and other operational costs. For the PoC, non-AI expenses may be manually entered estimates or omitted; label them accordingly.
18.3 Future public donations dashboard (document only)
Document a future public-facing dashboard that may display:
- Donations received during a reporting period, expenditure and current project balance.
- Expenses grouped by AI processing, servers, storage/backups and other categories.
- Pages processed and records reviewed/verified during that period.
- Estimated pages that a proposed donation could finance, based on measured recent cost-per-page and a clear methodology.
- Overall progress by parish, church book and period.
- Optional sponsorship of a particular parish/book as a funding preference, not a guaranteed exclusive processing service.
Do not show example numbers as real financial figures. A funding goal or cost-per-page projection must be explicitly labeled as an estimate until supported by measured data.
18.4 Future volunteer compute (document only)
Design a future extension where volunteers may contribute compute resources through isolated workers. Do not build the distributed worker system in the PoC.

The architecture documentation should address:
- Explicit permission to distribute archive images and any relevant third-party licensing constraints.
- Signed or authenticated jobs, task validation and protection against malicious or faulty results.
- Reproducibility, model/version tracking and quality assurance.
- Least-privilege access, data minimization and safe handling of potentially sensitive records.
- Avoiding exposure of centrally managed AI API keys to volunteer machines.
- Comparing volunteer GPU inference costs/quality with commercial inference.
18.5 Future governance (document only)
Add a short roadmap section describing how a Danish non-profit association or another suitable governance model might eventually manage donations, expenses, ownership and accountability. This is a future organizational decision, not a PoC prerequisite.
19. Additional acceptance criteria
The PoC should additionally demonstrate that:
1. Each processing run preserves available token usage, model identity, timestamps and cost provenance.
2. Estimated and provider-reported costs are distinguishable.
3. Retried and failed requests remain visible in usage accounting.
4. The operations dashboard reports total and per-page AI costs without inventing missing data.
5. An aggregated JSON/CSV usage export can be generated.
6. The architecture and roadmap document donations, public financial transparency and volunteer compute as future capabilities.
7. No payment provider, donor database or public donation endpoint is required in the first version.
Implementation priority: First complete the original end-to-end pipeline (import → transcription → extraction → search → human review). Add usage tracking as part of the processing pipeline, then the lightweight operations dashboard. Do not allow the funding architecture to delay or complicate the core PoC.

Build the application now.