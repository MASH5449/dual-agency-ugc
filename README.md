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

## Apify prospect intake and review

Open `/prospects/review` and enter the review token. Once Apify is configured,
**Run Apify now** starts the selected Actor and imports its dataset directly;
the app also retains the manual JSON import option. The page displays the
company, website, published contacts, and sample products so each store can be
checked.

Imported records are stored separately in SQLite, deduplicated by website
domain and Apify record ID, and begin in `needs_review`. Approve or reject each
record in the review page. Approving a prospect with a valid published business
email automatically creates a personalized outreach draft for review. **It does
not send email, add the prospect to Instantly, or start video generation.**
Prospects without an eligible email can still be approved for creative planning.
The Actor's output must include `name` and `websiteUrl`; `id`, `contacts`, and
`sampleProducts` are optional. The Actor ID and input are explicit environment
settings because different Apify Actors have different input schemas.

Automatic runs are off by default. Set `APIFY_AUTO_RUN=true` only after a
successful manual test. The scheduler waits one full configured interval after
startup before its first automatic run. Configure only one Railway replica:
this scheduler is process-local, while prospect data is shared through SQLite.
Each run passes Apify's `maxTotalChargeUsd`, run timeout, and `maxItems` limits;
`maxItems` is a charge limit for pay-per-result Actors, not a guarantee that
the Actor itself stops producing results. The dataset fetch is separately
limited to `APIFY_MAX_ITEMS` (at most 200 records).

Run status is saved in the prospects database. If the server restarts or loses
contact after submission, the app blocks another run because Apify may still
be processing or may already have charged for the run. Check the run in Apify
first, then use **I've checked Apify; allow another run** in the review page.
This guard also applies after an uncertain run-status or dataset request.

## Approval-gated Instantly outreach

The prospect review page creates one personalized plain-text email per approved
prospect, using the imported company and product names, immediately when you
approve the prospect. The owner can edit the subject and body. Drafting and
editing do not send anything; the owner must click **Approve & send with
Instantly** for that specific email. Previously approved prospects with no
saved draft can use the retry-draft action.

Sending creates one lead in the configured Instantly campaign with
`outreach_subject` and `outreach_body` custom variables. Configure that campaign
as a single email step whose subject and body use `{{outreach_subject}}` and
`{{outreach_body}}`. The campaign uses the sending account, signature, schedule,
and unsubscribe settings configured in Instantly; enrollment is not a guarantee
that delivery has already occurred. Set the sender to the intended business
account (for example, `alex@useleadloop.xyz`) and verify the campaign preview
and unsubscribe/footer settings before enabling live sends. Do not attach a
multi-step follow-up sequence to this campaign.

`ENABLE_OUTREACH` defaults to off. Keep it off until the one-step campaign has
been configured and tested with an internal address. Live sending additionally
requires `INSTANTLY_API_KEY` and the separate
`INSTANTLY_OUTREACH_CAMPAIGN_ID`; `INSTANTLY_CAMPAIGN_ID` remains reserved for
the legacy paid-customer pause integration. Each prospect can be enrolled only
once from this app. A network timeout or Instantly server error is marked
`submission_unknown`; check Instantly before taking further action, to avoid
duplicate outreach. An owner can suppress an address from future sends via the
review page. This app does not yet synchronize replies or suppressions back to
Instantly automatically.

Emails are generated from supplied prospect data, not verified claims about the
brand. Review contact appropriateness and all copy before sending.

## Creative testing workflow

Open `/creative-lab` to create a campaign brief for a brand and product, record
the goal, audience, offer, test hypothesis, and primary metric, then save
multiple ad concepts with a hook, script/outline, channel, and duration. Each
campaign also has an explicit action to generate 1–10 concept and spoken-script
drafts from its brief using the configured OpenAI key. The generator uses only
the supplied brief, avoids inventing product facts or skincare outcomes, and
saves results as drafts for human review; verify all copy before approval.
Approved prospects from `/prospects/review` can be selected to prefill the
brand and product details; campaign plans can also be entered manually.

Each concept follows a deliberate manual path:

`draft → approved → produced → testing → completed`

Draft concepts can be edited in the lab and may instead be rejected. Editing is
locked after approval. Approval only means approved for production; it does not
automatically create a video or publish an ad. For an approved concept, the
owner can explicitly request one HeyGen render at a time. The app uses one
available HeyGen avatar look and its default voice; the current Creative Lab
render path does not yet call ElevenLabs. Renders may incur HeyGen charges.
Connection failures that leave submission uncertain block automatic retry so
the owner can check HeyGen first. The configured HeyGen webhook should point to
`https://<your-app-domain>/webhook/heygen`; completed videos appear for review.
The owner must approve or reject each finished video. Approval attaches it to
the concept as produced; rejection allows a deliberate new render request.
Rendering does not send email or publish ads. Marking a concept testing is an
explicit record that the owner launched it outside this app. Results are
entered manually from the ad platform by date range. The page calculates CTR,
cost per conversion, and ROAS from those entered values; they are not
independently verified. There is no connection here to an advertising account,
no automatic campaign launch, and no performance guarantee.

