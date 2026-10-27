// The review form: fields, team lookup and the save/ready/... actions.
//
// Every action sends the job version this page was loaded with
// (expected_version). If someone else changed the job in between, the
// orchestrator answers 409; we then reload the page and tell the reviewer,
// listing the values they had typed so nothing is silently lost.
import { errorMessage, flashAfterReload, sendJson, showFlash } from "./common.js";

// The action Ctrl+Enter triggers: whichever "move it forward" button exists.
const PRIMARY_ACTIONS = ["ready", "reprocess", "retry"];

function numberOrNull(raw, parse) {
  const text = raw.trim();
  if (text === "") return null;
  const value = parse(text);
  return Number.isFinite(value) ? value : null;
}

export function setupReviewForm({ form, jobElement }) {
  const jobId = jobElement.dataset.jobId;
  const teamText = document.getElementById("team-name");
  let version = Number(jobElement.dataset.version);
  let unsavedChanges = false;
  let busy = false;

  // The body of PATCH …/review: only the fields that have a value
  // (an empty field means "leave as is", like in the API).
  function collect(action) {
    const body = { expected_version: version, action };
    const prova = form.elements.prova.value.trim();
    if (prova) body.prova = prova;
    const carNumber = numberOrNull(form.elements.car_number.value, (t) => parseInt(t, 10));
    if (carNumber !== null) body.car_number = carNumber;
    const passagem = numberOrNull(form.elements.passagem.value, (t) => parseInt(t, 10));
    if (passagem !== null) body.passagem = passagem;
    const cutIn = numberOrNull(form.elements.cut_in_s.value, parseFloat);
    if (cutIn !== null) body.cut_in_s = cutIn;
    const cutOut = numberOrNull(form.elements.cut_out_s.value, parseFloat);
    if (cutOut !== null) body.cut_out_s = cutOut;
    body.show_pass = form.elements.show_pass.checked;
    return body;
  }

  function describeValues(body) {
    const parts = [];
    for (const [key, label] of [["prova", "prova"], ["car_number", "car"], ["passagem", "pass"],
      ["cut_in_s", "in"], ["cut_out_s", "out"]]) {
      if (body[key] !== undefined) parts.push(`${label} ${body[key]}`);
    }
    return parts.length ? ` Your values were: ${parts.join(", ")}.` : "";
  }

  function markMissing(fields) {
    for (const element of form.querySelectorAll(".missing")) element.classList.remove("missing");
    for (const name of fields ?? []) form.elements[name]?.classList.add("missing");
  }

  function setBusy(value) {
    busy = value;
    for (const button of form.querySelectorAll("button")) button.disabled = value;
  }

  async function run(action) {
    if (busy || !form.querySelector(`[data-action="${action}"]`)) return;
    if (action === "discard" && !window.confirm("Discard this video? This cannot be undone.")) return;
    // Discard needs no fields; sending none keeps it from failing on a half-typed one.
    const body = action === "discard" ? { expected_version: version, action } : collect(action);
    setBusy(true);
    const result = await sendJson(`/review/api/jobs/${encodeURIComponent(jobId)}/review`, "PATCH", body);
    setBusy(false);

    if (result.ok) {
      version = result.data.job.version;
      jobElement.dataset.version = String(version);
      unsavedChanges = false;
      markMissing([]);
      if (action === "save") {
        showFlash(`${result.data.message} (v${version})`, "success");
      } else {
        // The state changed, so the buttons and status do too: reload.
        flashAfterReload(result.data.message, "success");
        window.location.reload();
      }
      return;
    }
    if (result.data?.reload) {
      // 409: the page is out of date. Reload with the current job and say why.
      const current = result.data.error.details?.current_version;
      const why = result.data.error.code === "version_conflict"
        ? `Someone else changed this job${current ? ` (now v${current})` : ""}; the page was reloaded with their version.`
        : `That action is no longer possible (${result.data.error.message}); the page was reloaded.`;
      flashAfterReload(why + describeValues(body), "warning");
      unsavedChanges = false;
      window.location.reload();
      return;
    }
    if (result.data?.error?.code === "missing_review_fields") {
      markMissing(result.data.error.details?.missing);
    }
    showFlash(errorMessage(result), "error");
  }

  // --- Team name from the car number (GET /teams/{car} via the UI). ---
  let lookupTimer = null;
  async function lookUpTeam() {
    const carNumber = numberOrNull(form.elements.car_number.value, (t) => parseInt(t, 10));
    if (carNumber === null) {
      teamText.textContent = "";
      return;
    }
    teamText.textContent = "Looking up the team…";
    const result = await sendJson(`/review/api/teams/${carNumber}`, "GET");
    // Ignore a late answer for a number the reviewer already changed.
    if (numberOrNull(form.elements.car_number.value, (t) => parseInt(t, 10)) !== carNumber) return;
    teamText.replaceChildren();
    if (result.ok) {
      const school = result.data.school ? ` (${result.data.school})` : "";
      const name = document.createElement("strong");
      name.textContent = result.data.team_name;
      teamText.append("Team: ", name, school);
    } else if (result.status === 404) {
      teamText.textContent = `Car ${carNumber} is not in the Baja app. Check the number.`;
    } else {
      teamText.textContent = `Team lookup unavailable: ${errorMessage(result)}`;
    }
  }
  form.elements.car_number.addEventListener("input", () => {
    clearTimeout(lookupTimer);
    lookupTimer = setTimeout(lookUpTeam, 350);
  });

  for (const button of form.querySelectorAll("[data-action]")) {
    button.addEventListener("click", () => run(button.dataset.action));
  }
  form.addEventListener("input", () => {
    unsavedChanges = true;
  });
  form.addEventListener("submit", (event) => event.preventDefault());
  window.addEventListener("beforeunload", (event) => {
    if (unsavedChanges) event.preventDefault();
  });

  return {
    run,
    runPrimary() {
      const action = PRIMARY_ACTIONS.find((name) => form.querySelector(`[data-action="${name}"]`));
      if (action) run(action);
    },
    version: () => version,
  };
}
