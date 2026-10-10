# dual-agency-ugc

UGC video ad agency: an autonomous lead engine plus the earlier webhook app.

* **LeadLoop engine** (current) — stdlib-only Python pipeline at the repository
  root: `main.py`, `config.py`, `db.py`, `agents/`.
* **Legacy FastAPI app** (reference only) — `legacy/fastapi_webhooks.py`, plus
  `video_pipeline.py`, `instantly_pipeline.py` and the older files in `agents/`.
  It needs third-party packages this machine cannot install. See
  [`legacy/README.md`](legacy/README.md). Nothing in it is imported by LeadLoop.

---

## LeadLoop engine (step 2a)

An autonomous background engine that ingests the owner's opted-in contact list,
assigns each lead a tier, writes a per-tier video script for it, and queues one
video render per lead. Everything is persisted in SQLite with hard dedupe on
email and website, so nothing is ever processed twice.

**Built and running (step 2a):** ingest → tiering → script → video queue, driven
by the asyncio runtime loop.

**Not built: the dispatcher (step 2b).** Nothing routes a finished video into
the tier's Instantly campaign yet, and no email or webhook is sent by this
build. That stage is not stubbed either — there is no placeholder module for it.

```
data/inbox/*.json ──┐
data/inbox/*.csv  ──┼─► [Agent-Scraper] ─► leads (SQLite, deduped)
data/queue.jsonl ───┘          │
                               ▼
                     [Agent-Classifier] ─► tier = quick | scale | whitelabel
                               │            status = classified
                               ▼
                     [Agent-Script] ─► data/scripts/<lead_id>.json (persisted)
                               │       status = scripted
                               ▼
                     [Agent-Video] ─► video provider queue (dry-run by default)
                               │     status = video_queued
                               ▼
                     [Agent-Dispatcher] ─► tier's Instantly campaign
                                          ← NOT BUILT — step 2b
```

### How to run

```bash
cp data/inbox/sample_batch.json /tmp/batch.json   # keep a pristine copy first
python3 main.py --once      # one full pipeline pass, exit 0
python3 main.py --loop      # recurring background run (also the default, no flag)
python3 main.py --status    # row counts by status and tier, plus env var state
python3 -m unittest discover -s tests -v
```

What `--once` actually does on a fresh `leads.db` with `sample_batch.json`
(verified, not aspirational):

1. `[Agent-Scraper]` reads 9 records, 7 valid → **5 inserted**, 2 duplicate
   records skipped, 2 invalid records logged and skipped
   (`9 read, 7 valid, 5 inserted, 2 skipped`). The batch is then archived to
   `data/processed/`.
2. `[Agent-Classifier]` tiers all 5 (quick 1, scale 1, whitelabel 3) and moves
   them to `status=classified`.
3. `[Agent-Script]` writes **5 script files** to `data/scripts/` — one per lead
   id, `1.json`, `3.json`, `5.json`, `6.json`, `7.json` — and moves them to
   `status=scripted`.
4. `[Agent-Video]` queues **5 renders in dry-run** with `dryrun-…` video ids and
   `https://dryrun.invalid/videos/…` URLs, and moves them to
   `status=video_queued`. No provider call leaves the machine.

Final DB state after that pass: **5 rows, all `video_queued`** (quick 1, scale 1,
whitelabel 3). Re-running `--once` over the same batch logs
`9 read, 7 valid, 0 inserted, 7 skipped`, inserts **0** new rows, writes **no**
new scripts and queues **no** new videos — each stage reports there is nothing
to do (`no 'new' leads to classify`, `no 'classified' leads to script`,
`no 'scripted' leads to queue`).

* `--loop` runs a pass every `LOOP_SECONDS` seconds and shuts down cleanly on
  `SIGINT`/`SIGTERM`. A failing stage is logged and the loop continues.
