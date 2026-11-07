// The review form: fields, team lookup and the save/ready/... actions.
//
// Every action sends the job version this page was loaded with
// (expected_version). If someone else changed the job in between, the
// orchestrator answers 409; we then reload the page and tell the reviewer,
// listing the values they had typed so nothing is silently lost.
import { errorMessage, flashAfterReload, sendJson, showFlash } from "./common.js";
import { t } from "./text.js";

// The action Ctrl+Enter triggers: whichever "move it forward" button exists.
const PRIMARY_ACTIONS = ["ready", "reprocess", "retry"];
// Actions that send the passagem on to a clip: the passagem warnings
// (LIVESTACK-45) are confirmed first.
const RENDERING_ACTIONS = ["ready", "reprocess"];

function numberOrNull(raw, parse) {
  const text = raw.trim();
  if (text === "") return null;
  const value = parse(text);
  return Number.isFinite(value) ? value : null;
}

// `confirmForward()` is asked before "Pronto" and "Salvar e reprocessar";
// false stops the action (js/passagem_help.js: the reviewer saw a warning).
export function setupReviewForm({ form, jobElement, confirmForward = () => true }) {
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

  // "Os valores que você tinha digitado: prova Enduro, carro 42." (or nothing).
  function describeValues(body) {
    const parts = [];
    for (const key of ["prova", "car_number", "passagem", "cut_in_s", "cut_out_s"]) {
      if (body[key] !== undefined) parts.push(t(`script.value.${key}`, { value: body[key] }));
    }
    return parts.length ? ` ${t("script.your_values", { values: parts.join(", ") })}` : "";
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
    if (action === "discard" && !window.confirm(t("script.confirm_discard"))) return;
    if (RENDERING_ACTIONS.includes(action) && !confirmForward()) return;
    // Discard and restore need no fields; sending none keeps them from failing on a half-typed one.
    const withoutFields = action === "discard" || action === "restore";
    const body = withoutFields ? { expected_version: version, action } : collect(action);
    setBusy(true);
    const result = await sendJson(`/review/api/jobs/${encodeURIComponent(jobId)}/review`, "PATCH", body);
    setBusy(false);

    if (result.ok) {
      version = result.data.job.version;
      jobElement.dataset.version = String(version);
      unsavedChanges = false;
      markMissing([]);
      if (action === "save" && !result.data.refresh_page) {
        showFlash(t("script.saved_version", { message: result.data.message, version }), "success");
      } else if (action === "save") {
        // A clip exists: reload so the warning about the clip on air shows now.
        flashAfterReload(t("script.saved_version", { message: result.data.message, version }), "warning");
        window.location.reload();
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
      let why = t("script.no_longer_possible");
      if (result.data.error.code === "version_conflict") {
        why = current
          ? t("script.version_conflict_now", { version: current })
          : t("script.version_conflict");
      }
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
    teamText.textContent = t("script.team_looking_up");
    const result = await sendJson(`/review/api/teams/${carNumber}`, "GET");
    // Ignore a late answer for a number the reviewer already changed.
    if (numberOrNull(form.elements.car_number.value, (t) => parseInt(t, 10)) !== carNumber) return;
    teamText.replaceChildren();
    if (result.ok) {
      // The names a clip shows, by the Settings page's choices (full or short).
      const schoolText = result.data.clip_school_name ?? result.data.school;
      const school = schoolText ? ` (${schoolText})` : "";
      const name = document.createElement("strong");
      name.textContent = result.data.clip_team_name ?? result.data.team_name;
      teamText.append(t("script.team"), name, school);
    } else if (result.status === 404) {
      teamText.textContent = t("script.team_not_found", { car: carNumber });
    } else {
      teamText.textContent = t("script.team_lookup_failed", { error: errorMessage(result) });
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
