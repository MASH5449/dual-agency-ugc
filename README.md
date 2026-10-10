# dual-agency-ugc

UGC video ad agency: an autonomous lead engine plus the earlier webhook app.

* **LeadLoop engine** (current) — stdlib-only Python pipeline at the repository
  root: `main.py`, `config.py`, `db.py`, `agents/`.
* **Legacy FastAPI app** (reference only) — `legacy/fastapi_webhooks.py`, plus
  `video_pipeline.py`, `instantly_pipeline.py` and the older files in `agents/`.
  It needs third-party packages this machine cannot install. See
  [`legacy/README.md`](legacy/README.md). Nothing in it is imported by LeadLoop.

---

## LeadLoop engine (step 1)

An autonomous background engine that ingests target companies, assigns each one
a tier, and persists everything in SQLite with hard dedupe. **Step 1 covers
ingest + tiering + the runtime loop.** Step 2 (script generation, video
generation, dispatch) is not built yet and is not stubbed.

```
data/inbox/*.json ──┐
                    ├─► [Agent-Scraper] ─► leads (SQLite, deduped)
data/queue.jsonl ───┘          │
                               ▼
                     [Agent-Classifier] ─► tier = quick | scale | whitelabel
                                            status = classified
```

### How to run

```bash
cp data/inbox/sample_batch.json /tmp/batch.json   # keep a pristine copy first
python3 main.py --once      # one full pipeline pass, exit 0
python3 main.py --loop      # recurring background run (also the default, no flag)
python3 main.py --status    # row counts by status and tier, plus env var state
python3 -m unittest discover -s tests -v
```

`--once` on a fresh `leads.db` with the sample batch inserts 5 lead rows (2
duplicate records and 2 invalid records are skipped) and logs every stage.
Running it again over the same input inserts **0** new rows.

* `--loop` runs a pass every `LOOP_SECONDS` seconds and shuts down cleanly on
  `SIGINT`/`SIGTERM`. A failing stage is logged and the loop continues.
* Startup logs a config summary: DB path, dry-run state, loop interval and which
  tier campaign env vars are set. **No secret value is ever logged.**

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `DB_PATH` | `./leads.db` | SQLite database file. |
| `DRY_RUN` | `true` | Anything other than `true`/`1`/`yes`/`on` requests live mode. Step 1 sends nothing either way. |
| `LOOP_SECONDS` | `300` | Seconds between passes in `--loop` mode. |
| `DATA_DIR` | `./data` | Parent of `inbox/`, `processed/` and `queue.jsonl`. |
| `INSTANTLY_CAMPAIGN_QUICK` | *(unset)* | Instantly campaign ID for the `quick` tier (step 2). |
| `INSTANTLY_CAMPAIGN_SCALE` | *(unset)* | Instantly campaign ID for the `scale` tier (step 2). |
| `INSTANTLY_CAMPAIGN_WHITELABEL` | *(unset)* | Instantly campaign ID for the `whitelabel` tier (step 2). |
| `INSTANTLY_API_KEY` | *(unset)* | Instantly API key. Secret — never logged. |
| `HEYGEN_API_KEY` | *(unset)* | HeyGen API key. Secret — never logged. |

### Dry-run default

The engine is dry-run unless `DRY_RUN` is explicitly set to a non-truthy value,
and step 1 performs no network calls at all. Live outbound additionally needs
real source credentials, which are not in this repository.

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
    created_at TEXT NOT NULL
);
```

Indexes on `status`, `tier` and `video_id`.

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
* **Status lifecycle (step 1):** `new` → `classified`, plus `failed`.
* **Tiers:** exactly `quick`, `scale` or `whitelabel`.

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

`[Agent-Scraper]` (ingest), `[Agent-Classifier]` (tiering),
`[LeadLoop]` (pipeline driver, config summary, loop lifecycle).
`[Agent-Script]` and `[Agent-Dispatcher]` arrive in step 2.

### Compliance rule (non-negotiable)

Outbound must be lawful. This engine **never scrapes personal email addresses
off the open web** for bulk cold email — inputs are curated, opted-in or
otherwise lawfully obtained business contact lists, each item declaring a
`lawful_basis`. Every send checks the `suppressions` table first, copy is
truthful, and there is an easy opt-out. See `data/inbox/README.md`.

### Repository conventions

See [`AGENTS.md`](AGENTS.md).
