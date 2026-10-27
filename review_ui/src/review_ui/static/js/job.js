// Job page: wires the player, the review form, keyboard shortcuts and the
// auto-refreshing status strip / event log together.
import { errorMessage, isTyping, localizeTimes, sendJson, showFlash } from "./common.js";
import { setupPlayer } from "./player.js";
import { parseFragment, startPolling } from "./poll.js";
import { setupReviewForm } from "./review_form.js";

const jobElement = document.getElementById("job");
const form = document.getElementById("review-form");
const video = document.getElementById("player");
const player = setupPlayer({ video, form, jobElement });
const review = setupReviewForm({ form, jobElement });

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
      showFlash(`Proxy requested. ${result.data.waiting_reason}`, "warning");
    } else {
      showFlash("Proxy requested. This page shows it once a holder has uploaded it.", "success");
    }
  });
}

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
