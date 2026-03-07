from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from models import EmailImport, SubscriptionResponse, SpendingSummary, AlertResponse
from extractor import (
    init_db, import_emails, list_subscriptions,
    get_spending_summary, list_alerts, mark_alert_read,
)

DB_PATH = "subtrack.db"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await init_db(DB_PATH)
    yield
    await app.state.db.close()


app = FastAPI(
    title="SubTrack",
    description="AI-powered subscription tracker. Paste billing emails, get a full picture of your recurring spend. Alerts on price increases — no bank connection needed.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.post("/subscriptions/import", response_model=list[SubscriptionResponse])
async def import_billing_emails(body: EmailImport):
    """
    Scan raw email bodies for subscription billing info.
    AI extracts: service name, amount, cycle, category, status.
    Detects price changes and creates alerts automatically.
    """
    results = await import_emails(app.state.db, body.emails)
    if not results:
        raise HTTPException(422, "No subscription data found in provided emails")
    return results


@app.get("/subscriptions", response_model=list[SubscriptionResponse])
async def index_subscriptions(
    status: str | None = Query(None, description="Filter: active, trial, cancelled, paused"),
    category: str | None = Query(None, description="Filter: saas, streaming, cloud, utilities, marketing, finance, other"),
):
    """List all detected subscriptions with optional filters."""
    return await list_subscriptions(app.state.db, status, category)


@app.get("/spending/summary", response_model=SpendingSummary)
async def spending_summary():
    """
    Aggregated spend analytics: monthly/yearly totals, breakdown by category,
    active vs trial count, top subscriptions by cost.
    """
    return await get_spending_summary(app.state.db)


@app.get("/alerts", response_model=list[AlertResponse])
async def get_alerts(unread_only: bool = Query(False)):
    """Price increase alerts and anomaly notifications."""
    return await list_alerts(app.state.db, unread_only)


@app.post("/alerts/{alert_id}/read")
async def read_alert(alert_id: int):
    """Mark an alert as read."""
    ok = await mark_alert_read(app.state.db, alert_id)
    if not ok:
        raise HTTPException(404, "Alert not found")
    return {"status": "ok"}
