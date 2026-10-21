// Interface translation. The English text is the key: t("Saved {n} trades", { n: 3 }) returns the text in
// the language chosen under Trading Plan -> Account (catalogs: trading_os/locales/<lang>/web/*.json), or the
// English text when no translation exists. Placeholders are {name}; values are inserted as given.
//
// Translate at render time, never at module load (the language is known only after the plan has loaded).
// Labels in constant tables are marked with N_("...") so the catalog check finds them, then shown with t().

import { setLocale } from "./format.js";

const LOCALES = { en: undefined, it: "it-IT" }; // undefined = the browser's own formats (unchanged English UI)
let current = "en";
let messages = {};

export const N_ = (text) => text;

export function t(text, params) {
  const template = messages[text] ?? text;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, key) => (key in params ? String(params[key]) : match));
}

/** Singular or plural form by count; {n} is the count. */
export function tn(count, singular, plural, params) {
  return t(count === 1 ? singular : plural, { n: count, ...params });
}

export function language() {
  return current;
}

/** Switch the interface language: loads its catalog and the matching number/date formats. */
export async function useLanguage(code) {
  const lang = code in LOCALES ? code : "en";
  let loaded = {};
  if (lang !== "en") {
    try {
      const response = await fetch(`/api/i18n/${lang}`, { headers: { Accept: "application/json" } });
      if (response.ok) loaded = (await response.json()).messages || {};
    } catch {
      loaded = {}; // offline or server error: keep English rather than a broken page
    }
  }
  current = lang;
  messages = loaded;
  document.documentElement.lang = lang;
  setLocale(LOCALES[lang]);
  return lang;
}
