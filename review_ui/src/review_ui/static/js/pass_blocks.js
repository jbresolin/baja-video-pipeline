// "Resultado da passagem" and "Cronômetro" follow the form (LIVESTACK-53).
//
// The page used to build both blocks from the saved job, so on a new
// video they only appeared after a save and a reload, and the largada
// could not be marked before that. Now, whenever prova, car or passagem
// change (saved or not), and after a save, the page asks the UI for both
// blocks for what the form says (GET /review/api/jobs/{id}/pass-blocks;
// the orchestrator computes them with the same functions as the saved
// job's, nothing is stored) and puts them in place:
//
//   * a block the answer has is shown; the fields the reviewer already set
//     on this page (a box ticked, a largada typed or marked) keep their
//     values, the others take the server's (a new prova's default);
//   * a block the answer does not have (a prova without a clock) is taken
//     out of the form and kept aside: its fields are then not sent, as for
//     any prova without a clock, and a largada typed in it comes back if
//     the reviewer goes back to a prova with a clock.
//
// After each swap the form gets a "pass-blocks-changed" event, whose
// detail is the overlay preview's result line and official time: the
// player redraws the largada line and the preview its texts.
import { sendJson } from "./common.js";

const REFRESH_DELAY_MILLISECONDS = 300;
// The fields of the two blocks the review form sends.
export const BLOCK_FIELDS = ["show_pass_result", "largada_s", "show_clock"];

// The query for what the form says now; only values that are complete
// (an empty field is left out: the saved value, as when saving).
export function formQuery({ prova, carNumber, passagem }) {
  const params = new URLSearchParams();
  if (prova.trim()) params.set("prova", prova.trim());
  if (/^\d+$/.test(carNumber.trim())) params.set("car_number", carNumber.trim());
  if (/^\d+$/.test(passagem.trim()) && Number(passagem) >= 1) params.set("passagem", passagem.trim());
  return params;
}

// Give `incoming` (a block from the server) the values the reviewer set in
// `current` (the block shown or kept aside): fields named in `touched` keep
// what the reviewer typed or ticked, the others stay as the server drew them.
// Both lists are of inputs: { name, type, value, checked }.
export function carryOverFields(currentFields, incomingFields, touched) {
  for (const field of incomingFields) {
    if (!touched.has(field.name)) continue;
    const current = currentFields.find((candidate) => candidate.name === field.name);
    if (!current) continue;
    if (field.type === "checkbox") field.checked = current.checked;
    else field.value = current.value;
  }
}

// One slot (#pass-result-slot or #pass-clock-slot) and the block it holds
// or keeps aside. `makeBlock(html)` turns the server's HTML into an element.
export function slotKeeper(slot, makeBlock) {
  let block = slot.firstElementChild;
  return {
    // Show the server's block (html) or take it out (null). Returns whether
    // the page changed shape (a block came or went).
    update(html, touched) {
      const wasShown = block !== null && block.parentNode === slot;
      if (html === null) {
        if (wasShown) slot.replaceChildren();
        return wasShown;
      }
      const incoming = makeBlock(html);
      if (block) carryOverFields(inputsOf(block), inputsOf(incoming), touched);
      slot.replaceChildren(incoming);
      block = incoming;
      return !wasShown;
    },
  };
}

function inputsOf(block) {
  return [...block.querySelectorAll("input")];
}

function blockFromHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
}

export function setupPassBlocks({ form }) {
  const resultSlot = document.getElementById("pass-result-slot");
  const clockSlot = document.getElementById("pass-clock-slot");
  if (!resultSlot || !clockSlot) return;
  const url = resultSlot.dataset.url;
  const result = slotKeeper(resultSlot, blockFromHtml);
  const clock = slotKeeper(clockSlot, blockFromHtml);
  // The block fields the reviewer changed since the page loaded or saved.
  const touched = new Set();
  let timer = null;
  // Only the newest answer is used: an older, slower one is ignored.
  let latestRequest = 0;

  async function refresh() {
    const request = ++latestRequest;
    const params = formQuery({
      prova: form.elements.prova.value,
      carNumber: form.elements.car_number.value,
      passagem: form.elements.passagem.value,
    });
    const answer = await sendJson(`${url}?${params}`, "GET");
    // On a failure the blocks stay as they are; the next change asks again.
    if (request !== latestRequest || !answer.ok) return;
    result.update(answer.data.result_html, touched);
    clock.update(answer.data.clock_html, touched);
    form.dispatchEvent(new CustomEvent("pass-blocks-changed", { detail: answer.data.preview }));
  }

  function refreshSoon() {
    clearTimeout(timer);
    timer = setTimeout(refresh, REFRESH_DELAY_MILLISECONDS);
  }

  for (const name of ["prova", "car_number", "passagem"]) {
    form.elements[name].addEventListener("input", refreshSoon);
    form.elements[name].addEventListener("change", refreshSoon);
  }
  // Marking the largada (button, l key) and "Limpar" send "input" too.
  for (const type of ["input", "change"]) {
    form.addEventListener(type, (event) => {
      if (BLOCK_FIELDS.includes(event.target.name)) touched.add(event.target.name);
    });
  }
  // Saved: the server has the values of the fields on the page now, and
  // the status lines (what will be burned in) may have changed. A field of
  // a block kept aside was not sent, so the reviewer's value stays.
  form.addEventListener("review-saved", () => {
    for (const name of [...touched]) {
      if (form.elements[name]) touched.delete(name);
    }
    refresh();
  });
}
