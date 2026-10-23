// Chart.js builders. Colours mirror the CSS tokens and were validated
// (contrast >= 3:1 on the surface; profit/loss separable under CVD).
// Profit/loss is never colour-only: bars sit above/below the zero line and
// every tooltip value is signed.

import { fmt } from "./format.js";
import { t, tn } from "./i18n.js";

const C = {
  surface: "#16171a",
  grid: "#2c2c2a",
  axis: "#383835",
  muted: "#898781",
  text2: "#c3c2b7",
  text: "#f0efec",
  accent: "#3987e5",
  accentWash: "rgba(57, 135, 229, 0.10)",
  profit: "#26a69a",
  profitHover: "#3fbcb0",
  loss: "#ef5350",
  lossHover: "#f37573",
  lossWash: "rgba(239, 83, 80, 0.10)",
  neutral: "#898781",
  tooltip: "#25272c",
};

export const chartsAvailable = () => typeof window.Chart === "function";

let defaultsApplied = false;

function applyDefaults() {
  if (defaultsApplied || !chartsAvailable()) return;
  defaultsApplied = true;
  const d = window.Chart.defaults;
  d.color = C.muted;
  d.font.family = getComputedStyle(document.body).fontFamily;
  d.font.size = 11.5;
  d.borderColor = C.grid;
  d.maintainAspectRatio = false;
  d.animation.duration = 250;
  d.plugins.legend.display = false;
  Object.assign(d.plugins.tooltip, {
    backgroundColor: C.tooltip,
    borderColor: "rgba(255, 255, 255, 0.16)",
    borderWidth: 1,
    titleColor: C.text2,
    bodyColor: C.text,
    footerColor: C.muted,
    padding: 10,
    cornerRadius: 8,
    displayColors: false,
    titleFont: { weight: "500" },
    bodyFont: { weight: "600", size: 13 },
    footerFont: { weight: "400" },
    caretSize: 0,
  });
}

// Vertical hairline that snaps to the hovered x position.
const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.();
    if (!active?.length) return;
    const { ctx, chartArea } = chart;
    const x = active[0].element.x;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.28)";
    ctx.stroke();
    ctx.restore();
  },
};

// Solid hairline marking a reference value (e.g. the starting balance).
const referenceLine = {
  id: "referenceLine",
  beforeDatasetsDraw(chart, _args, options) {
    if (options.value === undefined || options.value === null) return;
    const { ctx, chartArea, scales } = chart;
    const y = scales.y.getPixelForValue(options.value);
    if (y < chartArea.top || y > chartArea.bottom) return;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(chartArea.left, y);
    ctx.lineTo(chartArea.right, y);
    ctx.lineWidth = 1;
    ctx.strokeStyle = C.axis;
    ctx.stroke();
    if (options.label) {
      ctx.fillStyle = C.muted;
      ctx.font = `11px ${window.Chart.defaults.font.family}`;
      ctx.textAlign = "right";
      ctx.fillText(options.label, chartArea.right - 6, y - 6);
    }
    ctx.restore();
  },
};

function moneyAxis() {
  return {
    grid: { color: (ctx) => (ctx.tick?.value === 0 ? C.axis : C.grid) },
    border: { display: false },
    ticks: { maxTicksLimit: 6, callback: (value) => fmt.compactMoney(value) },
  };
}