An approved concept can be turned into one saved production handoff using
OpenAI's lower-cost `gpt-4o-mini` model with a 1,500-token output cap. It
segments the approved script into timed shots and includes practical visual
directions and a producer checklist; server validation requires the spoken
copy to reproduce the approved script exactly. The request is made only when
the owner clicks the handoff button. It does not request a video render or
publish anything. OpenAI usage may incur a small variable API charge.

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
| `OPENAI_API_KEY` | Required to generate campaign concept and script drafts. |
| `HEYGEN_API_KEY` | Required for explicit one-at-a-time Creative Lab video renders. |
| `ENABLE_OUTREACH` | Defaults to `false`; set to `true` only after reviewing the dedicated Instantly one-step campaign configuration. |
| `INSTANTLY_OUTREACH_CAMPAIGN_ID` | ID of the dedicated one-step outreach campaign; does not use the legacy customer-pause campaign ID. |
| `APIFY_API_TOKEN` | Apify API token; never exposed by the review API. |
| `APIFY_ACTOR_ID` | Defaults to the selected Shopify Store Leads Actor; override with an Actor ID or `username~actor-name`. |
| `APIFY_ACTOR_INPUT_JSON` | Optional JSON object override. Default pilot searches skincare in the Beauty category in the US, requests 25 stores, enables duplicate-history skipping, and leaves website enrichment off. |
| `APIFY_AUTO_RUN` | Defaults to `false`; enables scheduled runs only after valid Apify configuration is present. |
| `APIFY_INTERVAL_SECONDS` | Scheduled run interval; defaults to 86400 and must be at least 300 seconds. |
| `APIFY_MAX_CHARGE_USD` | Maximum Apify charge per run; defaults to $5.00. |
| `APIFY_MAX_ITEMS` | Maximum dataset records imported per run; defaults to 25 and cannot exceed 200. |
| `APIFY_RUN_TIMEOUT_SECONDS` | Actor run timeout; defaults to 1800 seconds and must be at least 30. |
| `APIFY_POLL_INTERVAL_SECONDS` | Actor status polling interval; defaults to 10 seconds. |

For data to survive Railway restarts and redeploys, attach a Railway Volume
mounted at `/data` and set `PROSPECTS_DB_PATH=/data/prospects.sqlite3`. Without
a persistent volume, the database is local to the container and can be lost.
The import accepts a JSON list or an object with an `items` list, up to 200
records and 5 MB per request. It imports published business contact fields as
source data, not as verified decision-maker addresses or permission to contact.
The same token protects the Apify run controls, prospect review, and creative
campaign and results APIs.

## Env

| Variable | Role |
| --- | --- |
| `HEYGEN_API_KEY` | Legacy fulfillment renders and explicit Creative Lab renders. |
| `OPENAI_API_KEY` | Existing webhook scripts and on-demand Creative Lab concept generation. |
| `STRIPE_WEBHOOK_SECRET` | When set, `/webhook/stripe` rejects a bad `Stripe-Signature`. Unset = accepted, and a warning is logged. |
| `INSTANTLY_API_KEY` / `INSTANTLY_CAMPAIGN_ID` | Legacy integration: pause the buyer's lead after payment. |
| `INSTANTLY_OUTREACH_CAMPAIGN_ID` / `ENABLE_OUTREACH` | Approval-gated prospect enrollment; off by default. |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` | Delivery mail. Default host is `smtp.porkbun.com:587`. |
| `PROSPECT_REVIEW_TOKEN` | Enables authenticated prospect review and import APIs when set. |
| `PROSPECTS_DB_PATH` | Prospect SQLite database location. Use a Railway Volume for persistence. |

Secrets stay in the environment. Do not commit a `.env`.

## What is not this app

`archive/stdlib-engine/` is the other pipeline (SQLite, dry-run video queue, `Pipeline`). It is not imported by `main.py`, and its tests expect a `Pipeline` class this file does not have.

`archive/loose-scripts/` is the extra agent files Docker used to launch beside uvicorn (`agent_x.py`, `lead_hunter.py`, Apollo, Arcads, and the old webhook module). They are not part of the server.
