// Fixed values the server stores in English (constants.EMOTIONS and MOODS), shown in the interface language.
// Anything else (a value typed by the user, an older journal entry) is shown as stored.

import { N_, t } from "./i18n.js";

const VALUE_LABELS = new Map([
  ["Calm", N_("Calm")],
  ["Confident", N_("Confident")],
  ["Focused", N_("Focused")],
  ["Anxious", N_("Anxious")],
  ["FOMO", N_("FOMO")],
  ["Greedy", N_("Greedy")],
  ["Fearful", N_("Fearful")],
  ["Impatient", N_("Impatient")],
  ["Revenge", N_("Revenge")],
  ["Bored", N_("Bored")],
  ["Euphoric", N_("Euphoric")],
  ["Tired", N_("Tired")],
  ["Satisfied", N_("Satisfied")],
  ["Neutral", N_("Neutral")],
  ["Frustrated", N_("Frustrated")],
  ["Tilted", N_("Tilted")],
  ["Exhausted", N_("Exhausted")],
]);

/** An emotion or mood in the interface language. */
export function valueLabel(value) {
  return VALUE_LABELS.has(value) ? t(VALUE_LABELS.get(value)) : value;
}
