// "Pré-visualizar overlay" on the job page (LIVESTACK-48).
//
// Draws the clip's overlay over the proxy, in HTML/CSS with BajaOvrLay's
// tokens.css and Bebas Neue (served scoped to .overlay-preview, see
// review_ui/overlay_preview.py), so the reviewer can check that the clock
// lines up before spending a render. It is a quick check, not a pixel copy
// of the worker's drawing:
//   * the texts come from the server as a render gets them (names after
//     the Settings' choices, the result line, the official time in clock
//     units: #overlay-preview-data); the page's own fields are read live,
//     so unsaved edits show (prova, passagem and its toggle, "Mostrar
//     resultado", "Mostrar cronômetro", the largada and the cuts);
//   * the clock follows the same rule as the clip (js/pass_clock.js),
//     from the video's currentTime, frame by frame (requestAnimationFrame);
//   * outside the cut there is no clip, so nothing is drawn.
// Laid out on a 1920×1080 stage like the clip, scaled to the player.
import { clockText, clockTextAt } from "./pass_clock.js";

const STAGE_WIDTH = 1920;
const STAGE_HEIGHT = 1080;
const SEPARATOR_OR_ARROW = /\s*([·→])\s*/;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
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
  const data = JSON.parse(dataElement.textContent);

  // --- The pills, built once; their texts change in place. ---
  const stage = element("div", "ovl-stage");
  const lowerThird = element("div", "ovl-lower-third");
  const namePill = element("div", "ovl-pill ovl-name-pill");
  if (data.car_number !== null) namePill.append(element("span", "ovl-chip", String(data.car_number)));
  if (data.team_name) namePill.append(element("span", "ovl-team", data.team_name.trim().toUpperCase()));
  const rolePill = element("div", "ovl-pill ovl-role-pill");
  lowerThird.append(namePill, rolePill);
  const resultRow = element("div", "ovl-result-row");
  const resultPill = element("div", "ovl-pill ovl-result-pill");
  for (const part of data.result_text ? resultParts(data.result_text) : []) {
    resultPill.append(element("span", `ovl-${part.kind}`, part.text));
  }
  const clockPill = element("div", "ovl-pill ovl-clock-pill");
  const clockDigits = element("span", "ovl-clock-digits");
  clockPill.append(clockDigits);
  resultRow.append(resultPill, clockPill);
  stage.append(lowerThird, resultRow);
  box.append(stage);

  // The clock pill keeps the official time's width while it runs, like the clip's.
  let clockWidthSet = false;
  function fixClockWidth() {
    if (clockWidthSet || !data.clock) return;
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
    resultPill.hidden = !(data.result_text && checked("show_pass_result", false));
    const largada = fieldNumber("largada_s");
    let clock = null;
    if (data.clock && largada !== null && checked("show_clock", false)) {
      fixClockWidth();
      clock = clockTextAt(now, largada, data.clock.official_units, data.clock.decimals);
    }
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
