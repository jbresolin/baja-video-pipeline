// "Pré-visualizar overlay" on the job page (LIVESTACK-48).
//
// Draws the clip's overlay over the proxy, in HTML/CSS with BajaOvrLay's
// tokens.css and Bebas Neue (served scoped to .overlay-preview, see
// review_ui/overlay_preview.py), so the reviewer can check that the clock
// lines up before spending a render. It is a quick check, not a pixel copy
// of the worker's drawing:
//   * the texts come from the server as a render gets them (names after
//     the Settings' choices, the result line, the official time in clock
//     units: #overlay-preview-data), fetched again after a save
//     (GET /review/api/jobs/{id}/overlay-preview); the page's own fields
//     are read live, so unsaved edits show (prova, passagem and its toggle,
//     "Mostrar resultado", "Mostrar cronômetro", the largada and the cuts),
//     and so does a typed car number with the team the form looked up for
//     it ("team-looked-up" from js/review_form.js);
//   * the clock follows the same rule as the clip (js/pass_clock.js),
//     from the video's currentTime, frame by frame (requestAnimationFrame);
//     with a clock, the result pill appears when it stops (LIVESTACK-53);
//   * outside the cut there is no clip, so nothing is drawn.
// Laid out on a 1920×1080 stage like the clip, scaled to the player.
import { clockText, clockTextAt, resultShownAt } from "./pass_clock.js";

const STAGE_WIDTH = 1920;
const STAGE_HEIGHT = 1080;
const SEPARATOR_OR_ARROW = /\s*([·→])\s*/;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// The clock's unit as the clip draws it: in capitals; "" for none. A clock
// from before the unit existed has none on record (null).
export function clockUnitText(unit) {
  return (unit ?? "").trim().toUpperCase();
}

// The result line as the worker splits it: words, a muted "·", the "→".
export function resultParts(line) {
  const parts = [];
  const pieces = line.trim().toUpperCase().split(SEPARATOR_OR_ARROW);
  pieces.forEach((piece, index) => {
    const isSeparator = index % 2 === 1;
    if (!isSeparator) {
      if (piece.trim()) parts.push({ text: piece.trim(), kind: "words" });
      return;
    }
    if (!parts.length || parts[parts.length - 1].kind !== "words") return;
    parts.push({ text: piece, kind: piece === "→" ? "arrow" : "separator" });
  });
  if (parts.length && parts[parts.length - 1].kind !== "words") parts.pop();
  return parts;
}

// The name pill's parts: the car number chip and the team, as the clip shows them.
export function nameParts({ carNumber, teamName }) {
  const parts = [];
  if (Number.isInteger(carNumber)) parts.push({ text: String(carNumber), kind: "chip" });
  if (teamName && teamName.trim()) parts.push({ text: teamName.trim().toUpperCase(), kind: "team" });
  return parts;
}

// What the preview keeps from a "team-looked-up" answer (GET /teams/{car}):
// the names the clip uses, by the Settings' choices, else the plain ones.
export function namesFromTeamAnswer(carNumber, team) {
  if (team === null) return { car_number: carNumber, team_name: null, school_name: null };
  return {
    car_number: carNumber,
    team_name: team.clip_team_name ?? team.team_name ?? null,
    school_name: team.clip_school_name ?? team.school ?? null,
  };
}

// The role pill's parts, as worker/overlay.py's OverlayText.role_parts().
export function roleParts({ prova, passagem, showPass, school }) {
  const parts = [];
  if (prova && prova.trim()) parts.push({ text: prova.trim().toUpperCase(), muted: false });
  if (showPass && Number.isInteger(passagem)) parts.push({ text: `PASSAGEM ${passagem}`, muted: false });
  if (school && school.trim()) parts.push({ text: school.trim().toUpperCase(), muted: true });
  return parts;
}

