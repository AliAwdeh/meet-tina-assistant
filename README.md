# Meet Tina

Meet Tina is an operational AI personal assistant for WhatsApp. The backend is FastAPI + LangGraph + SQLAlchemy with PostgreSQL/Redis-ready deployment, OpenWA webhook ingestion, signed n8n callbacks, persistent scheduler jobs, server-side file storage, and a React dashboard.

## Local Backend

```bash
cp .env.example .env
conda activate langgraph
cd backend
pip install -e ".[dev]"
python ../scripts/seed.py
uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
```

The development default uses SQLite at `data/meet_tina.db` so the API can boot immediately. For production set `DATABASE_URL` to PostgreSQL and `REDIS_REQUIRED=true`.

## Dashboard

```bash
cd dashboard
npm install
npm run dev -- --host 0.0.0.0
```

The dashboard dev server runs on port `5174` to avoid the CRM frontend on `5173`.

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Services: `postgres`, `redis`, `api`, `worker`, `scheduler`, and `dashboard`.

## Important Endpoints

`GET /health`, `GET /health/live`, and `GET /health/ready`

`POST /webhooks/openwa` with `X-OpenWA-Token`

`POST /api/integrations/n8n/callback` with `X-Request-ID`, `X-Timestamp`, and `X-Signature`

## Security Notes

Use distinct secrets for dashboard JWTs, OpenWA, n8n callbacks, n8n outbound authorization, worker auth, and the AI endpoint. Do not expose secrets through the dashboard. Uploaded media is treated as untrusted and stored with generated UUID file names under `/data`.

## Server Smoke Run

On a small host without Docker, clone the repo and run only the API:

```bash
cd /opt/meet-tina-assistant/backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
PUBLIC_BASE_URL=https://claw.meettina.net COOKIE_SECURE=false python ../scripts/seed.py
PUBLIC_BASE_URL=https://claw.meettina.net COOKIE_SECURE=false uvicorn app.main:app --host 0.0.0.0 --port 5000
```

Install PostgreSQL, Redis, Docker, and a process manager before treating that host as production.
