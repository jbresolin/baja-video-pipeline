// Dates and numbers the Brazilian way, exactly like review_ui/formatting.py:
// "05/11/2026 09:23:41" on a 24-hour clock in São Paulo time, "há 5 min (09:18)",
// and a decimal comma ("1:02,500"). tests/review_ui/test_formats.py runs both
// with the same inputs and expects the same strings.
import { t } from "./text.js";

const TIME_ZONE = "America/Sao_Paulo";

// The parts of a date in São Paulo, always two digits (four for the year).
const partsFormatter = new Intl.DateTimeFormat("pt-BR", {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function partsOf(date) {
  const parts = {};
  for (const { type, value } of partsFormatter.formatToParts(date)) parts[type] = value;
  return parts;
}

// "05/11/2026 09:23:41" (formatting.clock_text).
export function clockText(date) {
  const p = partsOf(date);
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}:${p.second}`;
}

// "09:23" (formatting.short_clock_text).
export function shortClockText(date) {
  const p = partsOf(date);
  return `${p.hour}:${p.minute}`;
}

// "há 12 s", "há 5 min", "há 3 h", "há 2 d" (formatting.age_text). Whole
// units, rounded down, like the server.
export function ageText(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  if (seconds < 60) return t("script.time.seconds_ago", { n: seconds });
  if (seconds < 3600) return t("script.time.minutes_ago", { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t("script.time.hours_ago", { n: Math.floor(seconds / 3600) });
  return t("script.time.days_ago", { n: Math.floor(seconds / 86400) });
}

// "há 5 min (09:18)" (formatting.relative_time_text).
export function relativeTimeText(date, now) {
  return t("script.time.relative_with_clock", {
    relative: ageText(now.getTime() - date.getTime()),
    clock: shortClockText(date),
  });
}

// A position in the video with milliseconds: 62.5 → "1:02,500" (formatting.format_timecode).
export function formatTimecode(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = (seconds - minutes * 60).toFixed(3).padStart(6, "0").replace(".", ",");
  return `${minutes}:${rest}`;
}