export function setupOverlayPreview({ video, form, player }) {
  const box = document.getElementById("overlay-preview");
  const toggle = document.getElementById("overlay-preview-toggle");
  const dataElement = document.getElementById("overlay-preview-data");
  if (!box || !toggle || !dataElement || !video) return;
  // Changed by "team-looked-up" (names) and after a save (everything).
  let data = JSON.parse(dataElement.textContent);
  const jobId = form.closest("[data-job-id]")?.dataset.jobId;

  // --- The pills; their contents are redrawn when the data changes. ---
  const stage = element("div", "ovl-stage");
  const lowerThird = element("div", "ovl-lower-third");
  const namePill = element("div", "ovl-pill ovl-name-pill");
  const rolePill = element("div", "ovl-pill ovl-role-pill");
  lowerThird.append(namePill, rolePill);
  const resultRow = element("div", "ovl-result-row");
  const resultPill = element("div", "ovl-pill ovl-result-pill");

  function fillNameAndResult() {
    namePill.replaceChildren();
    for (const part of nameParts({ carNumber: data.car_number, teamName: data.team_name })) {
      namePill.append(element("span", part.kind === "chip" ? "ovl-chip" : "ovl-team", part.text));
    }
    namePill.hidden = namePill.childElementCount === 0;
    resultPill.replaceChildren();
    for (const part of data.result_text ? resultParts(data.result_text) : []) {
      resultPill.append(element("span", `ovl-${part.kind}`, part.text));
    }
  }
  fillNameAndResult();

  // A car number typed in the form: its team, as the form looked it up.
  form.addEventListener("team-looked-up", (event) => {
    const { carNumber, team } = event.detail;
    data = { ...data, ...namesFromTeamAnswer(carNumber, team) };
    fillNameAndResult();
  });

  // Saved without a reload: the server's texts may have changed (a result
  // for the new car or passagem, its official time), so fetch them again.
  form.addEventListener("review-saved", async () => {
    if (!jobId) return;
    const answer = await fetch(`/review/api/jobs/${encodeURIComponent(jobId)}/overlay-preview`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!answer.ok) return;
    data = await answer.json();
    clockWidthSet = false;
    clockDigits.style.width = "";
    fillNameAndResult();
  });

  // Prova, car or passagem changed (js/pass_blocks.js, LIVESTACK-53): the
  // result line and the official time for what the form says now.
  form.addEventListener("pass-blocks-changed", (event) => {
    data = { ...data, result_text: event.detail.result_text, clock: event.detail.clock };
    clockWidthSet = false;
    clockDigits.style.width = "";
    fillNameAndResult();
  });

  // The clock, top right (LIVESTACK-53): digits, then its unit ("66,0 S").
  const clockPill = element("div", "ovl-pill ovl-clock-pill");
  const clockLine = element("span", "ovl-clock-text");
  const clockDigits = element("span", "ovl-clock-digits");
  const clockUnit = element("span", "ovl-clock-unit");
  clockLine.append(clockDigits, clockUnit);
  clockPill.append(clockLine);
  resultRow.append(resultPill);
  stage.append(lowerThird, resultRow, clockPill);
  box.append(stage);

  // The clock pill keeps the official time's width while it runs, like the clip's.
  let clockWidthSet = false;
  function fixClockWidth() {
    if (clockWidthSet || !data.clock) return;
    clockUnit.textContent = clockUnitText(data.clock.unit);
    clockUnit.hidden = clockUnit.textContent === "";
    clockDigits.textContent = clockText(data.clock.official_units, data.clock.decimals);
    const width = clockDigits.offsetWidth;
    if (width) {
      clockDigits.style.width = `${width}px`;
      clockWidthSet = true;
    }
  }

  function fieldNumber(name) {
    const field = form.elements[name];
    if (!field) return null;
    const value = parseFloat(field.value);
    return Number.isFinite(value) ? value : null;
  }

  function checked(name, whenAbsent) {
    const field = form.elements[name];
    return field ? field.checked : whenAbsent;
  }

  function updateRole() {
    const parts = roleParts({
      prova: form.elements.prova.value,
      passagem: fieldNumber("passagem"),
      showPass: form.elements.show_pass.checked,
      school: data.school_name,
    });
    rolePill.replaceChildren();
    parts.forEach((part, index) => {
      if (index) rolePill.append(element("span", "ovl-separator", "·"));
      rolePill.append(element("span", part.muted ? "ovl-muted" : "ovl-words", part.text));
    });
    rolePill.hidden = parts.length === 0;
  }

  // The stage over the 16:9 picture the clip will be, fitted in the player.
  function placeStage() {
    const width = video.clientWidth;
    const height = video.clientHeight;
    const scale = Math.min(width / STAGE_WIDTH, height / STAGE_HEIGHT);
    const left = video.offsetLeft + (width - STAGE_WIDTH * scale) / 2;
    const top = video.offsetTop + (height - STAGE_HEIGHT * scale) / 2;
    stage.style.transform = `translate(${left}px, ${top}px) scale(${scale})`;
  }

  function draw() {
    const now = video.currentTime;
    const cutIn = player.readMark("in") ?? 0;
    const cutOut = player.readMark("out") ?? Number.POSITIVE_INFINITY;
    stage.hidden = now < cutIn || now > cutOut;
    updateRole();
    const largada = fieldNumber("largada_s");
    const clockOn = data.clock && largada !== null && checked("show_clock", false);
    let clock = null;
    if (clockOn) {
      fixClockWidth();
      clock = clockTextAt(now, largada, data.clock.official_units, data.clock.decimals);
    }
    // With a clock, the result only from its freeze on, as in the clip.
    const resultNow =
      !clockOn || resultShownAt(now, largada, data.clock.official_units, data.clock.decimals);
    resultPill.hidden = !(data.result_text && checked("show_pass_result", false) && resultNow);
    clockPill.hidden = clock === null;
    if (clock !== null) clockDigits.textContent = clock;
  }

  let frameRequest = null;
  function loop() {
    draw();
    frameRequest = window.requestAnimationFrame(loop);
  }

  function setOn(on) {
    box.hidden = !on;
    if (frameRequest !== null) window.cancelAnimationFrame(frameRequest);
    frameRequest = null;
    if (on) {
      placeStage();
      loop();
    }
  }

  toggle.addEventListener("change", () => setOn(toggle.checked));
  new ResizeObserver(() => {
    if (!box.hidden) placeStage();
  }).observe(video);
  setOn(toggle.checked);
}
