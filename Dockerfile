Dockerfile
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure Python treats /app and /app/agent as import search paths
ENV PYTHONPATH=/app:/app/agent

EXPOSE 8080

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} & \
    python agent/agent_x.py & \
    python agent/lead_hunter.py
