"""Printable PDF reports (journal performance and strategy backtests) built with reportlab.

Input is the plain dict already produced for the dashboard
(``services.analytics.full_report``) or the incubator
(``services.backtest.strategy_report``), so the PDF always matches what the app
shows. Layout is print-first: white page, dark text, profit/loss colour only as a
supplement to signed numbers.
"""

from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from .disclaimer import FOOTER_TEXT
from .i18n import N_, tr


class ReportUnavailable(RuntimeError):
    """reportlab is not installed."""


def _reportlab():
    try:
        from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ReportUnavailable(tr("PDF export needs reportlab: pip install -r trading_os/requirements.txt")) from exc
    return {
        "Drawing": Drawing,
        "Line": Line,
        "PolyLine": PolyLine,
        "Rect": Rect,
        "String": String,
        "colors": colors,
        "A4": A4,
        "ParagraphStyle": ParagraphStyle,
        "getSampleStyleSheet": getSampleStyleSheet,
        "mm": mm,
        "Paragraph": Paragraph,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "Table": Table,
        "TableStyle": TableStyle,
    }


INK = "#16171a"
MUTED = "#6b6a65"
RULE = "#d9d8d2"
ACCENT = "#2a78d6"
PROFIT = "#1a8a7f"
LOSS = "#d03b3b"


# ------------------------------------------------------------------ formatting
# Core PDF fonts are Latin-1 only, so plain ASCII signs are used throughout.


def money(value, currency: str) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0.004 else "-" if value < -0.004 else ""
    return f"{sign}{abs(value):,.2f} {currency}"


def plain_money(value, currency: str) -> str:
    return "-" if value is None else f"{value:,.2f} {currency}"


def pct(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}%"


