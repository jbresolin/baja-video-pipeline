// Clips page: copy buttons for studio paths, and the prova filter.
import { autoSubmit, copyText } from "./common.js";
import { t } from "./text.js";

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    try {
      await copyText(button.dataset.copy);
      button.textContent = t("script.copied");
    } catch {
      button.textContent = t("script.copy_failed");
    }
    setTimeout(() => {
      button.textContent = t("script.copy");
    }, 1500);
  });
}

autoSubmit(document.getElementById("clips-filters"));
