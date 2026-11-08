// "Someone else is on this video" (LIVESTACK-51 item 1).
//
// While the job page is open it tells the orchestrator, every 20 s, that
// this reviewer is on the video (through /review/api/jobs/{id}/presence),
// and says goodbye when the page closes. Each answer names the *other*
// reviewers on it, already as a pt-BR sentence.
//
// When the page opened with someone else already on it, the server drew
// the banner "Alessander está revisando este vídeo (desde 14:02)." with
// "Revisar mesmo assim", and made the form inert (no field, no button
// works) until that is chosen. Nothing is ever locked for good:
//   * "Revisar mesmo assim" frees the form at once;
//   * if the other reviewer leaves (or their pings stop: a closed laptop
//     expires after 45 s), the next answer frees it by itself.
// Once working, a reviewer only gets a quiet note when someone else joins.
import { sendJson } from "./common.js";

const PING_MILLISECONDS = 20000;

// What the page shows after an answer. `blocked`: the form is still
// waiting for "Revisar mesmo assim"; `sentence`: the other reviewers, or
// null when nobody else is on the video. Pure, so it is tested in Node
// (tests/review_ui/test_presence_page.py).
export function presenceView(blocked, sentence) {
  if (blocked && sentence) {
    return { blocked: true, banner: sentence, note: null };
  }
  // Not blocked any more (the other reviewer left), or never was: at most
  // a quiet note.
  return { blocked: false, banner: null, note: blocked ? null : sentence };
}

export function setupPresence({ form, jobElement }) {
  const banner = document.getElementById("presence-banner");
  const bannerText = document.getElementById("presence-banner-text");
  const note = document.getElementById("presence-note");
  if (!banner || !note) return;
  const url = `/review/api/jobs/${encodeURIComponent(jobElement.dataset.jobId)}/presence`;

  function apply(view) {
    form.inert = view.blocked;
    banner.hidden = !view.banner;
    if (view.banner) bannerText.textContent = view.banner;
    note.hidden = !view.note;
    note.textContent = view.note || "";
  }

  async function ping() {
    const result = await sendJson(url, "PUT", {});
    // Presence only informs: a failed ping changes nothing on the page.
    if (result.ok) apply(presenceView(form.inert, result.data.sentence));
  }

  banner.querySelector("[data-review-anyway]")?.addEventListener("click", () => {
    apply(presenceView(false, null));
    form.querySelector("input, select, button")?.focus();
  });
  setInterval(ping, PING_MILLISECONDS);
  // A tab in the background pings rarely (browsers slow its timers down):
  // say "still here" as soon as it is looked at again.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") ping();
  });
  // Leaving the page ends the visit at once instead of after 45 s.
  // keepalive lets the request finish while the page goes away.
  window.addEventListener("pagehide", () => {
    fetch(`${url}/leave`, {
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  });
}