export function equityChart(canvas, points) {
  applyDefaults();
  const base = points[0]?.equity ?? 0;
  const last = points.length - 1;
  return new window.Chart(canvas, {
    type: "line",
    data: {
      labels: points.map((p) => p.n),
      datasets: [
        {
          data: points.map((p) => p.equity),
          borderColor: C.accent,
          borderWidth: 2,
          borderCapStyle: "round",
          borderJoinStyle: "round",
          tension: 0,
          fill: { target: { value: base }, above: C.accentWash, below: C.lossWash },
          pointRadius: (ctx) => (ctx.dataIndex === last ? 4 : 0),
          pointBackgroundColor: C.accent,
          pointBorderColor: C.surface,
          pointBorderWidth: 2,
          pointHoverRadius: 5,
          pointHoverBorderWidth: 2,
          pointHoverBackgroundColor: C.accent,
          pointHoverBorderColor: C.surface,
          pointHitRadius: 12,
        },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid: { display: false },
          border: { color: C.axis },
          ticks: { maxTicksLimit: 8, maxRotation: 0, callback: (_v, i) => (i === 0 ? t("Start") : `#${points[i]?.n}`) },
        },
        y: moneyAxis(),
      },
      plugins: {
        referenceLine: { value: base, label: t("Starting balance") },
        tooltip: {
          callbacks: {
            title: (items) => {
              const p = points[items[0].dataIndex];
              // Realised equity points (journal) carry the booking kind: a partial exit or a trade's close.
              if (p.n !== 0 && p.kind) {
                const params = { id: p.trade_id, symbol: p.symbol, date: fmt.date(p.t) };
                return p.closes ? t("Trade #{id} closed · {symbol} · {date}", params) : t("Partial exit · trade #{id} · {symbol} · {date}", params);
              }
              return p.n === 0 ? t("Starting balance") : t("Trade #{n} · {symbol} · {date}", { n: p.n, symbol: p.symbol, date: fmt.date(p.t) });
            },
            label: (item) => t("Equity {value}", { value: fmt.money(item.parsed.y) }),
            footer: (items) => {
              const p = points[items[0].dataIndex];
              if (p.pnl !== null && p.kind) return t("Booked P&L {pnl}", { pnl: fmt.signedMoney(p.pnl) });
              return p.pnl === null ? "" : t("Trade P&L {pnl}", { pnl: fmt.signedMoney(p.pnl) });
            },
          },
        },
      },
    },
    plugins: [crosshair, referenceLine],
  });
}

function barDataset(values, colorFor, hoverFor) {
  return {
    data: values,
    backgroundColor: values.map(colorFor),
    hoverBackgroundColor: values.map(hoverFor),
    borderRadius: 4,
    borderSkipped: "start",
    maxBarThickness: 24,
    categoryPercentage: 0.9,
    barPercentage: 0.92,
  };
}

const pnlColor = (v) => (v > 0 ? C.profit : v < 0 ? C.loss : C.neutral);
const pnlHover = (v) => (v > 0 ? C.profitHover : v < 0 ? C.lossHover : C.text2);

export function dailyPnlChart(canvas, days) {
  applyDefaults();
  const values = days.map((d) => d.pnl);
  return new window.Chart(canvas, {
    type: "bar",
    data: { labels: days.map((d) => d.date), datasets: [barDataset(values, pnlColor, pnlHover)] },
    options: {
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: { maxTicksLimit: 8, maxRotation: 0, callback: (_v, i) => fmt.shortDate(days[i]?.date) },
        },
        y: moneyAxis(),
      },
      plugins: {
        tooltip: {
          callbacks: {
            title: (items) => fmt.longDate(days[items[0].dataIndex].date),
            label: (item) => t("Day P&L {pnl}", { pnl: fmt.signedMoney(item.parsed.y) }),
            footer: (items) => {
              const d = days[items[0].dataIndex];
              const partials = d.partials ? ` · ${tn(d.partials, "{n} partial exit", "{n} partial exits")}` : "";
              return `${tn(d.trades, "{n} trade", "{n} trades")} · ${t("{wins}W / {losses}L", { wins: d.wins, losses: d.losses })}${partials}`;
            },
          },
        },
      },
    },
  });
}

