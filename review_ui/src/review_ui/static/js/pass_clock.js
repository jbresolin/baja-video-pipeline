// The running clock's rule, for the overlay preview (LIVESTACK-48).
//
// The same rule as shared/pass_clock.py (Python, and the ffmpeg expression
// the worker renders with): the clock starts at 0 on the largada, runs, and
// freezes on the official time; before the largada it is not shown. At
// every moment it shows min(elapsed, official) rounded down to the
// decimals, with a decimal comma ("0,0", "9,9", "10,0", "66,0").
//
// Times are compared in whole microseconds, each rounded half up on its
// own, and the count is kept in whole clock units (66 s with 1 decimal =
// 660), so no float rounding can make the preview differ from the clip.
// tests/fixtures/pass_clock/vectors.json holds cases that this file and
// the Python rule must both get right (test_pass_clock_rule.py runs both).

const MICROSECONDS_PER_SECOND = 1_000_000;

export function microseconds(seconds) {
  return Math.floor(seconds * MICROSECONDS_PER_SECOND + 0.5);
}

// Microseconds since the largada (negative before it).
export function elapsedMicroseconds(rawTimeSeconds, largadaSeconds) {
  return microseconds(rawTimeSeconds) - microseconds(largadaSeconds);
}

// The clock's count at `elapsedUs`, in units of its last decimal; null before the largada.
export function clockUnits(elapsedUs, officialUnits, decimals) {
  if (elapsedUs < 0) return null;
  const perSecond = 10 ** decimals;
  // Whole numbers below 2^53 throughout, so the division is exact enough
  // for Math.floor (the true quotient is never within 1e-6 of a whole
  // number unless it is one).
  return Math.min(Math.floor((elapsedUs * perSecond) / MICROSECONDS_PER_SECOND), officialUnits);
}

// 660 units with 1 decimal → "66,0"; 5 with 0 decimals → "5".
export function clockText(units, decimals) {
  const perSecond = 10 ** decimals;
  const whole = Math.floor(units / perSecond);
  if (decimals === 0) return String(whole);
  const fraction = String(units % perSecond).padStart(decimals, "0");
  return `${whole},${fraction}`;
}

// The clock's text at a moment of the raw video, or null when it is not shown.
export function clockTextAt(rawTimeSeconds, largadaSeconds, officialUnits, decimals) {
  const units = clockUnits(
    elapsedMicroseconds(rawTimeSeconds, largadaSeconds),
    officialUnits,
    decimals,
  );
  return units === null ? null : clockText(units, decimals);
}
