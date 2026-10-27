// Queue page: auto-refresh the rows and move through them with the keyboard.
import { autoSubmit, isTyping, localizeTimes } from "./common.js";
import { startPolling } from "./poll.js";

const queue = document.getElementById("queue");
// The selected job's id survives refreshes even when rows move around.
let selectedJobId = null;

function rows() {
  return [...queue.querySelectorAll(".job-row")];
}

function highlight(row) {
  for (const other of rows()) other.classList.toggle("selected", other === row);
  if (row) {
    selectedJobId = row.dataset.jobId;
    row.focus({ preventScroll: true });
    row.scrollIntoView({ block: "nearest" });
  }
}

function move(step) {
  const all = rows();
  if (all.length === 0) return;
  const current = all.findIndex((row) => row.dataset.jobId === selectedJobId);
  // Nothing selected yet: the first press selects the first row.
  const next = current === -1 ? 0 : Math.min(all.length - 1, Math.max(0, current + step));
  highlight(all[next]);
}

document.addEventListener("keydown", (event) => {
  if (isTyping(event) || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "ArrowDown" || event.key === "j") {
    move(1);
    event.preventDefault();
  } else if (event.key === "ArrowUp" || event.key === "k") {
    move(-1);
    event.preventDefault();
  } else if (event.key === "Enter" || event.key === "o") {
    const row = rows().find((candidate) => candidate.dataset.jobId === selectedJobId);
    if (row) {
      window.location.href = row.href;
      event.preventDefault();
    }
  }
});

// Clicking or tabbing to a row also selects it.
queue.addEventListener("focusin", (event) => {
  const row = event.target.closest?.(".job-row");
  if (row) highlight(row);
});

startPolling(queue, (html) => {
  // Don't steal focus from the filters while refreshing.
  const hadFocus = queue.contains(document.activeElement);
  queue.innerHTML = html;
  localizeTimes(queue);
  const again = rows().find((row) => row.dataset.jobId === selectedJobId);
  if (again) {
    again.classList.add("selected");
    if (hadFocus) again.focus({ preventScroll: true });
  }
});

autoSubmit(document.getElementById("queue-filters"));