export function distributionChart(canvas, bins) {
  applyDefaults();
  const values = bins.map((b) => b.count);
  return new window.Chart(canvas, {
    type: "bar",
    data: {
      labels: bins.map((b) => b.label),
      datasets: [
        barDataset(
          values,
          (_v, i) => pnlColor(bins[i]?.center ?? 0),
          (_v, i) => pnlHover(bins[i]?.center ?? 0),
        ),
      ],
    },
    options: {
      scales: {
        x: { grid: { display: false }, border: { color: C.axis }, ticks: { maxRotation: 0, autoSkip: true } },
        y: { grid: { color: C.grid }, border: { display: false }, ticks: { precision: 0, maxTicksLimit: 6 }, beginAtZero: true },
      },
      plugins: {
        tooltip: {
          callbacks: {
            title: (items) => t("R-multiple {label}", { label: bins[items[0].dataIndex].label }),
            label: (item) => tn(item.parsed.y, "{n} trade", "{n} trades"),
            footer: (items) => {
              const b = bins[items[0].dataIndex];
              return b.count ? `${tn(b.wins, "{n} win", "{n} wins")} · ${tn(b.losses, "{n} loss", "{n} losses")}` : "";
            },
          },
        },
      },
    },
  });
}

// Labelled hairlines at several values (e.g. a prop challenge's profit target and starting balance).
const referenceLines = {
  id: "referenceLines",
  beforeDatasetsDraw(chart, _args, options) {
    const { ctx, chartArea, scales } = chart;
    for (const line of options.lines || []) {
      if (line.value === undefined || line.value === null) continue;
      const y = scales.y.getPixelForValue(line.value);
      if (y < chartArea.top || y > chartArea.bottom) continue;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.lineWidth = 1;
      ctx.strokeStyle = line.color || C.axis;
      ctx.stroke();
      ctx.fillStyle = line.color || C.muted;
      ctx.font = `11px ${window.Chart.defaults.font.family}`;
      ctx.textAlign = "right";
      ctx.fillText(line.label, chartArea.right - 6, y - 6);
      ctx.restore();
    }
  },
};

// Daily closing balance of a prop challenge with its (possibly trailing) drawdown floor.
export function balanceChart(canvas, days, { start, target }) {
  applyDefaults();
  const balances = days.map((d) => d.balance);
  const floors = days.map((d) => d.drawdown_floor);
  const last = days.length - 1;
  return new window.Chart(canvas, {
    type: "line",
    data: {
      labels: days.map((d) => d.date),
      datasets: [
        {
          label: t("Balance"),
          data: balances,
          borderColor: C.accent,
          borderWidth: 2,
          borderCapStyle: "round",
          borderJoinStyle: "round",
          tension: 0,
          fill: { target: { value: start }, above: C.accentWash, below: C.lossWash },
          pointRadius: (ctx) => (ctx.dataIndex === last ? 4 : 0),
          pointBackgroundColor: C.accent,
          pointBorderColor: C.surface,
          pointBorderWidth: 2,
          pointHoverRadius: 5,
          pointHitRadius: 12,
        },
        {
          label: t("Drawdown floor"),
          data: floors,
          borderColor: C.loss,
          borderWidth: 1,
          stepped: true,
          pointRadius: 0,
          pointHoverRadius: 0,
          fill: false,
        },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 16 } }, // room for the reference-line labels at the top of the scale
      scales: {
        x: {
          grid: { display: false },
          border: { color: C.axis },
          ticks: { maxTicksLimit: 8, maxRotation: 0, callback: (_v, i) => fmt.shortDate(days[i]?.date) },
        },
        y: {
          ...moneyAxis(),
          suggestedMin: Math.min(...floors, ...balances),
          suggestedMax: Math.max(target ?? start, ...balances),
        },
      },
      plugins: {
        referenceLines: {
          lines: [
            { value: target, label: t("Profit target"), color: C.profit },
            { value: start, label: t("Starting balance") },
          ],
        },
        tooltip: {
          callbacks: {
            title: (items) => fmt.date(days[items[0].dataIndex].date),
            label: (item) => `${item.dataset.label} ${fmt.money(item.parsed.y)}`,
            footer: (items) => t("Day P&L {pnl}", { pnl: fmt.signedMoney(days[items[0].dataIndex].pnl) }),
          },
        },
      },
    },
    plugins: [crosshair, referenceLines],
  });
}
