"""REST API routers."""

from . import analytics, backup, checklist, risk, trades
from .plan import plan_routers

routers = [*plan_routers, checklist.router, risk.router, trades.router, analytics.router, backup.router]
