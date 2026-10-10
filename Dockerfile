FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app:/app/agents

EXPOSE 8080

# Run background agents in background with &, keep uvicorn in FOREGROUND
CMD python agents/agent_x.py & \
    python agents/lead_hunter.py & \
    uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
