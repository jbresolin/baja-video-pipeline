// The proxy player, the cut marks and the largada mark.
//
// Times are seconds from the start of the video. The proxy is rendered at
// a constant frame rate, so a time in the proxy is the same time in the raw
// (docs/api.md §1) and the marks can be sent to the orchestrator as-is.
//
// The largada (LIVESTACK-48: where the overlay's clock starts at 0) is a
// mark like the cuts: "Marcar" or the l key at the current frame, -/=
// nudge it by one frame once it is the active mark, a line on the cut bar.
// It is only on the page for a prova with a clock (_pass_clock.html), and
// that block may come and go while the reviewer types (js/pass_blocks.js,
// LIVESTACK-53), so its field and buttons are looked up when used.
//
// The page has its own control bar under the picture (play, frame step,
// time, the cut bar as scrubber), outside the stage that "Girar" turns, so
// it works at every rotation (LIVESTACK-51 item 4).
import { formatTimecode } from "./format.js";
import { t } from "./text.js";

// Used when the page carries no frame rate; phone footage is ~30 fps.
// The server already falls back to 30 (job_view.frame_rate_for_stepping),
// this is only a guard against a missing or broken attribute.
const DEFAULT_FRAME_RATE = 30;

// Length of one frame in seconds, from the proxy's fps that the server put
// in data-frame-rate (Job.frame_rate, e.g. 29.97).
function frameSecondsOf(jobElement) {
  const rate = Number(jobElement.dataset.frameRate);
  return 1 / (rate > 0 ? rate : DEFAULT_FRAME_RATE);
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

// The largada as written into its field: rounded *down* to the millisecond,
// so the frame the reviewer marked is at or after it and shows the clock at
// 0 (a mark rounded up could land just after that frame's start).
export function largadaFieldValue(seconds) {
  return (Math.floor(seconds * 1000 + 1e-6) / 1000).toFixed(3);
}

// The form field of each mark; the largada's only while its block is on the page.
const MARK_FIELDS = { in: "cut_in_s", out: "cut_out_s", largada: "largada_s" };

export function setupPlayer({ video, form, jobElement }) {
  // The field of a mark, or undefined when it is not in the form now.
  const markInput = (name) => form.elements[MARK_FIELDS[name]];
  const largadaMark = document.getElementById("largada-mark");
  const timecode = document.getElementById("current-time");
  const cutBar = document.getElementById("cut-bar");
  const cutRange = document.getElementById("cut-range");
  const playhead = document.getElementById("playhead");
  const lengthText = document.getElementById("cut-length");
  // ←/→ step and -/= nudge by exactly this much.
  const frameSeconds = frameSecondsOf(jobElement);
  // The mark that "-" and "=" nudge: the one set or focused most recently.
  let activeMark = "in";
  // When playing the cut, where to stop.
  let stopAt = null;

  function duration() {
    if (video && Number.isFinite(video.duration)) return video.duration;
    return Number(jobElement.dataset.duration) || 0;
  }

  function readMark(name) {
    const input = markInput(name);
    if (!input) return null;
    const value = parseFloat(input.value);
    return Number.isFinite(value) ? value : null;
  }

  function setActive(name) {
    activeMark = name;
    for (const box of form.querySelectorAll("[data-mark-box]")) {
      box.classList.toggle("active", box.dataset.markBox === name);
    }
  }

  function writeMark(name, seconds) {
    const input = markInput(name);
    if (!input) return;
    const limit = duration() || Number.POSITIVE_INFINITY;
    const value = clamp(seconds, 0, limit);
    input.value = name === "largada" ? largadaFieldValue(value) : value.toFixed(3);
    setActive(name);
    // Lets the form know it has unsaved changes.
    input.dispatchEvent(new Event("input", { bubbles: true }));
    render();
  }

  function render() {
    const total = duration();
    const now = video ? video.currentTime : 0;
    timecode.textContent = formatTimecode(now);
    const cutIn = readMark("in");
    const cutOut = readMark("out");
    if (total > 0) {
      playhead.style.left = `${(100 * now) / total}%`;
      const start = cutIn ?? 0;
      const end = cutOut ?? total;
      cutRange.style.left = `${(100 * start) / total}%`;
      cutRange.style.width = `${Math.max(0, (100 * (end - start)) / total)}%`;
      cutRange.hidden = cutIn === null && cutOut === null;
      const largada = readMark("largada");
      largadaMark.hidden = largada === null;
      if (largada !== null) largadaMark.style.left = `${(100 * largada) / total}%`;
    }
    const bothSet = cutIn !== null && cutOut !== null;
    lengthText.textContent = bothSet ? formatTimecode(cutOut - cutIn) : "—";
    lengthText.classList.toggle("invalid", bothSet && cutOut <= cutIn);
  }

  function seek(seconds) {
    if (!video) return;
    video.currentTime = clamp(seconds, 0, duration());
  }

  function seekBy(seconds) {
    if (video) seek(video.currentTime + seconds);
  }

  function stepFrames(frames) {
    if (!video) return;
    video.pause();
    seek(video.currentTime + frames * frameSeconds);
  }

  function nudgeActiveMark(frames) {
    if (!markInput(activeMark)) return;
    const current = readMark(activeMark);
    if (current === null) return;
    writeMark(activeMark, current + frames * frameSeconds);
    seek(readMark(activeMark));
  }

  function togglePlay() {
    if (!video) return;
    stopAt = null;
    if (video.paused) video.play();
    else video.pause();
  }

  function playCut() {
    const cutIn = readMark("in");
    if (!video || cutIn === null) return;
    seek(cutIn);
    stopAt = readMark("out");
    video.play();
  }

  // Play, frame step and the total time under the picture, and dragging
  // along the cut bar to scrub (a click alone still seeks, below).
  function setupControlBar() {
    if (!video) return;
    const playButton = document.getElementById("play-button");
    const totalTime = document.getElementById("total-time");
    const showPlayState = () => {
      const label = t(video.paused ? "script.player.play" : "script.player.pause");
      playButton.textContent = video.paused ? "▶" : "⏸";
      playButton.title = label;
      playButton.setAttribute("aria-label", label);
    };
    playButton.addEventListener("click", togglePlay);
    for (const name of ["play", "pause", "ended"]) video.addEventListener(name, showPlayState);
    showPlayState();
    document
      .querySelector('[data-player="frame-back"]')
      .addEventListener("click", () => stepFrames(-1));
    document
      .querySelector('[data-player="frame-forward"]')
      .addEventListener("click", () => stepFrames(1));
    video.addEventListener("loadedmetadata", () => {
      totalTime.textContent = formatTimecode(duration());
    });
    const seekToPointer = (event) => {
      const box = cutBar.getBoundingClientRect();
      seek(clamp((event.clientX - box.left) / box.width, 0, 1) * duration());
    };
    cutBar.addEventListener("pointerdown", (event) => {
      cutBar.setPointerCapture(event.pointerId);
      seekToPointer(event);
    });
    cutBar.addEventListener("pointermove", (event) => {
      if (cutBar.hasPointerCapture(event.pointerId)) seekToPointer(event);
    });
  }

  // Returns true when the key was a player shortcut (caller prevents default).
  function handleKey(event) {
    const key = event.key;
    if (key === " " || key === "k") togglePlay();
    else if (key === "i") writeMark("in", video ? video.currentTime : 0);
    else if (key === "o") writeMark("out", video ? video.currentTime : 0);
    else if (key === "l" && markInput("largada")) writeMark("largada", video ? video.currentTime : 0);
    else if (key === "I") seek(readMark("in") ?? 0);
    else if (key === "O") seek(readMark("out") ?? duration());
    else if (key === "ArrowLeft") event.shiftKey ? seekBy(-1) : stepFrames(-1);
    else if (key === "ArrowRight") event.shiftKey ? seekBy(1) : stepFrames(1);
    else if (key === "-" || key === "_") nudgeActiveMark(-1);
    else if (key === "=" || key === "+") nudgeActiveMark(1);
    else if (key === "p") playCut();
    else return false;
    return true;
  }

  // Listened to on the form, so the largada block's field and buttons work
  // whenever the block is on the page, however late it came.
  form.addEventListener("click", (event) => {
    const setMark = event.target.closest("[data-set-mark]");
    if (setMark) writeMark(setMark.dataset.setMark, video ? video.currentTime : 0);
    // "Limpar": no largada, so no clock in the clip.
    if (event.target.closest("[data-clear-largada]") && markInput("largada")) {
      markInput("largada").value = "";
      markInput("largada").dispatchEvent(new Event("input", { bubbles: true }));
      if (activeMark === "largada") setActive("in");
    }
  });
  form.addEventListener("focusin", (event) => {
    const name = Object.keys(MARK_FIELDS).find((mark) => MARK_FIELDS[mark] === event.target.name);
    if (name) setActive(name);
  });
  form.addEventListener("input", (event) => {
    if (Object.values(MARK_FIELDS).includes(event.target.name)) render();
  });
  // The largada block came or went (js/pass_blocks.js): its line on the
  // cut bar follows, and a mark that is gone can no longer be nudged.
  form.addEventListener("pass-blocks-changed", () => {
    if (!markInput(activeMark)) setActive("in");
    render();
  });
  cutBar.addEventListener("click", (event) => {
    const box = cutBar.getBoundingClientRect();
    seek(((event.clientX - box.left) / box.width) * duration());
  });
  setupControlBar();
  if (video) {
    for (const name of ["timeupdate", "seeked", "loadedmetadata"]) video.addEventListener(name, render);
    video.addEventListener("timeupdate", () => {
      if (stopAt !== null && video.currentTime >= stopAt) {
        video.pause();
        stopAt = null;
      }
    });
  }
  setActive("in");
  render();
  return { handleKey, readMark };
}
