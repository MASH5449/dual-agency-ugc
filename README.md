# dual-agency-ugc

FastAPI app in `main.py`. That is the process Docker runs.

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

## What it does

1. `POST /webhook/stripe` (also `/webhook-stripe`) reads a paid Checkout session.
2. Amount picks the batch: under $300 is Starter (1 video), $300–$799 is Growth (10), $800 and up is Enterprise (30).
3. The lead is paused in Instantly, the brand URL is scraped, and a spoken script is written.
4. HeyGen v3 renders a 9:16 avatar video. The look id comes from `GET /v3/avatars/looks`, and the voice is that look's `default_voice_id`.
5. `POST /webhook/heygen` emails the MP4 when the render finishes.

`GET /` is the health check. `GET /leads` is the legacy in-memory list.
`GET /api/stream` is the log stream.

## Apify prospect review

The deployed app can import the JSON dataset export from the tested Apify
Shopify Store Leads actor. Open `/prospects/review`, enter the review token,
choose the downloaded JSON file, then import it. The page displays the company,
website, published contacts, and sample products so each store can be checked.

Imported records are stored separately in SQLite, deduplicated by website
domain and Apify record ID, and begin in `needs_review`. Approve or reject each
record in the review page. **Approval does not send email, add the prospect to
Instantly, or start video generation.** This first build milestone is only
source-data intake and human qualification; automated discovery and dispatch
remain separate steps.

## Creative testing workflow

Open `/creative-lab` to create a campaign brief for a brand and product, record
the goal, audience, offer, test hypothesis, and primary metric, then save
multiple ad concepts with a hook, script/outline, channel, and duration.
Approved prospects from `/prospects/review` can be selected to prefill the
brand and product details; campaign plans can also be entered manually.

Each concept follows a deliberate manual path:

`draft → approved → produced → testing → completed`

Draft concepts may instead be rejected. Approval only means approved for
production; it does not create a video or publish an ad. Marking a concept
produced requires a video URL, and marking it testing is an explicit record
that the owner launched it outside this app. Results are entered manually from
the ad platform by date range. The page calculates CTR, cost per conversion,
and ROAS from those entered values; they are not independently verified.
There is no connection here to an advertising account, no automatic campaign
launch, and no performance guarantee.

The campaign tables use the same SQLite file as prospects by default. If using
Railway, mount persistent storage at `/data` and configure
`PROSPECTS_DB_PATH=/data/prospects.sqlite3` (or set `CREATIVE_DB_PATH` to the
same mounted database file).

Configure these Railway variables before using the review page:

| Variable | Role |
| --- | --- |
| `PROSPECT_REVIEW_TOKEN` | Required bearer token for importing, listing, and reviewing prospects. If unset, those API actions return 503. |
| `PROSPECTS_DB_PATH` | SQLite file path; defaults to `data/prospects.sqlite3`. |
| `CREATIVE_DB_PATH` | Optional database path for creative plans; defaults to `PROSPECTS_DB_PATH`. |

For data to survive Railway restarts and redeploys, attach a Railway Volume
mounted at `/data` and set `PROSPECTS_DB_PATH=/data/prospects.sqlite3`. Without
a persistent volume, the database is local to the container and can be lost.
The import accepts a JSON list or an object with an `items` list, up to 200
records and 5 MB per request. It imports published business contact fields as
source data, not as verified decision-maker addresses or permission to contact.
The same token protects the creative campaign and results APIs.

## Env

| Variable | Role |
| --- | --- |
| `HEYGEN_API_KEY` | Renders. Unset = no video is requested. |
| `OPENAI_API_KEY` | Scripts. Unset = a short fallback line, no invented ingredients. |
| `STRIPE_WEBHOOK_SECRET` | When set, `/webhook/stripe` rejects a bad `Stripe-Signature`. Unset = accepted, and a warning is logged. |
| `INSTANTLY_API_KEY` / `INSTANTLY_CAMPAIGN_ID` | Pause the buyer after payment. |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` | Delivery mail. Default host is `smtp.porkbun.com:587`. |
| `PROSPECT_REVIEW_TOKEN` | Enables authenticated prospect review and import APIs when set. |
| `PROSPECTS_DB_PATH` | Prospect SQLite database location. Use a Railway Volume for persistence. |

Secrets stay in the environment. Do not commit a `.env`.

## What is not this app

`archive/stdlib-engine/` is the other pipeline (SQLite, dry-run video queue, `Pipeline`). It is not imported by `main.py`, and its tests expect a `Pipeline` class this file does not have.

`archive/loose-scripts/` is the extra agent files Docker used to launch beside uvicorn (`agent_x.py`, `lead_hunter.py`, Apollo, Arcads, and the old webhook module). They are not part of the server.
