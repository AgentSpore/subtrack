from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class BillingCycle(str, Enum):
    monthly = "monthly"
    yearly = "yearly"
    weekly = "weekly"
    unknown = "unknown"


class SubStatus(str, Enum):
    active = "active"
    cancelled = "cancelled"
    trial = "trial"
    paused = "paused"


class Category(str, Enum):
    saas = "saas"
    streaming = "streaming"
    cloud = "cloud"
    utilities = "utilities"
    marketing = "marketing"
    finance = "finance"
    other = "other"


class EmailImport(BaseModel):
    emails: list[str] = Field(description="Raw email text/body list to scan for subscriptions")


class SubscriptionResponse(BaseModel):
    id: int
    service_name: str
    amount: float
    currency: str
    billing_cycle: BillingCycle
    category: Category
    status: SubStatus
    detected_from: str
    last_billed: str | None
    next_billing: str | None
    price_history: list[dict]
    created_at: str


class SpendingSummary(BaseModel):
    total_monthly: float
    total_yearly: float
    currency: str
    by_category: dict[str, float]
    active_count: int
    trial_count: int
    top_subscriptions: list[dict]


class AlertResponse(BaseModel):
    id: int
    subscription_id: int
    service_name: str
    alert_type: str
    message: str
    old_value: str | None
    new_value: str | None
    created_at: str
    is_read: bool
