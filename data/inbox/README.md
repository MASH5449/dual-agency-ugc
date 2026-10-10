# LeadLoop inbox — ingest file format

Drop lead batches here. The scraper agent consumes them on every pipeline pass
and moves each file to `../processed/`, so a batch is ingested once.

## Files the scraper reads

| Path | Purpose |
| --- | --- |
| `data/inbox/*.json` | A JSON **array** of lead objects (or an object with a `leads` array). |
| `data/queue.jsonl` | One JSON object per line. Lives in `data/`, not `data/inbox/`. |

`data/inbox/README.md` itself is ignored — only `*.json` is read.

## Lead object

```json
{
  "email": "jane.doe@example.com",
  "website": "https://www.example.com/",
  "company": "Example Ltd",
  "employees": 42,
  "revenue": 3500000,
  "industry": "Consumer electronics",
  "tier": "scale",
  "source": "partner_list_2026_03",
  "lawful_basis": "opted in via webinar signup, consent recorded 2026-02-11"
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `email` | **yes** | Normalised to lowercase + trimmed, then regex-validated. Invalid items are logged and skipped, never inserted. |
| `website` | no | Normalised (scheme, `www.` and trailing slash stripped) before dedupe. Invalid values are logged and skipped. |
| `company` | no | Used as a tiering keyword signal. |
| `employees` | no | Number, or `"85"` / `"1.2k"`. Empty means unknown. |
| `revenue` | no | Annual revenue, whole units. Number or `"6.5m"`. |
| `industry` | no | Free text; agency/marketing industries tier toward `whitelabel`. |
| `tier` | no | `quick` / `scale` / `whitelabel`. An explicit value **always wins** over the classifier. |
| `source` | no | Provenance label written to `lead_sources.source`. |
| `lawful_basis` | no | Why we are permitted to contact this lead. Written to `lead_sources.lawful_basis`. |

## Compliance rules for this directory

* **Do not scrape personal email addresses off the open web** for bulk cold
  email. That is a hard constraint of this project, not a technical limitation
  to work around.
* Every batch should come from an opted-in list, or from public business
  contact data used lawfully in the relevant market.
* Populate `source` and `lawful_basis` on every item. Missing values are stored
  as `"unknown"` / `"unspecified"` and are treated as a red flag.
* Opt-outs belong in the `suppressions` table, not here — a suppressed email is
  never inserted and never classified.
* The engine is dry-run by default. Nothing is sent anywhere in step 1.

## Dedupe

Email is unique (lowercased, trimmed). Website is unique (lowercased, scheme,
`www.` and trailing slash stripped). Re-ingesting the same batch inserts zero
rows — `data/inbox/sample_batch.json` exists to prove exactly that.

## Sample batch

`sample_batch.json` contains nine fictional records (`*.example.com` only, no
real contacts) covering all three tiers, two deliberate duplicates (one email
differing only in case, one website differing only by scheme/`www.`/slash) and
two invalid records. Expected result: **5 inserted, 2 duplicates skipped, 2
invalid skipped.**
