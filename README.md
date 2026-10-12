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

`GET /` is the health check. `GET /leads` is the in-memory lead list. `GET /api/stream` is the log stream.

## Env

| Variable | Role |
| --- | --- |
| `HEYGEN_API_KEY` | Renders. Unset = no video is requested. |
| `OPENAI_API_KEY` | Scripts. Unset = a short fallback line, no invented ingredients. |
| `STRIPE_WEBHOOK_SECRET` | When set, `/webhook/stripe` rejects a bad `Stripe-Signature`. Unset = accepted, and a warning is logged. |
| `INSTANTLY_API_KEY` / `INSTANTLY_CAMPAIGN_ID` | Pause the buyer after payment. |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` | Delivery mail. Default host is `smtp.porkbun.com:587`. |
| `ENABLE_OUTREACH` | Off unless `true`. Agent X and the lead hunter do not start on boot. |
| `X_BEARER_TOKEN` | Only read when outreach is on. |

Secrets stay in the environment. Do not commit a `.env`.

## What is not this app

`archive/stdlib-engine/` is the other pipeline (SQLite, dry-run video queue, `Pipeline`). It is not imported by `main.py`, and its tests expect a `Pipeline` class this file does not have.

`archive/loose-scripts/` is the extra agent files Docker used to launch beside uvicorn (`agent_x.py`, `lead_hunter.py`, Apollo, Arcads, and the old webhook module). They are not part of the server.
