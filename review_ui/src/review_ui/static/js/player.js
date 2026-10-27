// The proxy player and the cut marks.
//
// Times are seconds from the start of the video. The proxy is rendered at
// a constant frame rate, so a time in the proxy is the same time in the raw
// (docs/api.md §1) and the marks can be sent to the orchestrator as-is.

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

function formatTimecode(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = (seconds - minutes * 60).toFixed(3).padStart(6, "0");
  return `${minutes}:${rest}`;
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export function setupPlayer({ video, form, jobElement }) {
  const inputs = { in: form.elements.cut_in_s, out: form.elements.cut_out_s };
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
    const value = parseFloat(inputs[name].value);
    return Number.isFinite(value) ? value : null;
  }

  function setActive(name) {
    activeMark = name;
    for (const box of form.querySelectorAll("[data-mark-box]")) {
      box.classList.toggle("active", box.dataset.markBox === name);
    }
  }

  function writeMark(name, seconds) {
    const limit = duration() || Number.POSITIVE_INFINITY;
    inputs[name].value = clamp(seconds, 0, limit).toFixed(3);
    setActive(name);
    // Lets the form know it has unsaved changes.
    inputs[name].dispatchEvent(new Event("input", { bubbles: true }));
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

  // Returns true when the key was a player shortcut (caller prevents default).
  function handleKey(event) {
    const key = event.key;
    if (key === " " || key === "k") togglePlay();
    else if (key === "i") writeMark("in", video ? video.currentTime : 0);
    else if (key === "o") writeMark("out", video ? video.currentTime : 0);
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

  for (const button of form.querySelectorAll("[data-set-mark]")) {
    const name = button.dataset.setMark;
    button.addEventListener("click", () => writeMark(name, video ? video.currentTime : 0));
  }
  for (const name of ["in", "out"]) {
    inputs[name].addEventListener("focus", () => setActive(name));
    inputs[name].addEventListener("input", render);
  }
  cutBar.addEventListener("click", (event) => {
    const box = cutBar.getBoundingClientRect();
    seek(((event.clientX - box.left) / box.width) * duration());
  });
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
  return { handleKey };
}
