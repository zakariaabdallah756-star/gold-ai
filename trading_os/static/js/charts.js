// Chart.js builders. Colours mirror the CSS tokens and were validated
// (contrast >= 3:1 on the surface; profit/loss separable under CVD).
// Profit/loss is never colour-only: bars sit above/below the zero line and
// every tooltip value is signed.

import { fmt } from "./format.js";

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
          ticks: { maxTicksLimit: 8, maxRotation: 0, callback: (_v, i) => (i === 0 ? "Start" : `#${points[i]?.n}`) },
        },
        y: moneyAxis(),
      },
      plugins: {
        referenceLine: { value: base, label: "Starting balance" },
        tooltip: {
          callbacks: {
            title: (items) => {
              const p = points[items[0].dataIndex];
              return p.n === 0 ? "Starting balance" : `Trade #${p.n} · ${p.symbol} · ${fmt.date(p.t)}`;
            },
            label: (item) => `Equity ${fmt.money(item.parsed.y)}`,
            footer: (items) => {
              const p = points[items[0].dataIndex];
              return p.pnl === null ? "" : `Trade P&L ${fmt.signedMoney(p.pnl)}`;
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
            label: (item) => `Day P&L ${fmt.signedMoney(item.parsed.y)}`,
            footer: (items) => {
              const d = days[items[0].dataIndex];
              return `${d.trades} trade${d.trades === 1 ? "" : "s"} · ${d.wins}W / ${d.losses}L`;
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
            title: (items) => `R-multiple ${bins[items[0].dataIndex].label}`,
            label: (item) => `${item.parsed.y} trade${item.parsed.y === 1 ? "" : "s"}`,
            footer: (items) => {
              const b = bins[items[0].dataIndex];
              return b.count ? `${b.wins} win${b.wins === 1 ? "" : "s"} · ${b.losses} loss${b.losses === 1 ? "" : "es"}` : "";
            },
          },
        },
      },
    },
  });
}
