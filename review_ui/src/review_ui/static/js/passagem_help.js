// "Outros vídeos de <prova>, carro <n>" on the job page (LIVESTACK-45).
//
// Whenever prova, car or passagem change in the form (saved or not), ask
// the UI for the panel again and put it in place: the list of the car's
// videos in filming order, the suggestion and the warnings. The server
// works all of that out (passagem_help_view.py); this script only swaps
// the HTML and the warning lists. The page never reloads, and the field
// is only filled in when the reviewer clicks "Usar passagem N".
import { errorMessage, localizeTimes, sendJson } from "./common.js";
import { t } from "./text.js";

const REFRESH_DELAY_MILLISECONDS = 300;

function fillList(list, warnings) {
  list.replaceChildren(
    ...warnings.map((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      return item;
    }),
  );
}

export function setupPassagemHelp({ form }) {
  const panel = document.getElementById("passagem-help");
  const fieldWarnings = document.getElementById("passagem-warnings");
  const readyWarnings = document.getElementById("ready-warnings");
  // What the server rendered with the page.
  let warnings = [...fieldWarnings.querySelectorAll("li")].map((item) => item.textContent);
  let timer = null;
  // Only the newest answer is shown: an older, slower one is ignored.
  let latestRequest = 0;

  function showWarnings(list) {
    warnings = list;
    fillList(fieldWarnings, list);
    fillList(readyWarnings.querySelector("ul"), list);
    readyWarnings.hidden = list.length === 0;
  }

  function formValues() {
    const params = new URLSearchParams();
    const prova = form.elements.prova.value.trim();
    const car = form.elements.car_number.value.trim();
    const passagem = form.elements.passagem.value.trim();
    if (prova) params.set("prova", prova);
    if (/^\d+$/.test(car)) params.set("car_number", car);
    if (/^\d+$/.test(passagem) && Number(passagem) >= 1) params.set("passagem", passagem);
    return params;
  }

  async function refresh() {
    const params = formValues();
    const request = ++latestRequest;
    if (!params.has("prova") || !params.has("car_number")) {
      panel.replaceChildren();
      showWarnings([]);
      return;
    }
    panel.setAttribute("aria-busy", "true");
    const result = await sendJson(`${panel.dataset.url}?${params}`, "GET");
    if (request !== latestRequest) return;
    panel.removeAttribute("aria-busy");
    if (!result.ok) {
      const message = document.createElement("p");
      message.classList.add("note", "error");
      message.textContent = t("script.passagens.failed", { error: errorMessage(result) });
      panel.replaceChildren(message);
      showWarnings([]);
      return;
    }
    panel.innerHTML = result.data.html;
    localizeTimes(panel);
    showWarnings(result.data.warnings);
  }

  function refreshSoon() {
    clearTimeout(timer);
    timer = setTimeout(refresh, REFRESH_DELAY_MILLISECONDS);
  }

  for (const name of ["prova", "car_number", "passagem"]) {
    form.elements[name].addEventListener("input", refreshSoon);
    form.elements[name].addEventListener("change", refreshSoon);
  }

  // "Usar passagem N": the only way the suggestion reaches the field.
  panel.addEventListener("click", (event) => {
    const button = event.target.closest("[data-use-passagem]");
    if (!button) return;
    form.elements.passagem.value = button.dataset.usePassagem;
    // Same as typing it: marks the form as changed and refreshes the panel.
    form.elements.passagem.dispatchEvent(new Event("input", { bubbles: true }));
  });

  return {
    // Before "Pronto" / "Salvar e reprocessar": true to go ahead. Warnings
    // never block; the reviewer confirms they saw them.
    confirmForward() {
      if (warnings.length === 0) return true;
      return window.confirm(t("script.passagens.confirm", { warnings: warnings.join("\n") }));
    },
  };
}
