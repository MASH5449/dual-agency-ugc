# dual-agency-ugc

The running app is FastAPI in `main.py`. Docker starts `uvicorn main:app` and nothing else.

Do not put a second entrypoint at the repo root. The stdlib pipeline and the loose agent scripts live under `archive/` because they are not what the server imports.

Outreach does not start unless `ENABLE_OUTREACH=true`. Do not fabricate `@twitter.com` addresses or send a pitch that has not been approved.

HeyGen creates videos with a look id from `GET /v3/avatars/looks` and that look's `default_voice_id`. Do not post a group id from `GET /v3/avatars`, and do not pin a voice the look does not own.

Scripts may only use facts from the scraped page. No invented ingredients, reviews, or prices.
