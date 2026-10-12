# Archived stdlib engine

This is the other LeadLoop pipeline: SQLite ingest, tiering (`quick` / `scale` / `whitelabel`), script files, and a dry-run video queue.

It is not the running app. `main.py` at the repo root is FastAPI and does not define `Pipeline`. The tests in this folder still do `from main import Pipeline`, so they fail against the server. They are kept here so the work is not thrown away.

Nothing in this folder is imported by the FastAPI app.
