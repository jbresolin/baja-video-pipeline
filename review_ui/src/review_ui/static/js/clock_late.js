// "O clipe termina antes do cronômetro parar" on the job page (LIVESTACK-53).
//
// With a running clock, the result pill appears when the clock stops
// (largada + official time). If the cut ends before that, the clip never
// shows its result: the clock block says so, from the form's own fields
// (largada, end of the cut, the two toggles), so it follows every edit
// before a save. It never blocks anything.
//
// The official time comes from the clock block (data-official-units,
// data-decimals) and "is there a result line" from the result block
// (data-has-text); both blocks are redrawn by js/pass_blocks.js.
import { clipEndsBeforeFreeze } from "./pass_clock.js";

// True when the warning applies: a clock and a result are both going into
// the clip, and the cut ends before the clock stops. Missing values
// (no largada, no end of the cut, no official time yet) never warn.
export function resultNeverAppears({ officialUnits, decimals, largada, cutOut, showClock, showResult }) {
  if (!showClock || !showResult) return false;
  if (officialUnits === null || largada === null || cutOut === null) return false;
  return clipEndsBeforeFreeze(cutOut, largada, officialUnits, decimals);
}

function fieldNumber(form, name) {
  const value = parseFloat(form.elements[name]?.value ?? "");
  return Number.isFinite(value) ? value : null;
}

export function setupClockLate({ form }) {
  function update() {
    const clockBlock = form.querySelector("#pass-clock");
    const warning = clockBlock?.querySelector("#pass-clock-late");
    if (!warning) return;
    const units = clockBlock.dataset.officialUnits;
    const resultBlock = form.querySelector("#pass-result");
    warning.hidden = !resultNeverAppears({
      officialUnits: units === undefined ? null : Number(units),
      decimals: Number(clockBlock.dataset.decimals ?? 0),
      largada: fieldNumber(form, "largada_s"),
      cutOut: fieldNumber(form, "cut_out_s"),
      showClock: Boolean(form.elements.show_clock?.checked),
      showResult: Boolean(resultBlock?.hasAttribute("data-has-text") && form.elements.show_pass_result?.checked),
    });
  }
  // Typing, marking (the player sends "input"), ticking, and new blocks.
  for (const type of ["input", "change", "pass-blocks-changed"]) form.addEventListener(type, update);
  update();
}
