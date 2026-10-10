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
  appears exactly once per line: `[Agent-Scraper]`, `[Agent-Classifier]`,
  `[Agent-Script]`, `[Agent-Dispatcher]`, `[LeadLoop]`.
* Use `Agent.log()` / `Agent.log_error()` from `agents/base.py` so per-item
  failures are logged under the right tag and never crash the caller.
* **Never log secret values.** Log whether a variable is set — use
  `config.describe()` / `config.summary()`, which report `set`/`unset` only.
  Campaign IDs and API keys are secrets.

## Safety and compliance

* **Dry-run is the default.** Live outbound requires the explicit go-live env
  flag; step 1 makes no network calls at all.
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
* Tier values are exactly `quick` / `scale` / `whitelabel`. Status lifecycle in
  step 1 is `new` → `classified`, plus `failed`.

## Tests

* `python3 -m unittest discover -s tests -v` from the repository root.
* Tests must use a temp DB and temp data dir, and must not depend on files
  another test wrote.
