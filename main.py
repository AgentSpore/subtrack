from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from models import EmailImport, SubscriptionResponse, SpendingSummary, AlertResponse
from extractor import (
    init_db, import_emails, list_subscriptions,
    get_spending_summary, list_alerts, mark_alert_read,
    delete_subscription, list_upcoming,
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
    version="0.4.0",
    lifespan=lifespan,
)


@app.post("/subscriptions/import", response_model=list[SubscriptionResponse])
async def import_billing_emails(body: EmailImport):
    """Scan raw email bodies for subscription billing info."""
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


@app.get("/subscriptions/upcoming", response_model=list[SubscriptionResponse])
async def upcoming_subscriptions(
    days: int = Query(7, ge=1, le=90, description="Days ahead to look"),
):
    """List active subscriptions renewing within the next N days (default 7)."""
    return await list_upcoming(app.state.db, days)


@app.get("/subscriptions/export/csv")
async def export_subscriptions_csv(
    status: str | None = Query(None),
    category: str | None = Query(None),
):
    """Export subscriptions as CSV file for spreadsheets or accounting."""
    subs = await list_subscriptions(app.state.db, status, category)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["service_name", "amount", "currency", "billing_cycle",
                     "category", "status", "last_billed", "next_billing"])
    for s in subs:
        writer.writerow([s["service_name"], s["amount"], s["currency"],
                         s["billing_cycle"], s["category"], s["status"],
                         s.get("last_billed", ""), s.get("next_billing", "")])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscriptions.csv"},
    )


@app.delete("/subscriptions/{subscription_id}", status_code=204)
async def remove_subscription(subscription_id: int):
    """Delete a subscription and all its associated alerts."""
    ok = await delete_subscription(app.state.db, subscription_id)
    if not ok:
        raise HTTPException(404, "Subscription not found")


@app.get("/spending/summary", response_model=SpendingSummary)
async def spending_summary():
    """Aggregated spend analytics: monthly/yearly totals, breakdown by category."""
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