* Startup logs a config summary: DB path, dry-run state, loop interval, active
  video provider, throttle window and which tier campaign env vars are set.
  **No secret value is ever logged.**

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `DB_PATH` | `./leads.db` | SQLite database file. |
| `DRY_RUN` | `true` | The go-live switch. Anything other than `true`/`1`/`yes`/`on`/empty asks for live mode. Dry-run is the default and the safe state. |
| `VIDEO_PROVIDER` | `dry-run` | `dry-run` (no network at all) or `heygen`. An unknown name raises at startup rather than silently falling back to dry-run. |
| `MOCK_MODE` | `false` | Hard offline switch carried over from the legacy app. `true` makes the live HeyGen provider refuse to run even when `DRY_RUN=false`. |
| `HEYGEN_API_KEY` | *(unset)* | HeyGen credential. **Secret** — reported as set/unset, never printed. |
| `INSTANTLY_API_KEY` | *(unset)* | Instantly credential. **Secret** — reported as set/unset, never printed. |
| `LOOP_SECONDS` | `300` | Seconds between passes in `--loop` mode. |
| `DATA_DIR` | `./data` | Parent of `inbox/`, `processed/`, `scripts/` and `queue.jsonl`. |
| `DEFAULT_LAWFUL_BASIS` | `owned opted-in list` | Recorded in `lead_sources.lawful_basis` for an input item that does not declare its own. |
| `INSTANTLY_CAMPAIGN_QUICK` | *(unset)* | Instantly campaign ID for the `quick` tier. **Recognised and reported, not yet consumed** — the dispatcher (step 2b) will be the first code to read it. |
| `INSTANTLY_CAMPAIGN_SCALE` | *(unset)* | Same, for the `scale` tier. |
| `INSTANTLY_CAMPAIGN_WHITELABEL` | *(unset)* | Same, for the `whitelabel` tier. |
| `TIER_PRICE_QUICK` / `TIER_PRICE_SCALE` / `TIER_PRICE_WHITELABEL` | *(unset)* | Optional price line in a generated script. **Leave unset.** No pricing exists for this business yet, so the scripts say nothing about price rather than inventing one. |
| `THROTTLE_MIN_SECONDS` | `2` | Lower bound of the randomised delay between consecutive **live** provider calls. |
| `THROTTLE_MAX_SECONDS` | `5` | Upper bound of that delay. |
| `THROTTLE_DELAY_SECONDS` | *(unset)* | Optional fixed override for the delay; `0` disables pacing entirely. Never applied in dry-run. |

Secrets are only ever reported as `set`/`unset` — by `--status`, by the startup
summary (`config.describe()` / `config.summary()`) and in this document, which
deliberately shows no example key values either. Put real credentials in the
environment or a local `.env` that is never committed.

### Dry-run is the default — and the go-live switch

The engine is dry-run unless `DRY_RUN` is explicitly set to a non-truthy value.
On top of that, a **live HeyGen render is refused unless all three hold**:

1. `DRY_RUN=false`, **and**
2. `HEYGEN_API_KEY` is set, **and**
3. `MOCK_MODE` is off.

If any of the three fails, the provider logs the refusal reason and the lead is
marked `failed` with that reason rather than being queued. `MOCK_MODE=true` is
the offline override: even with `DRY_RUN=false` and a key present, it keeps every
live provider call refused.

Even fully live, **this build sends no email to anyone** — outbound dispatch does
not exist yet (step 2b), so there is no path from a video to a contact.

### Script stage

`[Agent-Script]` reads every `classified` lead and writes one JSON script per
lead to `data/scripts/<lead_id>.json`, then moves the lead to `scripted`. Each
file holds `lead_id`, `email`, `tier`, `script`, `target_duration_seconds`,
`word_count`, `estimated_seconds` and `created_at`. Copy comes from per-tier
templates (quick ≈18s, scale ≈38s, whitelabel ≈52s), uses only facts present on
the lead, names no price unless a `TIER_PRICE_*` variable is set, and always
points at the opt-out link.

### Video queue stage

`[Agent-Video]` queues exactly one render per `scripted` lead through the single
provider interface in `agents/video_provider.py` — swapping providers changes
nothing in the agent. The dry-run provider returns a synthetic id and URL
immediately and touches no network; the HeyGen provider is the only live path and
obeys the three-part refusal rule above. Provider calls run off the event loop in
a worker thread. `scripted` → `video_queued`, or `failed` with the logged reason.

### Resilience helpers (`agents/util.py`)

Operators should expect these behaviours, both shared by every stage:

* **Retry ×3 with exponential backoff** on transient failures only (`1s → 2s →
  4s`, ±25% jitter, capped at 8s). Non-transient errors fail immediately.
* **2–5s randomised throttle** between consecutive **live** provider calls
  (bounds overridable via `THROTTLE_MIN_SECONDS` / `THROTTLE_MAX_SECONDS`, or a
  fixed value via `THROTTLE_DELAY_SECONDS`). It is a no-op in dry-run.
* A per-item failure is logged under its agent tag and never crashes the pass —
  the loop keeps going and the row is marked `failed`.

### `leads` schema

```sql
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    website TEXT UNIQUE,
    tier TEXT,
    video_id TEXT UNIQUE,
    video_url TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    updated_at TEXT   -- added by migration, see below
);
```

