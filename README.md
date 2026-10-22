# TraceForge Realtime — working full-stack MVP

This version replaces the static dashboard with a real FastAPI backend, SQLite persistence, a WebSocket event stream, and an interactive React frontend.

## What is actually real-time here?
- The browser opens a WebSocket at `/ws/live`.
- Clicking **Generate healthy trace** or **Inject fault** calls the backend API.
- The API writes generated spans to the database and broadcasts the new trace to connected browsers.
- The waterfall, service-health view, span inspector, and live event feed update from the event.
- `/api/ingest` accepts an individual span from another local service and broadcasts it to open dashboards.
- Trace records persist in the Docker volume.

The built-in trace generator is a controlled demo workload, not telemetry from a real external production system. To see your own app's real traces, instrument it and POST span data to `/api/ingest` or add an OpenTelemetry OTLP receiver/collector.

## Start with Docker (recommended)
Requirements: Docker Desktop / Docker Engine with Compose.

```bash
docker compose up --build
```

Then open:
- Frontend: http://localhost:5173
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

Click **Generate healthy trace** or **Inject fault**. Keep the dashboard open; it updates via WebSocket events. To stop: `Ctrl+C`, then `docker compose down`. To also remove persisted demo data: `docker compose down -v`.

## Run without Docker
Terminal 1:
```bash
cd backend
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Terminal 2:
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173. The frontend defaults to API `http://localhost:8000`.

## Ingest a span from another program
The API accepts JSON at `POST http://localhost:8000/api/ingest`:

```bash
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "checkout-demo-001",
    "span_id": "span-001",
    "parent_span_id": null,
    "service_name": "my-api",
    "operation_name": "POST /checkout",
    "duration_ms": 83.2,
    "status": "ok",
    "region": "local",
    "status_code": 200,
    "attributes": {"http.method": "POST", "http.route": "/checkout"},
    "logs": ["request accepted"],
    "events": ["request.completed"]
  }'
```

Use a unique `span_id` for each span; the current SQLite schema uses it as the primary key. In production, use globally unique span IDs and OpenTelemetry trace/span ID formats.

## API endpoints
- `GET /health` — health and active WebSocket client count
- `GET /api/traces?limit=50` — recent traces
- `GET /api/traces/{trace_id}` — retrieve spans for a trace
- `POST /api/demo/generate?fault=false` — create a healthy demo trace
- `POST /api/demo/generate?fault=true` — create a trace with a payment/gateway timeout
- `POST /api/ingest` — persist and broadcast an individual span
- `WS /ws/live` — real-time event stream

## Production hardening still needed
This is a local MVP, not a production observability service. Before deploying publicly, add authentication, tenant isolation, payload/rate limits, redaction for secrets and personal data, retention policies, deduplication, migrations, structured logging, tests, and an OpenTelemetry OTLP receiver. SQLite is fine for a demo; use PostgreSQL/ClickHouse or a trace backend for a multi-user deployment. The root-cause card is a rule-based hint, not an LLM or proven causal diagnosis.
