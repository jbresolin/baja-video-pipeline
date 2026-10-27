// Clips page: copy buttons for studio paths, and the prova filter.
import { autoSubmit, copyText } from "./common.js";

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    try {
      await copyText(button.dataset.copy);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }
    setTimeout(() => {
      button.textContent = "Copy";
    }, 1500);
  });
}

autoSubmit(document.getElementById("clips-filters"));
