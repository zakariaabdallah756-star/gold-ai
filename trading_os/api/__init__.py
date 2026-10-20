"""REST API routers."""

from . import analytics, backup, checklist, news, positions, reviews, risk, signals, trades
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
    backup.router,
]
