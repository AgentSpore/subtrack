from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta

import aiosqlite

SQL_TABLES = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    billing_cycle TEXT NOT NULL DEFAULT 'monthly',
    category TEXT NOT NULL DEFAULT 'other',
    status TEXT NOT NULL DEFAULT 'active',
    detected_from TEXT NOT NULL DEFAULT 'email',
    last_billed TEXT,
    next_billing TEXT,
    price_history TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
);
"""

# Known subscription services and their patterns
SERVICE_PATTERNS = [
    (r"netflix", "Netflix", "streaming", "monthly"),
    (r"spotify", "Spotify", "streaming", "monthly"),
    (r"apple\s*(?:one|music|tv\+|icloud)", "Apple Services", "streaming", "monthly"),
    (r"amazon\s*(?:prime|aws)", "Amazon", "cloud", "monthly"),
    (r"google\s*(?:one|workspace|cloud)", "Google", "cloud", "monthly"),
    (r"microsoft\s*(?:365|azure|office)", "Microsoft", "saas", "monthly"),
    (r"dropbox", "Dropbox", "cloud", "monthly"),
    (r"github", "GitHub", "saas", "monthly"),
    (r"notion", "Notion", "saas", "monthly"),
    (r"slack", "Slack", "saas", "monthly"),
    (r"zoom", "Zoom", "saas", "monthly"),
    (r"figma", "Figma", "saas", "monthly"),
    (r"linear", "Linear", "saas", "monthly"),
    (r"vercel", "Vercel", "cloud", "monthly"),
    (r"heroku", "Heroku", "cloud", "monthly"),
    (r"digitalocean", "DigitalOcean", "cloud", "monthly"),
    (r"openai", "OpenAI", "saas", "monthly"),
    (r"anthropic", "Anthropic", "saas", "monthly"),
    (r"hubspot", "HubSpot", "marketing", "monthly"),
    (r"mailchimp", "Mailchimp", "marketing", "monthly"),
    (r"stripe", "Stripe", "finance", "monthly"),
    (r"quickbooks", "QuickBooks", "finance", "monthly"),
    (r"adobe", "Adobe", "saas", "monthly"),
    (r"canva", "Canva", "saas", "monthly"),
    (r"loom", "Loom", "saas", "monthly"),
    (r"airtable", "Airtable", "saas", "monthly"),
]

AMOUNT_PATTERN = re.compile(
    r"(?:charged|billed|payment|invoice|receipt|total|amount)[^\$£€\d]*"
    r"(?P<currency>[\$£€]|USD|EUR|GBP)?\s*(?P<amount>\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)
GENERIC_AMOUNT_PATTERN = re.compile(r"(?P<currency>[\$£€])\s*(?P<amount>\d+(?:[.,]\d{1,2})?)")

DATE_PATTERN = re.compile(
    r"(?:date|billed on|charged on|next billing)[:\s]+([A-Za-z]+ \d{1,2},? \d{4}|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})",
    re.IGNORECASE,
)
TRIAL_PATTERN = re.compile(r"trial|free\s+(?:month|period|trial)", re.IGNORECASE)
CANCEL_PATTERN = re.compile(r"cancell?ed|subscription\s+ended|no longer\s+active", re.IGNORECASE)
YEARLY_PATTERN = re.compile(r"annual|yearly|per\s+year|\/year", re.IGNORECASE)

_DATE_FMTS = [
    "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%d-%m-%Y",
    "%m/%d/%y", "%m-%d-%y",
]


def _parse_date(date_str: str) -> datetime | None:
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _compute_next_billing(last_billed: str | None, billing_cycle: str) -> str | None:
    """Compute next billing date from last_billed + cycle."""
    base = _parse_date(last_billed) if last_billed else datetime.now(timezone.utc)
    if base is None:
        base = datetime.now(timezone.utc)
    if billing_cycle == "yearly":
        try:
            nxt = base.replace(year=base.year + 1)
        except ValueError:
            nxt = base + timedelta(days=365)
    elif billing_cycle == "weekly":
        nxt = base + timedelta(weeks=1)
    else:  # monthly
        month = base.month + 1
        year = base.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        try:
            nxt = base.replace(year=year, month=month)
        except ValueError:
            nxt = base.replace(year=year, month=month, day=28)
    return nxt.strftime("%Y-%m-%d")


def extract_subscription(email_text: str) -> dict | None:
    """Extract subscription info from a raw email body."""
    lower = email_text.lower()

    # Match known service
    service_name = None
    category = "other"
    default_cycle = "monthly"
    for pattern, name, cat, cycle in SERVICE_PATTERNS:
        if re.search(pattern, lower):
            service_name = name
            category = cat
            default_cycle = cycle
            break

    if not service_name:
        # Try generic: look for "receipt", "invoice", "subscription" keywords
        if not any(kw in lower for kw in ["receipt", "invoice", "subscription", "billing", "payment", "charged"]):
            return None
        # Extract company from "from" line or subject
        match = re.search(r"from\s*([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", email_text)
        service_name = match.group(1) if match else "Unknown Service"

    # Extract amount
    amount = 0.0
    currency = "USD"
    for pat in [AMOUNT_PATTERN, GENERIC_AMOUNT_PATTERN]:
        m = pat.search(email_text)
        if m:
            raw = m.group("amount").replace(",", ".")
            try:
                amount = float(raw)
            except ValueError:
                pass
            cur = m.group("currency") or "USD"
            currency = {"$": "USD", "£": "GBP", "€": "EUR"}.get(cur, cur)
            break

    # Determine billing cycle
    billing_cycle = "yearly" if YEARLY_PATTERN.search(email_text) else default_cycle

    # Determine status
    if CANCEL_PATTERN.search(email_text):
        status = "cancelled"
    elif TRIAL_PATTERN.search(email_text):
        status = "trial"
    else:
        status = "active"

    # Extract date
    date_match = DATE_PATTERN.search(email_text)
    last_billed = date_match.group(1) if date_match else None
    next_billing = _compute_next_billing(last_billed, billing_cycle)

    return {
        "service_name": service_name,
        "amount": amount,
        "currency": currency,
        "billing_cycle": billing_cycle,
        "category": category,
        "status": status,
        "last_billed": last_billed,
        "next_billing": next_billing,
    }


async def init_db(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.executescript(SQL_TABLES)
    await db.commit()
    return db


async def import_emails(db: aiosqlite.Connection, emails: list[str]) -> list[dict]:
    """Process a list of email bodies and upsert subscriptions."""
    now = datetime.now(timezone.utc).isoformat()
    results = []

    for email_text in emails:
        extracted = extract_subscription(email_text)
        if not extracted:
            continue

        # Check if subscription already exists
        existing = await db.execute_fetchall(
            "SELECT * FROM subscriptions WHERE service_name = ?",
            (extracted["service_name"],),
        )

        if existing:
            row = existing[0]
            # Detect price change → alert
            if row["amount"] != extracted["amount"] and extracted["amount"] > 0:
                old_price = f'{row["currency"]} {row["amount"]}'
                new_price = f'{extracted["currency"]} {extracted["amount"]}'
                await db.execute(
                    "INSERT INTO alerts (subscription_id, alert_type, message, old_value, new_value, created_at) VALUES (?, 'price_change', ?, ?, ?, ?)",
                    (row["id"], f'{extracted["service_name"]} changed price: {old_price} → {new_price}', old_price, new_price, now),
                )
                history = json.loads(row["price_history"])
                history.append({"date": now, "amount": extracted["amount"], "currency": extracted["currency"]})
                await db.execute(
                    "UPDATE subscriptions SET amount=?, last_billed=?, price_history=? WHERE id=?",
                    (extracted["amount"], extracted["last_billed"] or row["last_billed"], json.dumps(history), row["id"]),
                )
            results.append(dict(row))
        else:
            cur = await db.execute(
                "INSERT INTO subscriptions (service_name, amount, currency, billing_cycle, category, status, detected_from, last_billed, next_billing, price_history, created_at) VALUES (?, ?, ?, ?, ?, ?, 'email', ?, ?, '[]', ?)",
                (extracted["service_name"], extracted["amount"], extracted["currency"],
                 extracted["billing_cycle"], extracted["category"], extracted["status"],
                 extracted["last_billed"], extracted.get("next_billing"), now),
            )
            row = await db.execute_fetchall("SELECT * FROM subscriptions WHERE id=?", (cur.lastrowid,))
            results.append(dict(row[0]))

    await db.commit()
    return results


async def list_subscriptions(db: aiosqlite.Connection, status: str | None = None, category: str | None = None) -> list[dict]:
    q = "SELECT * FROM subscriptions"
    params: list = []
    conditions = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if conditions:
        q += " WHERE " + " AND ".join(conditions)
    q += " ORDER BY next_billing ASC NULLS LAST"
    rows = await db.execute_fetchall(q, params)
    return [_sub_row(r) for r in rows]


async def get_spending_summary(db: aiosqlite.Connection) -> dict:
    rows = await db.execute_fetchall("SELECT * FROM subscriptions WHERE status = 'active'")
    total_monthly = 0.0
    total_yearly = 0.0
    by_cat: dict[str, float] = {}
    top: list[dict] = []

    for r in rows:
        monthly = r["amount"] if r["billing_cycle"] == "monthly" else r["amount"] / 12
        yearly = r["amount"] * 12 if r["billing_cycle"] == "monthly" else r["amount"]
        total_monthly += monthly
        total_yearly += yearly
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + monthly
        top.append({"name": r["service_name"], "monthly": round(monthly, 2), "category": r["category"]})

    top.sort(key=lambda x: x["monthly"], reverse=True)
    trial_count = len(await db.execute_fetchall("SELECT id FROM subscriptions WHERE status='trial'"))

    return {
        "total_monthly": round(total_monthly, 2),
        "total_yearly": round(total_yearly, 2),
        "currency": "USD",
        "by_category": {k: round(v, 2) for k, v in by_cat.items()},
        "active_count": len(rows),
        "trial_count": trial_count,
        "top_subscriptions": top[:10],
    }


async def list_alerts(db: aiosqlite.Connection, unread_only: bool = False) -> list[dict]:
    q = "SELECT a.*, s.service_name FROM alerts a JOIN subscriptions s ON a.subscription_id = s.id"
    if unread_only:
        q += " WHERE a.is_read = 0"
    q += " ORDER BY a.created_at DESC"
    rows = await db.execute_fetchall(q)
    return [_alert_row(r) for r in rows]


async def mark_alert_read(db: aiosqlite.Connection, alert_id: int) -> bool:
    await db.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))
    await db.commit()
    return True


def _sub_row(r) -> dict:
    return {
        "id": r["id"], "service_name": r["service_name"],
        "amount": r["amount"], "currency": r["currency"],
        "billing_cycle": r["billing_cycle"], "category": r["category"],
        "status": r["status"], "detected_from": r["detected_from"],
        "last_billed": r["last_billed"], "next_billing": r["next_billing"],
        "price_history": json.loads(r["price_history"]), "created_at": r["created_at"],
    }


def _alert_row(r) -> dict:
    return {
        "id": r["id"], "subscription_id": r["subscription_id"],
        "service_name": r["service_name"], "alert_type": r["alert_type"],
        "message": r["message"], "old_value": r["old_value"],
        "new_value": r["new_value"], "created_at": r["created_at"],
        "is_read": bool(r["is_read"]),
    }

async def delete_subscription(db: aiosqlite.Connection, subscription_id: int) -> bool:
    """Delete a subscription and its associated alerts."""
    await db.execute("DELETE FROM alerts WHERE subscription_id = ?", (subscription_id,))
    cur = await db.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
    await db.commit()
    return cur.rowcount > 0

async def list_upcoming(db: aiosqlite.Connection, days: int = 7) -> list:
    """Return subscriptions with next_billing within the next N days."""
    from datetime import datetime, timedelta
    today = datetime.utcnow().date()
    cutoff = (today + timedelta(days=days)).isoformat()
    today_str = today.isoformat()
    rows = await db.execute_fetchall(
        """SELECT * FROM subscriptions
           WHERE next_billing IS NOT NULL
             AND next_billing >= ?
             AND next_billing <= ?
             AND status = 'active'
           ORDER BY next_billing ASC""",
        (today_str, cutoff),
    )
    return [_sub_row(r) for r in rows]

async def update_subscription(db: aiosqlite.Connection, subscription_id: int, updates: dict) -> dict | None:
    """Partially update a subscription (status, amount, billing_cycle, next_billing, category)."""
    allowed = {"status", "amount", "billing_cycle", "next_billing", "category"}
    fields = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not fields:
        # Return current row unchanged
        rows = await db.execute_fetchall("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
        return _sub_row(rows[0]) if rows else None
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    cur = await db.execute(
        f"UPDATE subscriptions SET {set_clause} WHERE id = ?",
        list(fields.values()) + [subscription_id],
    )
    await db.commit()
    if cur.rowcount == 0:
        return None
    rows = await db.execute_fetchall("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
    return _sub_row(rows[0]) if rows else None



async def get_analytics(db: aiosqlite.Connection) -> dict:
    """
    Aggregated subscription analytics for dashboard:
    - monthly/annual spend totals
    - per-category breakdown
    - active vs cancelled counts
    - most expensive subscription
    - avg cost per subscription
    """
    from datetime import datetime
    rows = await db.execute_fetchall("SELECT * FROM subscriptions")
    if not rows:
        return {
            "total_monthly_spend": 0.0,
            "total_annual_spend": 0.0,
            "active_count": 0,
            "cancelled_count": 0,
            "avg_monthly_cost": 0.0,
            "most_expensive": None,
            "by_category": {},
            "by_status": {},
        }

    def to_monthly(amount: float, cycle: str) -> float:
        if cycle == "yearly":
            return round(amount / 12, 2)
        elif cycle == "weekly":
            return round(amount * 4.33, 2)
        elif cycle == "quarterly":
            return round(amount / 3, 2)
        return amount  # monthly

    total_monthly = 0.0
    by_cat: dict[str, float] = {}
    by_status: dict[str, int] = {}
    most_expensive = None
    most_expensive_monthly = 0.0

    for r in rows:
        sub = _sub_row(r)
        monthly = to_monthly(sub.get("amount", 0), sub.get("billing_cycle", "monthly"))
        status = sub.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        if status != "active":
            continue
        total_monthly += monthly
        cat = sub.get("category") or "other"
        by_cat[cat] = round(by_cat.get(cat, 0.0) + monthly, 2)
        if monthly > most_expensive_monthly:
            most_expensive_monthly = monthly
            most_expensive = {"name": sub.get("name"), "monthly_cost": monthly,
                              "billing_cycle": sub.get("billing_cycle")}

    active = by_status.get("active", 0)
    return {
        "total_monthly_spend": round(total_monthly, 2),
        "total_annual_spend": round(total_monthly * 12, 2),
        "active_count": active,
        "cancelled_count": by_status.get("cancelled", 0),
        "avg_monthly_cost": round(total_monthly / active, 2) if active else 0.0,
        "most_expensive": most_expensive,
        "by_category": by_cat,
        "by_status": by_status,
    }


async def create_subscription(db: aiosqlite.Connection, data: dict) -> dict:
    """Manually create a subscription without email parsing."""
    next_billing = data.get("next_billing") or _compute_next_billing(
        data.get("last_billed"), data.get("billing_cycle", "monthly")
    )
    cur = await db.execute(
        """INSERT INTO subscriptions
           (service_name, amount, currency, billing_cycle, category, status,
            detected_from, last_billed, next_billing, price_history)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            data["service_name"],
            data.get("amount", 0.0),
            data.get("currency", "USD"),
            data.get("billing_cycle", "monthly"),
            data.get("category", "other"),
            data.get("status", "active"),
            "manual",
            data.get("last_billed"),
            next_billing,
            "[]",
        ),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM subscriptions WHERE id = ?", (cur.lastrowid,))
    return _sub_row(rows[0]) if rows else {}


async def get_subscription(db: aiosqlite.Connection, subscription_id: int) -> dict | None:
    """Get a single subscription by ID."""
    rows = await db.execute_fetchall(
        "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
    )
    return _sub_row(rows[0]) if rows else None
