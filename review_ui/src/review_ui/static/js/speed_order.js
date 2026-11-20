// "A marca de velocidade está antes da largada" on the job page (LIVESTACK-67).
//
// For "Aceleração e Velocidade" the reviewer marks the largada (where the
// clock starts) and the moment the car crosses the speed trap (where the
// top speed appears under the clock). The trap is down the track, so a
// speed mark before the largada is almost surely a slip. The clock block
// says so from the form's own fields, so it follows every mark and nudge
// before a save. It never blocks anything: the orchestrator stores the
// marks as they are.
import { microseconds } from "./pass_clock.js";

// True when both marks are set and the speed's is before the largada's.
// Compared in whole microseconds, like every mark (js/pass_clock.js), so
// two marks on the same frame are never "before" each other.
export function speedMarkBeforeLargada({ largada, speedMark }) {
  if (largada === null || speedMark === null) return false;
  return microseconds(speedMark) < microseconds(largada);
}

function fieldNumber(form, name) {
  const value = parseFloat(form.elements[name]?.value ?? "");
  return Number.isFinite(value) ? value : null;
}

export function setupSpeedOrder({ form }) {
  function update() {
    // The clock block may come and go (js/pass_blocks.js): look it up each time.
    const warning = form.querySelector("#pass-speed-early");
    if (!warning) return;
    warning.hidden = !speedMarkBeforeLargada({
      largada: fieldNumber(form, "largada_s"),
      speedMark: fieldNumber(form, "speed_mark_s"),
    });
  }
  // Typing, marking (the player sends "input"), and new blocks.
  for (const type of ["input", "change", "pass-blocks-changed"]) form.addEventListener(type, update);
  update();
}
