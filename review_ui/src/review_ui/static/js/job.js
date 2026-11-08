// Job page: wires the player, the review form, keyboard shortcuts and the
// auto-refreshing status strip / event log together.
import { errorMessage, isTyping, localizeTimes, sendJson, showFlash } from "./common.js";
import { setupClipDownloads } from "./clip_review.js";
import { setupClockLate } from "./clock_late.js";
import { setupOverlayPreview } from "./overlay_preview.js";
import { setupPassBlocks } from "./pass_blocks.js";
import { setupPlayer } from "./player.js";
import { parseFragment, startPolling } from "./poll.js";
import { setupPassagemHelp } from "./passagem_help.js";
import { setupPresence } from "./presence.js";
import { setupRenderOptions } from "./render_options.js";
import { setupReviewForm } from "./review_form.js";
import { t } from "./text.js";

// Player keys that change the form (cut marks and their nudges).
const MARK_KEYS = new Set(["i", "o", "-", "_", "=", "+"]);

const jobElement = document.getElementById("job");
const form = document.getElementById("review-form");
const video = document.getElementById("player");
const player = setupPlayer({ video, form, jobElement });
const passagemHelp = setupPassagemHelp({ form });
setupRenderOptions({ form, video });
// "Resultado da passagem" and "Cronômetro" follow prova, car and passagem (LIVESTACK-53).
setupPassBlocks({ form });
// With a clock the result appears when it stops: warn when the cut ends first (LIVESTACK-53).
setupClockLate({ form });
// "Pré-visualizar overlay" (LIVESTACK-48): the overlay and its clock over the proxy.
setupOverlayPreview({ video, form, player });
// Who else is on this video (LIVESTACK-51); may hold the form until chosen.
setupPresence({ form, jobElement });
const review = setupReviewForm({
  form,
  jobElement,
  confirmForward: () => passagemHelp.confirmForward(),
});

document.addEventListener("keydown", (event) => {
  const withCommand = event.ctrlKey || event.metaKey;
  if (withCommand && event.key.toLowerCase() === "s") {
    event.preventDefault();
    review.run("save");
    return;
  }
  if (withCommand && event.key === "Enter") {
    event.preventDefault();
    review.runPrimary();
    return;
  }
  if (event.key === "Escape" && isTyping(event)) {
    event.target.blur();
    return;
  }
  if (isTyping(event) || withCommand || event.altKey) return;
  if (event.key === "?") {
    const shortcuts = document.getElementById("shortcuts");
    shortcuts.open = !shortcuts.open;
    return;
  }
  // While the form waits for "Revisar mesmo assim", the keys that change it
  // (marks, nudges) do nothing; playing and seeking still work.
  if (form.inert && MARK_KEYS.has(event.key)) return;
  if (player.handleKey(event)) event.preventDefault();
});

// Ask a holder to render the proxy again (evicted or broken proxy).
for (const button of document.querySelectorAll("[data-regenerate-proxy]")) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    const jobId = encodeURIComponent(jobElement.dataset.jobId);
    const result = await sendJson(`/review/api/jobs/${jobId}/regenerate-proxy`, "POST", {});
    button.disabled = false;
    if (!result.ok) {
      showFlash(errorMessage(result), "error");
    } else if (result.data.waiting_reason) {
      showFlash(t("script.proxy_requested_waiting", { reason: result.data.waiting_text }), "warning");
    } else {
      showFlash(t("script.proxy_requested"), "success");
    }
  });
}

// "Baixar original" (LIVESTACK-41 item 15): ready at once while the server
// still has the raw; otherwise a PC that holds it uploads it first, and we
// ask again every few seconds until the link is there.
const ORIGINAL_POLL_MILLISECONDS = 5000;
const originalStatus = document.getElementById("original-status");

function showOriginal(answer) {
  originalStatus.replaceChildren();
  if (answer.state === "ready") {
    const link = document.createElement("a");
    link.href = answer.download_url;
    link.download = answer.file_name;
    link.textContent = answer.file_name;
    originalStatus.append(t("script.original.ready"), " ", link);
    return true;
  }
  if (answer.state === "preparing") {
    originalStatus.textContent = t("script.original.preparing");
    return false;
  }
  originalStatus.textContent = t("script.original.no_holder");
  return true;
}

async function followOriginal(url) {
  const result = await sendJson(url, "GET");
  if (!result.ok) return showFlash(errorMessage(result), "error");
  if (!showOriginal(result.data)) setTimeout(() => followOriginal(url), ORIGINAL_POLL_MILLISECONDS);
}

for (const button of document.querySelectorAll("[data-original]")) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    const url = `/review/api/jobs/${encodeURIComponent(jobElement.dataset.jobId)}/original`;
    const result = await sendJson(url, "POST", {});
    button.disabled = false;
    if (!result.ok) return showFlash(errorMessage(result), "error");
    if (!showOriginal(result.data)) setTimeout(() => followOriginal(url), ORIGINAL_POLL_MILLISECONDS);
  });
}

setupClipDownloads(document);

for (const button of document.querySelectorAll("[data-reload]")) {
  button.addEventListener("click", () => window.location.reload());
}

// Refresh the status strip and event log. The form is never touched, so a
// refresh cannot overwrite what the reviewer is typing; if the job changed
// underneath them, a banner offers a reload instead.
startPolling(document.getElementById("job-panel"), (html) => {
  const fragment = parseFragment(html);
  for (const id of ["status-strip", "event-log"]) {
    const fresh = fragment.getElementById(id);
    if (fresh) document.getElementById(id).replaceWith(fresh);
  }
  localizeTimes(document);
  const latestVersion = Number(document.getElementById("status-strip").dataset.version);
  document.getElementById("stale-banner").hidden = !(latestVersion > review.version());
});
