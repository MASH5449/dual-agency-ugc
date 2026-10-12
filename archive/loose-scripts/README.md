# Archived loose scripts

These files are not imported by `main.py`.

Docker used to start `agent_x.py` and `lead_hunter.py` in the background next to uvicorn. The same jobs also lived inside `main.py`, so a boot could run them twice. They are not started anymore.

`legacy/` is an older webhook module. `video_pipeline.py` and `instantly_pipeline.py` were the helpers that module called.

Agent X in `main.py` only runs when `ENABLE_OUTREACH=true`.
