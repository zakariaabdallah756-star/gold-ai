"""REST API routers."""

from . import (
    analytics,
    backtest,
    backup,
    checklist,
    news,
    partners,
    positions,
    prop,
    reports,
    reviews,
    risk,
    signals,
    trades,
    vision,
)
from .plan import plan_routers

routers = [
    *plan_routers,
    checklist.router,
    risk.router,
    trades.router,
    positions.router,
    signals.router,
    news.router,
    reviews.router,
    analytics.router,
    vision.router,
    backtest.router,
    prop.router,
    partners.router,
    reports.router,
    backup.router,
]
