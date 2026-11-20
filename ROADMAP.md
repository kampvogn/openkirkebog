# OpenKirkebog roadmap

## Validate the local proof of concept

- Measure recognition accuracy on lawfully usable scans from multiple record categories, layouts and periods. Report model and prompt versions and human review outcomes alongside the cost and processing time.
- Verify a backup and restore of SQLite, original images and thumbnails before expanding the collection.
- Collect feedback on split-screen review and search quality from people familiar with Danish handwriting and historical spelling.

## Scale processing and review

- Move durable storage to PostgreSQL when shared access or write throughput warrants it. Port the FTS5 query behavior and test search parity before migration.
- Separate queue workers and image storage from the web process when distributed deployment is needed. Use job leases, idempotent writes and durable run provenance. Pilot isolated volunteer GPU workers after permission and validation requirements can be met. Compare commercial and volunteer inference on the same labeled records and include hardware, operating and administrative costs.
- Add multi-user authentication, source-level permissions, contributor attribution, community corrections and peer review. Establish release and rollback procedures and retain reproducible versions of public datasets.
- Explore permitted Rigsarkivet APIs or bulk exports, Link-Lives integration and evidence-based person matching. Preserve provenance and match confidence whenever identities are linked. Evaluate new models and fine-tuning on rights-cleared material. Extend language and country support after validating Danish records across periods and layouts.

## Funding and governance

Keep publicly releasable records, search and project code freely accessible regardless of donations. A future public transparency dashboard could report donations, spending and project balance by period, with in-kind contributions tracked separately. Break out AI costs, servers, storage, backups and other expenses. Include pages processed, review and verification outcomes, and progress by parish, book and period. Base any proposed donation's estimated page count on measured recent cost per page and publish the calculation and coverage. A parish or church book preference may help prioritize permitted work without promising exclusive access or violating archival and privacy obligations.

A Danish non-profit association or another suitable governance body could take responsibility for project ownership, authorize expenditures, publish accounts and manage conflicts of interest. Establish the legal entity and verify applicable rules before describing donations as charitable or tax deductible. Publish donor names or individual amounts only with explicit consent. Decide these arrangements before collecting donations or adding payment services.
