// Number and date formatting. Trade times are naive account-local timestamps,
// so they are parsed as local time and never shifted between zones.

let currency = "USD";
let moneyFormat = makeMoney("USD");

function makeMoney(code) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: code, minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function setCurrency(code) {
  if (code && code !== currency) {
    currency = code;
    moneyFormat = makeMoney(code);
  }
}

const MINUS = "−";
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const dateFormat = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", year: "numeric" });
const shortDateFormat = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" });
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const weekdayFormat = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });

function parseLocal(iso) {
  if (!iso) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  return new Date(iso);
}

export const fmt = {
  money(value) {
    if (!isNum(value)) return "—";
    return moneyFormat.format(value).replace("-", MINUS);
  },
  signedMoney(value) {
    if (!isNum(value)) return "—";
    const text = moneyFormat.format(Math.abs(value));
    if (Math.abs(value) < 0.005) return text;
    return (value > 0 ? "+" : MINUS) + text;
  },
  compactMoney(value) {
    if (!isNum(value)) return "";
    return new Intl.NumberFormat(undefined, { style: "currency", currency, notation: "compact", maximumFractionDigits: 1 })
      .format(value)
      .replace("-", MINUS);
  },
  num(value, digits = 2) {
    if (!isNum(value)) return "—";
    return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits }).replace("-", MINUS);
  },
  price(value) {
    if (!isNum(value)) return "—";
    const digits = Math.abs(value) < 10 ? 5 : Math.abs(value) < 1000 ? 3 : 2;
    return value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: digits });
  },
  lots(value) {
    if (!isNum(value)) return "—";
    return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
  },
  pct(value, digits = 1) {
    if (!isNum(value)) return "—";
    return `${value.toFixed(digits).replace("-", MINUS)}%`;
  },
  rr(value) {
    return isNum(value) ? `1:${value.toFixed(2)}` : "—";
  },
  r(value) {
    if (!isNum(value)) return "—";
    return `${value > 0 ? "+" : value < 0 ? MINUS : ""}${Math.abs(value).toFixed(2)}R`;
  },
  date(iso) {
    const d = parseLocal(iso);
    return d ? dateFormat.format(d) : "—";
  },
  shortDate(iso) {
    const d = parseLocal(iso);
    return d ? shortDateFormat.format(d) : "—";
  },
  longDate(iso) {
    const d = parseLocal(iso);
    return d ? weekdayFormat.format(d) : "—";
  },
  time(iso) {
    const d = parseLocal(iso);
    return d ? timeFormat.format(d) : "";
  },
  datetime(iso) {
    const d = parseLocal(iso);
    return d ? `${dateFormat.format(d)} ${timeFormat.format(d)}` : "—";
  },
  duration(minutes) {
    if (!isNum(minutes)) return "—";
    if (minutes < 60) return `${Math.round(minutes)}m`;
    if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}h ${Math.round(minutes % 60)}m`;
    return `${(minutes / 1440).toFixed(1)}d`;
  },
};

export function pnlClass(value) {
  if (!isNum(value) || Math.abs(value) < 0.005) return "flat";
  return value > 0 ? "pos" : "neg";
}

/** Format a Date as the value of an <input type="datetime-local">. */
export function toInputDateTime(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function toInputDate(date) {
  return toInputDateTime(date).slice(0, 10);
}