Indexes on `status`, `tier` and `video_id`.

* **`updated_at` and the idempotent migration:** the eight original columns are
  frozen; `updated_at` is added by an additive migration that runs at startup
  (`Database.migrate_schema()`), skipped when the column already exists, with
  existing rows backfilled from `created_at`. It is therefore **safe to upgrade
  an existing `leads.db` in place** — re-running is a no-op, nothing is dropped,
  renamed or reordered, and every status change refreshes the timestamp.
  `update_lead()` is the only writer and accepts `email`, `website`, `tier`,
  `video_id`, `video_url`, `status`.

Additive tables (**they never alter `leads`**):

```sql
CREATE TABLE IF NOT EXISTS lead_sources (
    lead_id INTEGER NOT NULL, source TEXT, lawful_basis TEXT, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppressions (
    email TEXT PRIMARY KEY, reason TEXT, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lead_firmographics (
    lead_id INTEGER PRIMARY KEY, company TEXT, employees REAL, revenue REAL,
    industry TEXT, added_at TEXT NOT NULL
);
```

`lead_firmographics` exists because tiering reads `employees`/`revenue`/
`industry`/`company`, and the `leads` table is frozen — so those inputs live in
their own additive table keyed by `lead_id`.

* **Dedupe:** email is lowercased and trimmed; website is lowercased with the
  scheme, `www.` and trailing slash stripped. Inserts use
  `ON CONFLICT DO NOTHING` and report inserted vs skipped counts.
* **Status lifecycle (step 2a):** `new` → `classified` → `scripted` →
  `video_queued`, plus `failed`. Dispatch states arrive with step 2b.
* **Tiers:** exactly `quick`, `scale` or `whitelabel`.

### Ingest, including CSV

The scraper reads local files only — it never fetches anything:

| Path | Format |
| --- | --- |
| `data/inbox/*.json` | A JSON array of lead objects (or an object with a `leads` array). |
| `data/inbox/*.csv` | The owner's opted-in export: header row required, column names case-insensitive and matched through an alias table. |
| `data/queue.jsonl` | One JSON object per line. Lives in `data/`, not `data/inbox/`. |

To ingest a CSV export, drop it in `data/inbox/` and run `--once`. Recognised
headers are `email` / `e-mail` / `email address`, `website` / `domain`,
`company` / `company name`, `employees`, `revenue`, `industry`, `source` and
`lawful_basis` / `lawful basis`; **unknown columns are ignored**, so an export
can carry extra data without breaking the import. `employees` and `revenue` may
be plain numbers or the usual `85` / `1.2k` / `6.5m` shorthand. Rows with an
invalid email or website are logged and skipped, never inserted.

Consumed files are moved to `data/processed/`, so the same batch is never
ingested twice; combined with the email/website dedupe, re-ingesting the same
export inserts zero rows. See [`data/inbox/README.md`](data/inbox/README.md).

### Tiering

Thresholds live in one block at the top of `agents/classifier_agent.py`:
`QUICK_MAX_EMPLOYEES = 19`, `QUICK_MAX_REVENUE = 1_000_000`,
`SCALE_MAX_EMPLOYEES = 199`, `SCALE_MAX_REVENUE = 25_000_000`, plus the
whitelabel/quick keyword lists. Decision order:

1. explicit `tier` on the input — always wins;
2. `whitelabel` — agency/reseller keyword, agency-shaped industry, or above
   either scale ceiling;
3. `quick` — employees and revenue both at/below their quick maxima;
4. `scale` — everything else.

The classifier is deterministic and logs its reason per lead.

### Log tags

`[Agent-Scraper]` (ingest), `[Agent-Classifier]` (tiering), `[Agent-Script]`
(per-tier script + persistence), `[Agent-Video]` (provider queueing),
`[LeadLoop]` (pipeline driver, config summary, loop lifecycle).
`[Agent-Dispatcher]` belongs to the unbuilt step 2b — the tag exists in the
conventions, but no code logs it yet.

### Compliance rule (non-negotiable)

Outbound must be lawful. This engine **never scrapes personal email addresses
off the open web** for bulk cold email — inputs are curated, opted-in or
otherwise lawfully obtained business contact lists, each item declaring a
`lawful_basis`. Every send checks the `suppressions` table first, copy is
truthful, and there is an easy opt-out. The only lead source is the owner's own
opted-in list, supplied as JSON or CSV in `data/inbox/`. See
[`data/inbox/README.md`](data/inbox/README.md).

### Repository conventions

See [`AGENTS.md`](AGENTS.md).
