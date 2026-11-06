// The localhost page's only script (http://127.0.0.1:8700, worker/setup_page.py).
//
// LIVESTACK-41 item 14: the page used to reload itself every 5 seconds (an
// HTML "refresh" meta tag), which wiped the token form while a volunteer was
// pasting into it. Now nothing ever reloads the whole page:
//
//   * every few seconds this script asks GET /api/page for the page's parts
//     and replaces the *live* region (#live: state line, messages, numbers,
//     buttons; never a form field) in place;
//   * the *form* region (#forms) is replaced only when the server wants a
//     different form (e.g. the token form became the studio-folder form)
//     AND nobody is using the current one: no field has the focus and
//     nothing was typed into it since it appeared;
//   * buttons POST to the JSON API and then refresh the same way.
//
// Works in a browser and, for the tests, in Node with a tiny fake document
// (tests/worker/test_page_script.py): the functions take the document.

const REFRESH_MILLISECONDS = 5000;

// True while the volunteer is (or was) working in the form region.
function formIsInUse(doc) {
  const forms = doc.getElementById("forms");
  if (!forms) return false;
  const active = doc.activeElement;
  if (active && active !== doc.body && forms.contains(active)) return true;
  return forms.dataset.edited === "yes";
}

// Apply one answer of GET /api/page to the document.
function applyPageUpdate(doc, update) {
  const live = doc.getElementById("live");
  if (live) {
    // Keep an opened "Detalhes" open across the swap.
    const details = live.querySelector("details");
    const detailsWasOpen = Boolean(details && details.open);
    live.innerHTML = update.live_html;
    const newDetails = live.querySelector("details");
    if (newDetails && detailsWasOpen) newDetails.open = true;
  }
  if (update.title) doc.title = update.title;
  const forms = doc.getElementById("forms");
  if (forms && forms.dataset.form !== update.form_key && !formIsInUse(doc)) {
    forms.innerHTML = update.form_html;
    forms.dataset.form = update.form_key;
    forms.dataset.edited = "no";
  }
}

async function refreshPage() {
  try {
    const answer = await fetch("/api/page", { cache: "no-store" });
    if (!answer.ok) return;
    applyPageUpdate(document, await answer.json());
  } catch (error) {
    // The worker is restarting: keep showing what we have and try again later.
  }
}

// Buttons carry data-action (and data-question for a confirmation).
async function actFrom(button) {
  const question = button.dataset.question;
  if (question && !confirm(question)) return;
  await fetch("/api/" + button.dataset.action, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).catch(() => null);
  await refreshPage();
}

// "Copiar" buttons carry the text in data-copy (e.g. the latest.m3u path).
async function copyFrom(button) {
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
    button.textContent = "Copiado!";
  } catch (error) {
    button.textContent = "Selecione e copie à mão";
  }
}

// Typing anywhere in the form region marks it as in use.
function markFormEdited(event) {
  const forms = document.getElementById("forms");
  if (forms && event.target && forms.contains(event.target)) forms.dataset.edited = "yes";
}

if (typeof document !== "undefined" && typeof window !== "undefined") {
  document.addEventListener("input", markFormEdited);
  setInterval(refreshPage, REFRESH_MILLISECONDS);
}

if (typeof module !== "undefined") {
  module.exports = { applyPageUpdate, formIsInUse, REFRESH_MILLISECONDS };
}
