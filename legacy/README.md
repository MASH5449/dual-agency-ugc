# Legacy app — FastAPI / Stripe webhooks (reference only)

`fastapi_webhooks.py` is the **previous** application, moved here verbatim when
the LeadLoop engine replaced it. It is kept for reference only.

## What it was

A FastAPI service that handled the old agency/UGC checkout flow:

* `POST /webhook-quick`, `/webhook-scale`, `/webhook-whitelabel` — Stripe
  checkout-session completion handlers (one per price tier).
* `POST /webhook-heygen` — HeyGen video-completion callback.
* An in-memory `JOBS_DB` dict correlating checkout sessions with rendering jobs.

It relied on `video_pipeline.py` (HeyGen v3 render trigger) and
`instantly_pipeline.py` (Instantly v1 lead add), which are still present at the
repository root and are also legacy. `agents/lead_hunter.py`,
`agents/instantly_agent.py`, `agents/arcads_agent.py`, `agents/apollo_agent.py`,
`agents/agent_x.py` and `agents/stripe_handler.py` are the legacy agents.

## Why it is not running

It needs third-party packages this machine cannot install. Per `requirements.txt`:
`fastapi`, `uvicorn`, `stripe`, `tweepy`, `requests`, `python-dotenv`,
`apscheduler`, `beautifulsoup4`, `openai`, `httpx`, `pydantic`, `playwright`.

This environment has ~290 MB of free disk and no third-party Python packages;
installing them (playwright in particular) would fill the disk. Do not run
`pip install`, and do not add to `requirements.txt`.

`Dockerfile` still describes the legacy container and is left untouched.

## What replaced it

The LeadLoop engine at the repository root — `main.py`, `config.py`, `db.py`
and `agents/{base,scraper_agent,classifier_agent}.py` — is stdlib-only and runs
here. See the "LeadLoop engine (step 1)" section of the root `README.md`.

Nothing in this directory is imported by the new engine, and this file is
deleted only if the owner explicitly drops the legacy app.
