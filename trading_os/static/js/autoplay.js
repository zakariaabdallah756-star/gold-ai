// Carousel autoplay timing (v1.6). No DOM and no imports, so tests can run it in node.
//
// createAutoplay({ intervalMs, onAdvance }) calls onAdvance() every intervalMs while nothing holds it. Every
// reason that stops rotation (hover, focus, touch, a hidden tab, the pause button...) is tracked on its own:
// rotation resumes only once all of them are gone, with the time that was left on the current slide, so the
// progress bar and the timer stay in step. restart() gives a new slide its full interval. setEnabled(false)
// stops rotation for good reasons that are not pauses (one slide only, reduced motion).
//
// The clock (now / setTimeout / clearTimeout) can be replaced, which is how the tests drive time.

export const DEFAULT_INTERVAL_MS = 4000;
export const MIN_INTERVAL_MS = 3000;
export const MAX_INTERVAL_MS = 60000;

const realClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (id) => clearTimeout(id),
};

/** Seconds from the server -> milliseconds within the allowed range (DEFAULT_INTERVAL_MS when unreadable). */
export function intervalMs(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_INTERVAL_MS;
  return Math.min(MAX_INTERVAL_MS, Math.max(MIN_INTERVAL_MS, Math.round(value * 1000)));
}

export function createAutoplay({ intervalMs: interval = DEFAULT_INTERVAL_MS, onAdvance, onChange, enabled = true, clock = realClock } = {}) {
  const reasons = new Set();
  const later = new Map(); // reason -> pending timer that lifts it
  let canRun = Boolean(enabled);
  let remaining = interval;
  let startedAt = 0;
  let timer = null;
  let running = false;
  let alive = true;

  function stopTimer() {
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
  }

  function sync() {
    const shouldRun = alive && canRun && reasons.size === 0;
    if (running && !shouldRun) {
      stopTimer();
      remaining = Math.max(0, remaining - (clock.now() - startedAt));
      running = false;
    } else if (!running && shouldRun) {
      startedAt = clock.now();
      timer = clock.setTimeout(fire, remaining);
      running = true;
    }
    onChange?.(snapshot());
  }

  function fire() {
    timer = null;
    running = false;
    remaining = interval;
    if (!alive) return;
    // onAdvance normally shows the next slide and calls restart(); start the next interval in any case.
    onAdvance?.();
    if (alive && !running) sync();
  }

  function cancelLater(reason) {
    const pending = later.get(reason);
    if (pending !== undefined) clock.clearTimeout(pending);
    later.delete(reason);
  }

  function snapshot() {
    const left = running ? Math.max(0, remaining - (clock.now() - startedAt)) : remaining;
    return { running, enabled: canRun, remainingMs: left, intervalMs: interval, reasons: [...reasons].sort() };
  }

  if (canRun) {
    // Start right away, without a change event: the page may still be building what onChange updates.
    startedAt = clock.now();
    timer = clock.setTimeout(fire, remaining);
    running = true;
  }

  return {
    /** Stop rotating for ``reason`` until resume(reason). */
    pause(reason) {
      if (!alive) return;
      cancelLater(reason);
      reasons.add(reason);
      sync();
    },
    /** Lift ``reason``; rotation restarts once no reason is left. */
    resume(reason) {
      if (!alive) return;
      cancelLater(reason);
      reasons.delete(reason);
      sync();
    },
    /** Lift ``reason`` after ``ms`` (a tap on a phone has no "pointer left" to resume on). */
    resumeAfter(reason, ms) {
      if (!alive || !reasons.has(reason)) return;
      cancelLater(reason);
      later.set(
        reason,
        clock.setTimeout(() => {
          later.delete(reason);
          if (!alive) return;
          reasons.delete(reason);
          sync();
        }, ms),
      );
    },
    toggle(reason) {
      if (reasons.has(reason)) this.resume(reason);
      else this.pause(reason);
      return reasons.has(reason);
    },
    isPaused(reason) {
      return reason === undefined ? !running : reasons.has(reason);
    },
    /** A new slide is showing: give it a full interval. */
    restart() {
      if (!alive) return;
      stopTimer();
      running = false;
      remaining = interval;
      sync();
    },
    setEnabled(value) {
      if (!alive) return;
      canRun = Boolean(value);
      sync();
    },
    state: snapshot,
    dispose() {
      if (!alive) return;
      stopTimer();
      for (const reason of [...later.keys()]) cancelLater(reason);
      running = false;
      alive = false;
    },
  };
}
