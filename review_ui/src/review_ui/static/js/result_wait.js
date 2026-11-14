// "Esperar o resultado oficial (até N min)" next to "Pronto" (LIVESTACK-62).
//
// The box (#result-wait) is on the job page whenever "Pronto" or "Salvar e
// reprocessar" is, ticked by default. It is shown only while something from
// the results would go into the clip: "Mostrar resultado da passagem" or
// "Mostrar cronômetro" ticked. Those boxes come and go with the prova
// (js/pass_blocks.js), so this follows every change and every block swap.
// A hidden box is also disabled, and js/review_form.js leaves a disabled
// box out of the request: the job is then rendered at once, as before.
//
// The orchestrator still decides whether waiting makes sense (nothing
// fetched yet, no largada for the clock, ...); this only asks.

// True when the result line or the clock is ticked on the form. A box that
// is not on the page (prova without results) counts as off.
export function resultOrClockIsOn(form) {
  return Boolean(form.elements.show_pass_result?.checked || form.elements.show_clock?.checked);
}

// Shows or hides (and enables or disables) the box now and after every change.
export function setupResultWait({ form }) {
  const box = form.querySelector("#result-wait");
  if (!box) return;
  const input = box.querySelector("input");
  function update() {
    const shown = resultOrClockIsOn(form);
    box.hidden = !shown;
    input.disabled = !shown;
  }
  // Ticking a toggle, and the result and clock blocks swapped in or out.
  for (const type of ["change", "pass-blocks-changed"]) form.addEventListener(type, update);
  update();
}
