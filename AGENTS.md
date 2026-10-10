# AGENTS.md — working conventions for this repository

Read this before writing code here. It is short and every rule is load-bearing.

## Process

* One shared clone at `/home/team/shared/dual-agency-ugc`. Start from a clean
  default branch: `git checkout main && git pull --ff-only`.
* Do work on a feature branch (`feature/<short-slug>` or `fix/<short-slug>`),
  open a PR with `gh pr create`, and report the PR URL. The lead merges
  (squash); do not merge your own PR.
* Finish with `main` checked out, a clean working tree, and no stray background
  processes. Full workflow: `/home/team/shared/WORKFLOW.md`.

## Environment

* **stdlib only.** `asyncio`, `sqlite3`, `http.server`, `urllib`, `json`,
  `logging`, `argparse`, `re`, `unittest`. Nothing else.
* **Never run `pip install` or `npm install`**, and never add to
  `requirements.txt` — it describes the legacy FastAPI app, which cannot run
  here.
* `/home` has ~290 MB free. Don't create large files, virtualenvs or caches.
* Never touch `/home/team/shared/site` — that is the public website on port 3000.

## Logging

* Tag every agent action with its tag, supplied as the logger *name* so it
  appears exactly once per line: `[Agent-Scraper]` (ingest), `[Agent-Classifier]`
  (tiering), `[Agent-Script]` (per-tier script + persistence), `[Agent-Video]`
  (video provider queue), `[LeadLoop]` (pipeline driver, config, loop lifecycle).
  `[Agent-Dispatcher]` is reserved for the unbuilt step 2b — no code logs it yet.
* Use `Agent.log()` / `Agent.log_error()` from `agents/base.py` so per-item
  failures are logged under the right tag and never crash the caller.
* **Never log secret values.** Log whether a variable is set — use
  `config.describe()` / `config.summary()`, which report `set`/`unset` only.
  Campaign IDs and API keys are secrets.

## Safety and compliance

* **Dry-run is the default.** Live outbound requires the explicit go-live env
  flag (`DRY_RUN=false`). A live HeyGen render is refused unless all three hold:
  `DRY_RUN=false`, `HEYGEN_API_KEY` set, `MOCK_MODE` off. The refusal is logged
  and the row is marked `failed` — no silent skip. **No email is sent by this
  build**: the dispatcher (step 2b) does not exist yet.
* No scraping personal email addresses off the open web for bulk cold email.
* Check the `suppressions` table before any dispatch, and keep an easy opt-out
  in the copy. Truthful copy only — no manufactured urgency.
* Never commit `*.db`, `*.sqlite3`, `.env`, logs or `__pycache__` (see
  `.gitignore`).

## Data model

* `leads` is frozen: `(id, email, website, tier, video_id, video_url, status,
  created_at)` with `UNIQUE` on email, website and video_id. Do not alter it —
  add separate additive tables instead (`lead_sources`, `suppressions`,
  `lead_firmographics`).
* Dedupe is the point of the engine: normalise before insert, then
  `ON CONFLICT DO NOTHING`. Never bypass `db.insert_leads()`.
* Tier values are exactly `quick` / `scale` / `whitelabel`. Status lifecycle:
  `new` → `classified` → `scripted` → `video_queued`, plus `failed`. Dispatch
  states arrive with step 2b.
* Pipeline stages, one agent each: `[Agent-Scraper]` (ingest from
  `data/inbox/*.json` / `*.csv` / `data/queue.jsonl`) → `[Agent-Classifier]` →
  `[Agent-Script]` (persists `data/scripts/<lead_id>.json`) → `[Agent-Video]`
  (provider queue, dry-run by default). The dispatcher that would route a
  finished video into a tier's Instantly campaign is **not built**.
* `leads.updated_at` is added by an **idempotent, additive migration** run at
  startup (`Database.migrate_schema()`): skipped when the column already exists,
  backfilled from `created_at`. Upgrading an existing `leads.db` in place is
  safe. Never drop, rename or reorder columns.
* Shared resilience helpers live in `agents/util.py`: retry ×3 with exponential
  backoff (transient errors only) and a 2–5s throttle between consecutive *live*
  provider calls. Reuse them instead of writing new retry loops.

## Tests

* `python3 -m unittest discover -s tests -v` from the repository root.
* Tests must use a temp DB and temp data dir, and must not depend on files
  another test wrote.
