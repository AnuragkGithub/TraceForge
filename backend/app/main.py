from __future__ import annotations
import asyncio, json, os, random, time, uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, Float, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./traceforge.db")
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase): pass

class TraceSpan(Base):
    __tablename__ = "spans"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    operation: Mapped[str] = mapped_column(String(256))
    start_time: Mapped[float] = mapped_column(Float)
    duration_ms: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), index=True)
    region: Mapped[str] = mapped_column(String(64), default="local")
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, default="{}")
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    events_json: Mapped[str] = mapped_column(Text, default="[]")

Base.metadata.create_all(engine)
app = FastAPI(title="TraceForge Realtime API", version="1.0.0")
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

clients: set[WebSocket] = set()
latest_events: list[dict[str, Any]] = []
DEMO_SERVICES = ["api-gateway", "checkout-service", "payment-service", "inventory-service", "pricing-service", "bank-gateway", "notification-service"]
REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "eu-central-1"]

def serialize(s: TraceSpan) -> dict[str, Any]:
    return {
        "id": s.id, "traceId": s.trace_id, "parent": s.parent_id,
        "service": s.service, "operation": s.operation, "start": float(json.loads(s.tags_json).get("demo.start_ms", 0)),
        "duration": round(s.duration_ms, 1), "status": s.status, "region": s.region,
        "statusCode": s.status_code, "error": s.error,
        "tags": json.loads(s.tags_json), "logs": json.loads(s.logs_json), "events": json.loads(s.events_json),
        "kind": "server", "method": "POST", "url": "/demo"
    }

async def broadcast(message: dict[str, Any]):
    dead = []
    for ws in list(clients):
        try: await ws.send_json(message)
        except Exception: dead.append(ws)
    for ws in dead: clients.discard(ws)

def add_span(db, *, trace_id: str, parent_id: str | None, service: str, operation: str,
             start: float, duration: float, status: str, region: str, status_code: int,
             error: str | None = None, tags: dict | None = None, logs: list | None = None, events: list | None = None):
    s = TraceSpan(id=uuid.uuid4().hex[:8], trace_id=trace_id, parent_id=parent_id, service=service,
        operation=operation, start_time=start, duration_ms=duration, status=status, region=region,
        status_code=status_code, error=error, tags_json=json.dumps(tags or {}),
        logs_json=json.dumps(logs or []), events_json=json.dumps(events or []))
    db.add(s); db.commit(); db.refresh(s)
    return s

def create_demo_trace(fault: bool = False):
    trace_id = uuid.uuid4().hex
    now = time.time()
    specs = [
        ("api-gateway", "POST /checkout", 0, random.randint(18, 50), "ok", "us-east-1", 200, None),
        ("auth-service", "Validate Token", 25, random.randint(8, 30), "ok", "us-east-1", 200, None),
        ("checkout-service", "Create Checkout", 55, random.randint(45, 110), "ok", "us-east-1", 202, None),
        ("inventory-service", "Check Availability", 80, random.randint(20, 70), "ok", "us-west-2", 200, None),
        ("pricing-service", "Calculate Price", 100, random.randint(12, 45), "ok", "us-west-2", 200, None),
    ]
    if fault:
        specs += [
            ("payment-service", "Charge Payment", 140, random.randint(900, 1500), "error", "eu-west-1", 500, "Payment gateway timeout"),
            ("bank-gateway", "Process Charge", 180, random.randint(800, 1300), "error", "eu-central-1", 504, "Upstream gateway timeout"),
        ]
    else:
        specs += [
            ("payment-service", "Charge Payment", 140, random.randint(50, 140), "ok", "eu-west-1", 200, None),
            ("bank-gateway", "Process Charge", 165, random.randint(20, 80), "ok", "eu-central-1", 200, None),
        ]
    specs += [("notification-service", "Queue Confirmation", 260, random.randint(10, 35), "ok", "us-east-1", 202, None)]
    created = []
    db = SessionLocal()
    try:
        root = None
        checkout = None
        payment = None
        for service, op, start, dur, status, region, code, error in specs:
            parent = None
            if service != "api-gateway":
                parent = payment if service == "bank-gateway" else checkout if service in ("inventory-service","pricing-service","payment-service") else root
            span = add_span(db, trace_id=trace_id, parent_id=parent, service=service, operation=op,
                start=now + start/1000, duration=dur, status=status, region=region, status_code=code, error=error,
                tags={"http.status_code": str(code), "service.name": service, "demo.generated": "true", "demo.start_ms": str(start)},
                logs=([error] if error else [f"{op} completed"]), events=["span.exception"] if error else ["span.completed"])
            if service == "api-gateway": root = span.id
            if service == "checkout-service": checkout = span.id
            if service == "payment-service": payment = span.id
            created.append(serialize(span))
    finally: db.close()
    return {"type":"trace_created", "traceId":trace_id, "createdAt":datetime.now(timezone.utc).isoformat(), "spans":created, "fault":fault}

