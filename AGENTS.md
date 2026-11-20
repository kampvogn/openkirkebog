OpenKirkebog – Agents instructions
Source of truth
- Read docs/OpenKirkebog.md before planning or implementing project work. Consult other relevant files under docs/ as needed.
- Inspect the repository before making changes; do not assume a blank project.
- Follow the specification and its acceptance criteria. Keep future-phase features out of the PoC unless explicitly requested.
Role and execution
- Act as lead engineer/orchestrator: plan briefly, delegate independent tasks when worthwhile, integrate and verify results.
- Prefer a working vertical slice: image import → transcription → extraction → storage → search → human review.
- Implement rather than stopping at a plan. Make reasonable decisions without unnecessary clarification.
- Keep changes focused; avoid unnecessary dependencies, abstractions and infrastructure.
Token and model economy
- Optimize total successful-task cost, including delegation overhead, review, retries and integration—not just tokens per call.
- For simple tasks, work directly or choose a lightweight model and low reasoning. Use medium capability for routine coding, stronger models/high reasoning for architecture, security, difficult debugging and data integrity.
- Only select models or reasoning levels that the current environment actually supports. Never claim a model switch that did not occur, or invent token/cost data.
- Delegate only when it provides a clear benefit. Default to at most 3 independent concurrent agents; prevent overlapping file edits and define interfaces first.
- Give subagents narrow scopes, relevant files/interfaces and acceptance criteria; avoid repeating the full specification in every handoff.
- Escalate after evidence of failure rather than retrying the same approach indefinitely.
- Track development quota, runtime AI API costs and hosting costs separately.
Quality and safety
- Preserve original transcriptions, source references, processing versions and human corrections; never silently overwrite reviewed data.
- Mark uncertain readings as uncertain; never invent missing historical facts or image coordinates.
- Treat historical person mentions as distinct unless identity is substantiated.
- Respect archival download terms, third-party rights and privacy requirements. Do not enable bulk scraping or public publication by default.
- Validate AI outputs, handle failures/retries, and test the end-to-end flow. Use mocked AI in automated tests.
Reporting and completion
- Maintain a concise plan and report decisions, completed work, tests, blockers and remaining items.
- Run relevant tests before calling work complete. Clearly identify untested or incomplete features.
- If token/cost telemetry is unavailable, report that fact rather than estimating it as observed usage.
