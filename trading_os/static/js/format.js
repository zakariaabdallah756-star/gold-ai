// Number and date formatting. Trade times are naive account-local timestamps,
// so they are parsed as local time and never shifted between zones.

let currency = "USD";
let locale; // undefined = the browser's formats; set from the interface language (i18n.js)
let moneyFormat = makeMoney("USD");

function makeMoney(code) {
  return new Intl.NumberFormat(locale, { style: "currency", currency: code, minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function setCurrency(code) {
  if (code && code !== currency) {
    currency = code;
    moneyFormat = makeMoney(code);
  }
}

/** Number and date formats of ``tag`` (e.g. "it-IT"); undefined = the browser's. */
export function setLocale(tag) {
  locale = tag;
  moneyFormat = makeMoney(currency);
  dateFormat = new Intl.DateTimeFormat(locale, { day: "2-digit", month: "short", year: "numeric" });
  shortDateFormat = new Intl.DateTimeFormat(locale, { day: "2-digit", month: "short" });
  timeFormat = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" });
  weekdayFormat = new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

export function currentLocale() {
  return locale;
}

const MINUS = "−";
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
let dateFormat = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short", year: "numeric" });
let shortDateFormat = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" });
let timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
let weekdayFormat = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });

/** ``value.toFixed(digits)`` with the language's decimal separator (identical to toFixed in English). */
function fixed(value, digits) {
  if (locale === undefined) return value.toFixed(digits);
  return value.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false });
}

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
  /** A round amount without cents, e.g. an account size in a name ("$10,000", "10.000 USD"). */
  wholeMoney(value) {
    if (!isNum(value)) return "—";
    return new Intl.NumberFormat(locale, { style: "currency", currency, minimumFractionDigits: 0, maximumFractionDigits: 0 })
      .format(value)
      .replace("-", MINUS);
  },
  compactMoney(value) {
    if (!isNum(value)) return "";
    return new Intl.NumberFormat(locale, { style: "currency", currency, notation: "compact", maximumFractionDigits: 1 })
      .format(value)
      .replace("-", MINUS);
  },
  num(value, digits = 2) {
    if (!isNum(value)) return "—";
    return value.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }).replace("-", MINUS);
  },
  price(value) {
    if (!isNum(value)) return "—";
    const digits = Math.abs(value) < 10 ? 5 : Math.abs(value) < 1000 ? 3 : 2;
    return value.toLocaleString(locale, { minimumFractionDigits: 0, maximumFractionDigits: digits });
  },
  lots(value) {
    if (!isNum(value)) return "—";
    return value.toLocaleString(locale, { maximumFractionDigits: 3 });
  },
  pct(value, digits = 1) {
    if (!isNum(value)) return "—";
    return `${fixed(value, digits).replace("-", MINUS)}%`;
  },
  rr(value) {
    return isNum(value) ? `1:${fixed(value, 2)}` : "—";
  },
  r(value) {
    if (!isNum(value)) return "—";
    return `${value > 0 ? "+" : value < 0 ? MINUS : ""}${fixed(Math.abs(value), 2)}R`;
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
    return `${fixed(minutes / 1440, 1)}d`;
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