class SpanIn(BaseModel):
    trace_id: str = Field(min_length=1, max_length=64)
    span_id: str = Field(min_length=1, max_length=64)
    parent_span_id: str | None = None
    service_name: str
    operation_name: str
    start_time_unix_nano: int | None = None
    duration_ms: float = Field(gt=0, le=600000)
    status: str = "ok"
    region: str = "local"
    status_code: int = 200
    error: str | None = None
    attributes: dict[str, Any] = {}
    logs: list[str] = []
    events: list[str] = []

@app.get("/health")
def health():
    return {"status":"ok", "service":"traceforge-api", "websocket_clients":len(clients)}

@app.get("/api/traces")
def get_traces(limit: int = 50):
    db = SessionLocal()
    try:
        rows = db.query(TraceSpan).order_by(TraceSpan.start_time.desc()).limit(min(max(limit,1),500)).all()
        by_trace: dict[str,list] = {}
        for s in rows: by_trace.setdefault(s.trace_id, []).append(serialize(s))
        return [{"traceId": tid, "spanCount":len(ss), "errorCount":sum(s["status"]=="error" for s in ss),
                 "startTime":min(s["start"] for s in ss), "duration":max(s["start"]+s["duration"] for s in ss)-min(s["start"] for s in ss),
                 "spans":ss} for tid,ss in by_trace.items()]
    finally: db.close()

@app.get("/api/traces/{trace_id}")
def get_trace(trace_id: str):
    db = SessionLocal()
    try:
        rows = db.query(TraceSpan).filter(TraceSpan.trace_id==trace_id).order_by(TraceSpan.start_time.asc()).all()
        if not rows: raise HTTPException(404, "Trace not found")
        return {"traceId":trace_id,"spans":[serialize(s) for s in rows]}
    finally: db.close()

@app.post("/api/demo/generate")
async def generate_demo(fault: bool = False):
    event = create_demo_trace(fault)
    latest_events.append(event)
    if len(latest_events)>100: latest_events.pop(0)
    await broadcast(event)
    return event

@app.post("/api/ingest")
async def ingest_span(payload: SpanIn):
    db = SessionLocal()
    try:
        s = add_span(db, trace_id=payload.trace_id, parent_id=payload.parent_span_id,
            service=payload.service_name, operation=payload.operation_name,
            start=(payload.start_time_unix_nano/1e6 if payload.start_time_unix_nano else time.time()*1000),
            duration=payload.duration_ms, status="error" if payload.error or payload.status.lower()=="error" else payload.status.lower(),
            region=payload.region, status_code=payload.status_code, error=payload.error,
            tags=payload.attributes, logs=payload.logs, events=payload.events)
        event={"type":"span_ingested","traceId":payload.trace_id,"span":serialize(s)}
        await broadcast(event)
        return {"accepted":True,"span":serialize(s)}
    finally: db.close()

@app.websocket("/ws/live")
async def live(websocket: WebSocket):
    await websocket.accept(); clients.add(websocket)
    try:
        await websocket.send_json({"type":"connected","at":datetime.now(timezone.utc).isoformat()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