def num(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def r_value(value) -> str:
    return "-" if value is None else f"{value:+.2f}R"


def profit_factor(stats: dict) -> str:
    if stats.get("profit_factor_infinite"):
        return "inf"
    return num(stats.get("profit_factor"))


def _markup_safe(text) -> str:
    """Paragraph text: reportlab parses it as XML-like markup, so user text (names, filters) is escaped."""
    return xml_escape(_ascii(text))


def _ascii(text) -> str:
    return (
        str(text)
        .replace("≤", "<=")
        .replace("≥", ">=")
        .replace("−", "-")
        .replace("—", "-")
        .replace("–", "-")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


# --------------------------------------------------------------------- builder


class _Builder:
    def __init__(self, title: str, subtitle: str):
        self.rl = _reportlab()
        rl = self.rl
        self.colors = rl["colors"]
        base = rl["getSampleStyleSheet"]()
        ps = rl["ParagraphStyle"]
        self.styles = {
            "title": ps(
                "title", parent=base["Title"], fontSize=18, leading=22, alignment=0, textColor=INK, spaceAfter=2
            ),
            "subtitle": ps("subtitle", parent=base["Normal"], fontSize=9, textColor=MUTED, spaceAfter=10),
            "h2": ps(
                "h2", parent=base["Heading2"], fontSize=11.5, leading=14, textColor=INK, spaceBefore=10, spaceAfter=4
            ),
            "body": ps("body", parent=base["Normal"], fontSize=8.5, leading=11, textColor=INK),
            "small": ps("small", parent=base["Normal"], fontSize=7, leading=9, textColor=MUTED),
        }
        self.story = [
            rl["Paragraph"](_markup_safe(title), self.styles["title"]),
            rl["Paragraph"](_markup_safe(subtitle), self.styles["subtitle"]),
        ]
        self.width = rl["A4"][0] - 36 * rl["mm"]

    def heading(self, text: str) -> None:
        self.story.append(self.rl["Paragraph"](_markup_safe(text), self.styles["h2"]))

    def paragraph(self, text: str, style: str = "body") -> None:
        self.story.append(self.rl["Paragraph"](_markup_safe(text), self.styles[style]))

    def table(
        self, header: list[str], rows: list[list], col_widths=None, numeric_from: int = 1, signs: dict | None = None
    ):
        """A compact zebra table; columns from ``numeric_from`` are right-aligned.

        ``signs`` maps column index -> list of raw numbers used to colour that column's cells.
        """
        rl = self.rl
        data = [[_ascii(c) for c in header]] + [[_ascii(c) for c in row] for row in rows]
        table = rl["Table"](data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
        style = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("TEXTCOLOR", (0, 1), (-1, -1), INK),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
            ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for index in range(2, len(data), 2):
            style.append(("BACKGROUND", (0, index), (-1, index), "#f4f4f1"))
        for column, values in (signs or {}).items():
            for row_index, value in enumerate(values, start=1):
                if value is not None and abs(value) >= 0.005:
                    style.append(("TEXTCOLOR", (column, row_index), (column, row_index), PROFIT if value > 0 else LOSS))
        table.setStyle(rl["TableStyle"](style))
        self.story.append(table)

    def kpis(self, items: list[tuple[str, str]], per_row: int = 4) -> None:
        rl = self.rl
        cells = [
            [f"{label}\n{value}" for label, value in items[i : i + per_row]] for i in range(0, len(items), per_row)
        ]
        for row in cells:
            row.extend([""] * (per_row - len(row)))
        table = rl["Table"]([[_ascii(c) for c in row] for row in cells], colWidths=[self.width / per_row] * per_row)
        table.setStyle(
            rl["TableStyle"](
                [
                    ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                    ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                    ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, RULE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEADING", (0, 0), (-1, -1), 12),
                ]
            )
        )
        self.story.append(table)

    def line_chart(self, values: list[float], title: str, reference: float | None = None, height: float = 150):
        """A single-series line chart with min/max labels and an optional reference line."""
        rl = self.rl
        width = self.width
        drawing = rl["Drawing"](width, height)
        if len(values) < 2:
            drawing.add(
                rl["String"](
                    4,
                    height / 2,
                    _ascii(tr("Not enough data for a chart")),
                    fontName="Helvetica",
                    fontSize=8,
                    fillColor=MUTED,
                )
            )
            self.story.append(drawing)
            return
        pad_left, pad_bottom, pad_top = 46, 14, 16
        low = min(values + ([reference] if reference is not None else []))
        high = max(values + ([reference] if reference is not None else []))
        span = (high - low) or 1.0
        plot_w, plot_h = width - pad_left - 6, height - pad_bottom - pad_top

        def y(value):
            return pad_bottom + (value - low) / span * plot_h

        drawing.add(rl["String"](0, height - 10, _ascii(title), fontName="Helvetica-Bold", fontSize=8.5, fillColor=INK))
        for value in (low, high):
            drawing.add(rl["Line"](pad_left, y(value), width - 6, y(value), strokeColor=RULE, strokeWidth=0.5))
            drawing.add(
                rl["String"](0, y(value) - 3, f"{value:,.0f}", fontName="Helvetica", fontSize=7, fillColor=MUTED)
            )
        if reference is not None:
            drawing.add(rl["Line"](pad_left, y(reference), width - 6, y(reference), strokeColor=MUTED, strokeWidth=0.6))
        step = plot_w / (len(values) - 1)
        points = []
        for index, value in enumerate(values):
            points.extend([pad_left + index * step, y(value)])
        drawing.add(rl["PolyLine"](points, strokeColor=ACCENT, strokeWidth=1.4))
        self.story.append(drawing)

    def spacer(self, height: float = 6) -> None:
        self.story.append(self.rl["Spacer"](1, height))

    def build(self) -> bytes:
        rl = self.rl
        buffer = io.BytesIO()
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        footer = _ascii(tr(FOOTER_TEXT))

        def decorate(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 6.5)
            canvas.setFillColor(MUTED)
            text = canvas.beginText(18 * rl["mm"], 14 * rl["mm"])
            line, lines = "", []
            for word in footer.split():
                if canvas.stringWidth(f"{line} {word}", "Helvetica", 6.5) > self.width:
                    lines.append(line)
                    line = word
                else:
                    line = f"{line} {word}".strip()
            lines.append(line)
            for item in lines:
                text.textLine(item)
            canvas.drawText(text)
            canvas.drawRightString(
                rl["A4"][0] - 18 * rl["mm"],
                8 * rl["mm"],
                _ascii(tr("Trading OS - generated {generated} - page {page}", generated=generated, page=doc.page)),
            )
            canvas.restoreState()

        doc = rl["SimpleDocTemplate"](
            buffer,
            pagesize=rl["A4"],
            leftMargin=18 * rl["mm"],
            rightMargin=18 * rl["mm"],
            topMargin=16 * rl["mm"],
            bottomMargin=28 * rl["mm"],
            title=_ascii(tr("Trading OS report")),
            author="Trading OS",
        )
        doc.build(self.story, onFirstPage=decorate, onLaterPages=decorate)
        return buffer.getvalue()


# ---------------------------------------------------------------------- reports

BREAKDOWN_SECTIONS = (("symbol", N_("By asset")), ("strategy", N_("By strategy")), ("session", N_("By session")))
STRATEGY_STATUS_WORDS = {"live": N_("live"), "incubator": N_("incubator"), "retired": N_("retired")}
BOOKING_NOTE = N_(
    "P&L is booked on the day it was realised: each partial exit on the day it was filled, the rest of "
    "the trade - with its fees - when it closed. Trade statistics count each trade once, at its close."
)


def _breakdown_rows(rows: list[dict], currency: str, limit: int = 10):
    shown = rows[:limit]
    return (
        [
            [
                r["key"],
                str(r["trades"]),
                pct(r["win_rate"]),
                money(r["net_pnl"], currency),
                profit_factor(r),
                money(r["expectancy"], currency),
            ]
            for r in shown
        ],
        [r["net_pnl"] for r in shown],
    )


def _group_header() -> list[str]:
    return ["", tr("Trades"), tr("Win rate"), tr("Net P&L"), tr("Profit factor"), tr("Expectancy")]


def _realised_equity(b: _Builder, report: dict, currency: str) -> None:
    curve = [point["equity"] for point in report.get("equity_curve", [])]
    b.line_chart(curve, tr("Realised equity ({currency})", currency=currency), reference=curve[0] if curve else None)


def _recent_days(b: _Builder, days: list[dict], currency: str) -> None:
    b.heading(tr("Most recent trading days"))
    recent = days[-15:][::-1]
    b.table(
        [tr("Date"), tr("Trades"), "W / L", tr("Partial exits"), "P&L", tr("Cumulative")],
        [
            [
                d["date"],
                str(d["trades"]),
                f"{d['wins']} / {d['losses']}",
                str(d.get("partials", 0)),
                money(d["pnl"], currency),
                money(d["cumulative"], currency),
            ]
            for d in recent
        ],
        signs={4: [d["pnl"] for d in recent]},
    )
    b.paragraph(tr(BOOKING_NOTE), "small")


def build_performance_pdf(report: dict, *, account: str, currency: str, period: str, filters: str = "") -> bytes:
    """Journal performance summary from ``analytics.full_report`` output."""
    s = report["summary"]
    days = report.get("daily_pnl") or []
    b = _Builder(tr("Trading performance report"), f"{account} - {period}" + (f" - {filters}" if filters else ""))
    if not s["trades"]:
        b.paragraph(tr("No closed trades in this period."))
        if days:  # only partial exits of trades that close later (or are still open) were booked
            b.paragraph(tr("Realised in this period: {amount}", amount=money(s.get("realized_pnl"), currency)))
            b.spacer(10)
            _realised_equity(b, report, currency)
            _recent_days(b, days, currency)
        return b.build()

    b.kpis(
        [
            (tr("Net P&L"), money(s["net_pnl"], currency)),
            (tr("Return"), pct(s.get("return_pct"), 2)),
            (tr("Trades"), f"{s['trades']} ({s['wins']}W / {s['losses']}L / {s['breakeven']}BE)"),
            (tr("Win rate"), pct(s["win_rate"])),
            (tr("Profit factor"), profit_factor(s)),
            (tr("Expectancy / trade"), f"{money(s['expectancy'], currency)} ({r_value(s.get('avg_r'))})"),
            (
                tr("Max drawdown"),
                f"{plain_money(s.get('max_drawdown'), currency)} ({pct(s.get('max_drawdown_pct'))})",
            ),
            (tr("Payoff ratio"), num(s.get("payoff_ratio"))),
            (tr("Avg planned R:R"), "-" if s.get("avg_planned_rr") is None else f"1:{s['avg_planned_rr']:.2f}"),
            (tr("Rule compliance"), pct(s.get("compliance_rate"), 0)),
            (
                tr("Largest win / loss"),
                f"{money(s.get('largest_win'), currency)} / {money(s.get('largest_loss'), currency)}",
            ),
            (tr("Fees"), plain_money(s.get("total_fees"), currency)),
        ]
    )
    realized = s.get("realized_pnl")
    if realized is not None and abs(realized - (s["net_pnl"] or 0.0)) >= 0.005:
        # Partial exits on either side of the range: what was booked in it differs from the closed trades' P&L.
        b.spacer(4)
        b.paragraph(tr("Realised in this period: {amount}", amount=money(realized, currency)))
    b.spacer(10)
    _realised_equity(b, report, currency)

    followed, broke = s.get("followed_plan"), s.get("broke_rules")
    if followed and broke:
        b.heading(tr("Discipline"))
        b.table(
            _group_header(),
            [
                [
                    tr("Followed plan"),
                    str(followed["trades"]),
                    pct(followed["win_rate"]),
                    money(followed["net_pnl"], currency),
                    profit_factor(followed),
                    money(followed["expectancy"], currency),
                ],
                [
                    tr("Broke rules"),
                    str(broke["trades"]),
                    pct(broke["win_rate"]),
                    money(broke["net_pnl"], currency),
                    profit_factor(broke),
                    money(broke["expectancy"], currency),
                ],
            ],
            signs={3: [followed["net_pnl"], broke["net_pnl"]]},
        )

    for key, title in BREAKDOWN_SECTIONS:
        rows = report.get("breakdowns", {}).get(key) or []
        if rows:
            b.heading(tr(title))
            body, nets = _breakdown_rows(rows, currency)
            b.table(_group_header(), body, signs={3: nets})

    costs = s.get("mistake_cost") or []
    if costs:
        b.heading(tr("Cost of mistakes"))
        b.table(
            [tr("Mistake"), tr("Trades"), tr("Win rate"), tr("Net P&L"), tr("Avg P&L")],
            [
                [
                    c["mistake"],
                    str(c["trades"]),
                    pct(c.get("win_rate")),
                    money(c["net_pnl"], currency),
                    money(c.get("avg_pnl"), currency),
                ]
                for c in costs[:10]
            ],
            signs={3: [c["net_pnl"] for c in costs[:10]]},
        )

    if days:
        _recent_days(b, days, currency)
    return b.build()


def build_backtest_pdf(report: dict) -> bytes:
    """Strategy backtest summary from ``backtest.strategy_report`` output."""
    strategy, m, currency = report["strategy"], report["metrics"], report["currency"]
    status = strategy["status"]
    b = _Builder(
        tr("Backtest report - {name}", name=strategy["name"]),
        tr(
            "Status: {status} - {risk:,.2f} {currency} per 1R - hypothetical results",
            status=tr(STRATEGY_STATUS_WORDS[status]) if status in STRATEGY_STATUS_WORDS else status,
            risk=report["risk_per_trade"],
            currency=currency,
        ),
    )
    if not m["trades"]:
        b.paragraph(tr("No backtest trades recorded for this strategy yet."))
        return b.build()
    b.kpis(
        [
            (tr("Trades"), f"{m['trades']} ({m['wins']}W / {m['losses']}L / {m['breakeven']}BE)"),
            (tr("Win rate"), pct(m["win_rate"])),
            (tr("Profit factor"), profit_factor(m)),
            (tr("Expectancy / trade"), f"{r_value(m.get('expectancy_r'))} ({money(m['expectancy'], currency)})"),
            (tr("Net result"), f"{r_value(m.get('net_r'))} ({money(m['net_pnl'], currency)})"),
            (tr("Avg win : avg loss"), "-" if m.get("avg_rr") is None else f"{m['avg_rr']:.2f} : 1"),
            (
                tr("Max drawdown"),
                f"{num(m.get('max_drawdown_r'))}R ({plain_money(m.get('max_drawdown'), currency)}, {pct(m.get('max_drawdown_pct'))})",
            ),
            (tr("Avg planned R:R"), "-" if m.get("avg_planned_rr") is None else f"1:{m['avg_planned_rr']:.2f}"),
        ]
    )
    b.spacer(10)
    b.line_chart([p.get("r", 0.0) for p in report["equity_curve"]], tr("Cumulative result (R)"), reference=0.0)

    b.heading(tr("Backtest vs live journal"))
    rows = report["comparison"]["rows"]

    def cell(row, side):
        if (row.get("infinite") or {}).get(side):
            return "inf"
        value = row[side]
        if value is None:
            return "-"
        unit = row.get("unit")
        size = row["key"].startswith("max_drawdown")  # a drawdown is a size, never a gain
        if unit == "pct":
            return pct(value)
        if unit == "money":
            return plain_money(value, currency) if size else money(value, currency)
        if unit == "r":
            return f"{num(value)}R" if size else r_value(value)
        if row["key"] == "trades":
            return str(value)
        return num(value)

    def delta(row):
        if row["delta"] is None:
            return "-"
        if row.get("unit") == "money":
            return f"{row['delta']:+,.2f} {currency}"
        if row.get("unit") == "r":
            return f"{row['delta']:+.2f}R"
        if row.get("unit") == "pct":
            return tr("{value:+.2f} pp", value=row["delta"])
        return f"{row['delta']:+,.2f}"

    b.table(
        [tr("Metric"), tr("Backtest"), tr("Live"), tr("Delta")],
        [[r["label"], cell(r, "backtest"), cell(r, "live"), delta(r)] for r in rows],
    )
    b.spacer(4)
    gap = report["comparison"].get("gap")
    if gap:
        kept = (
            ""
            if gap["edge_retained_pct"] is None
            else tr(", {share:.0f}% of the backtest edge kept", share=gap["edge_retained_pct"])
        )
        at_risk = (
            ""
            if gap["gap_money_same_basis"] is None
            else tr(" ({amount} per trade at the backtest risk)", amount=money(gap["gap_money_same_basis"], currency))
        )
        b.paragraph(
            tr("Expectancy gap: {gap:+.2f}R per trade{at_risk}{kept}.", gap=gap["gap_r"], at_risk=at_risk, kept=kept)
        )
    verdict = report["comparison"]["verdict"]
    b.paragraph(verdict["message"])
    for note in verdict.get("notes", []):
        b.paragraph(note, "small")
    b.spacer(6)
    b.paragraph(
        tr(
            "Hypothetical results have inherent limitations: they are prepared with hindsight and do not reflect real "
            "execution costs, slippage or the psychological pressure of live trading."
        ),
        "small",
    )
    return b.build()
