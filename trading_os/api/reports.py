"""Performance report PDF export (journal and strategy backtests)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..models import Strategy
from ..services import backtest, report_pdf
from ..services import trades as trade_service
from ..services.account import local_today
from .deps import DbDep, SettingsDep, get_or_404
from .trades import FiltersDep

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _pdf(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render(build, *args, **kwargs) -> bytes:
    try:
        return build(*args, **kwargs)
    except report_pdf.ReportUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/performance.pdf")
def performance_pdf(settings: SettingsDep, db: DbDep, filters: FiltersDep):
    """Journal performance summary; accepts the same filters as /api/analytics."""
    _, report = trade_service.performance_report(db, settings, filters)

    if filters.date_from or filters.date_to:
        period = f"{filters.date_from or 'start'} to {filters.date_to or local_today(settings)}"
    else:
        period = f"All time to {local_today(settings)}"
    applied = ", ".join(
        f"{label}: {value}"
        for label, value in (
            ("Asset", filters.symbol),
            ("Strategy", filters.strategy),
            ("Session", filters.session),
            ("Direction", filters.direction),
        )
        if value
    )
    content = _render(
        report_pdf.build_performance_pdf,
        report,
        account=settings.account_name,
        currency=settings.currency,
        period=period,
        filters=applied,
    )
    return _pdf(content, f"trading-os-performance-{local_today(settings):%Y%m%d}.pdf")


@router.get("/backtest/{strategy_id}.pdf")
def backtest_pdf(strategy_id: int, settings: SettingsDep, db: DbDep):
    strategy = get_or_404(db, Strategy, strategy_id)
    content = _render(report_pdf.build_backtest_pdf, backtest.strategy_report(db, settings, strategy))
    return _pdf(content, f"trading-os-backtest-{strategy.id}-{local_today(settings):%Y%m%d}.pdf")
