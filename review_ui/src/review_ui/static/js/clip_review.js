// Watching and downloading the rendered clip (LIVESTACK-52), on the job
// page and the clips page.
//
// "Baixar clipe": like "Baixar original", the link is ready at once while
// the server still has the clip in its outbox; otherwise a PC that has it
// uploads it first ("preparando…"), and we ask again every few seconds
// until the link is there. Each button carries data-clip-download="<job id>"
// and is followed by its own status line [data-clip-download-status].
//
// "Ver clipe" on the clips page: one <dialog> with one player; each row's
// button puts its preview in it. The video only loads when opened.
import { errorMessage, sendJson, showFlash } from "./common.js";
import { t } from "./text.js";

const CLIP_DOWNLOAD_POLL_MILLISECONDS = 5000;

// Shows the answer in `status`; true when nothing more will change.
export function showClipDownload(status, answer) {
  status.replaceChildren();
  if (answer.state === "ready") {
    const link = document.createElement("a");
    link.href = answer.download_url;
    link.download = answer.file_name;
    link.textContent = answer.file_name;
    status.append(t("script.clip_download.ready"), " ", link);
    return true;
  }
  if (answer.state === "preparing") {
    status.textContent = t("script.clip_download.preparing");
    return false;
  }
  status.textContent = t("script.clip_download.no_source");
  return true;
}

function clipDownloadUrl(jobId) {
  return `/review/api/jobs/${encodeURIComponent(jobId)}/clip-download`;
}

async function followClipDownload(status, url) {
  const result = await sendJson(url, "GET");
  if (!result.ok) return showFlash(errorMessage(result), "error");
  if (!showClipDownload(status, result.data)) {
    setTimeout(() => followClipDownload(status, url), CLIP_DOWNLOAD_POLL_MILLISECONDS);
  }
}

export function setupClipDownloads(root = document) {
  for (const button of root.querySelectorAll("[data-clip-download]")) {
    const status = button.parentElement.querySelector("[data-clip-download-status]");
    button.addEventListener("click", async () => {
      button.disabled = true;
      const url = clipDownloadUrl(button.dataset.clipDownload);
      const result = await sendJson(url, "POST", {});
      button.disabled = false;
      if (!result.ok) return showFlash(errorMessage(result), "error");
      if (!showClipDownload(status, result.data)) {
        setTimeout(() => followClipDownload(status, url), CLIP_DOWNLOAD_POLL_MILLISECONDS);
      }
    });
  }
}

export function setupPreviewDialog(dialog) {
  if (!dialog) return;
  const video = dialog.querySelector("video");
  const title = dialog.querySelector("[data-preview-title]");
  for (const button of document.querySelectorAll("[data-preview-url]")) {
    button.addEventListener("click", () => {
      title.textContent = button.dataset.previewTitle;
      video.src = button.dataset.previewUrl;
      dialog.showModal();
      video.play().catch(() => {});
    });
  }
  // Closing (button, Esc) stops the video and frees the connection.
  dialog.addEventListener("close", () => {
    video.pause();
    video.removeAttribute("src");
    video.load();
  });
}
