from __future__ import annotations

import csv
import io
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from models import EmailImport, SubscriptionResponse, SpendingSummary, AlertResponse
from extractor import (
    init_db, import_emails, list_subscriptions,
    get_spending_summary, list_alerts, mark_alert_read,
    delete_subscription, list_upcoming, update_subscription,
    get_analytics,
)

DB_PATH = "subtrack.db"


class SubscriptionUpdate(BaseModel):
    status: Optional[str] = None
    amount: Optional[float] = None
    billing_cycle: Optional[str] = None
    next_billing: Optional[str] = None
    category: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await init_db(DB_PATH)
    yield
    await app.state.db.close()


app = FastAPI(
    title="SubTrack",
    description="AI-powered subscription tracker. Paste billing emails, get a full picture of your recurring spend.",
    version="0.6.0",
    lifespan=lifespan,
)


@app.post("/subscriptions/import", response_model=list[SubscriptionResponse])
async def import_billing_emails(body: EmailImport):
    results = await import_emails(app.state.db, body.emails)
    if not results:
        raise HTTPException(422, "No subscription data found in provided emails")
    return results


@app.get("/subscriptions", response_model=list[SubscriptionResponse])
async def index_subscriptions(
    status: str | None = Query(None),
    category: str | None = Query(None),
):
    return await list_subscriptions(app.state.db, status, category)




@app.get("/subscriptions/analytics")
async def subscription_analytics():
    """
    Aggregated analytics: total monthly/annual spend, breakdown by category,
    active vs cancelled counts, most expensive subscription, avg cost.
    """
    return await get_analytics(app.state.db)

@app.get("/subscriptions/upcoming", response_model=list[SubscriptionResponse])
async def upcoming_subscriptions(days: int = Query(7, ge=1, le=90)):
    """List active subscriptions renewing within the next N days."""
    return await list_upcoming(app.state.db, days)


@app.get("/subscriptions/export/csv")
async def export_subscriptions_csv(
    status: str | None = Query(None),
    category: str | None = Query(None),
):
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


@app.patch("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
async def patch_subscription(subscription_id: int, body: SubscriptionUpdate):
    """Partially update a subscription: status, amount, billing_cycle, next_billing, category."""
    result = await update_subscription(app.state.db, subscription_id, body.model_dump())
    if not result:
        raise HTTPException(404, "Subscription not found")
    return result


@app.delete("/subscriptions/{subscription_id}", status_code=204)
async def remove_subscription(subscription_id: int):
    ok = await delete_subscription(app.state.db, subscription_id)
    if not ok:
        raise HTTPException(404, "Subscription not found")


@app.get("/spending/summary", response_model=SpendingSummary)
async def spending_summary():
    return await get_spending_summary(app.state.db)


@app.get("/alerts", response_model=list[AlertResponse])
async def get_alerts(unread_only: bool = Query(False)):
    return await list_alerts(app.state.db, unread_only)


@app.post("/alerts/{alert_id}/read")
async def read_alert(alert_id: int):
    ok = await mark_alert_read(app.state.db, alert_id)
    if not ok:
        raise HTTPException(404, "Alert not found")
    return {"status": "ok"}
