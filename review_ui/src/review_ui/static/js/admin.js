// Admin pages. They are plain HTML forms and work without this script;
// it only adds conveniences:
//   [data-copy-from=id]   button copies the text of element #id (the new token)
//   form[data-confirm]    asks before submitting (revoke, remove)
//   select[data-autosubmit] submits its form when changed (contacts filter)
//   form[data-inline=url]  sends the form as JSON to that URL instead of
//                          posting it, and swaps in the rows the answer
//                          carries (data-inline-target: "#id" or the closest
//                          "tr"), so the page does not reload or jump to the
//                          top; a short note beside the button says it worked
//                          (routes_admin_inline.py; LIVESTACK-42)
//   textarea[data-welcome-preview=id]  refreshes the preview #id while
//                          typing (Settings, welcome message; LIVESTACK-44)
//   select[data-welcome-status]  on the add-contact form: "Bloqueado"
//                          unchecks and disables [data-welcome-checkbox],
//                          "Permitido" checks it again
import { errorMessage, localizeTimes, sendJson } from "./common.js";
import { t } from "./text.js";
import { fillWelcomeMessage } from "./welcome.js";

// How long the "done" note stays beside the button.
const INLINE_NOTE_SECONDS = 4;

for (const button of document.querySelectorAll("[data-copy-from]")) {
  button.addEventListener("click", async () => {
    const text = document.getElementById(button.dataset.copyFrom)?.textContent ?? "";
    try {
      await navigator.clipboard.writeText(text.trim());
      button.textContent = t("script.copied");
    } catch {
      // Clipboard blocked (e.g. not HTTPS): select the text so Ctrl+C works.
      const range = document.createRange();
      range.selectNodeContents(document.getElementById(button.dataset.copyFrom));
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
      button.textContent = t("script.selected_press_ctrl_c");
    }
  });
}

// Also run on rows swapped in by a data-inline form (a contact row's
// "Enviar boas-vindas" asks before sending).
function enhanceConfirmForms(root) {
  for (const form of root.querySelectorAll("form[data-confirm]")) {
    if (form.dataset.inline) continue; // submitInline asks itself
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  }
}

enhanceConfirmForms(document);

// "Prova padrão" (LIVESTACK-51): saving a contact row whose sender has
// videos waiting for review without a prova asks whether to fill them too.
// Delegated, so rows swapped in by the Permitir/Bloquear buttons work too.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.matches?.("form[data-waiting-without-prova]")) return;
  const flag = form.elements.apply_default_prova_to_waiting;
  flag.value = "";
  const count = Number(form.dataset.waitingWithoutProva);
  const select = form.elements.default_prova_id;
  if (!count || !select?.value) return;
  const prova = select.selectedOptions[0].textContent.trim();
  const key = count === 1 ? "script.contacts.apply_default.one" : "script.contacts.apply_default.other";
  if (window.confirm(t(key, { prova, count }))) flag.value = "1";
});

// The element a data-inline form's answer replaces.
function inlineTarget(form) {
  const selector = form.dataset.inlineTarget;
  return selector.startsWith("#") ? document.querySelector(selector) : form.closest(selector);
}

// A short note right after the form: what was done, or why it was refused.
function noteBeside(form, message, isError) {
  const note = document.createElement("span");
  note.className = isError ? "inline-note error-text" : "inline-note";
  note.setAttribute("role", isError ? "alert" : "status");
  note.textContent = message;
  form.after(note);
  if (!isError) setTimeout(() => note.remove(), INLINE_NOTE_SECONDS * 1000);
}

async function submitInline(form) {
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
  const body = Object.fromEntries(new FormData(form).entries());
  const button = form.querySelector("button[type=submit]");
  if (button) button.disabled = true;
  const result = await sendJson(form.dataset.inline, "POST", body);
  if (!result.ok) {
    if (button) button.disabled = false;
    noteBeside(form, errorMessage(result), true);
    return;
  }
  // Put the new rows in place of the old ones (a <template> parses table
  // rows correctly, unlike a <div>), then wire up their forms.
  const template = document.createElement("template");
  template.innerHTML = result.data.html.trim();
  const fresh = template.content.firstElementChild;
  inlineTarget(form).replaceWith(fresh);
  enhanceInlineForms(fresh);
  enhanceConfirmForms(fresh);
  localizeTimes(fresh);
  // The same action's form in the new rows (same URL and, for ↑/↓, the
  // same direction), so the note appears beside the button that was pressed.
  const candidates = [...fresh.querySelectorAll("form[data-inline]")].filter(
    (candidate) => candidate.dataset.inline === form.dataset.inline,
  );
  const again =
    candidates.find((candidate) => candidate.elements.direction?.value === body.direction) ??
    candidates[0];
  noteBeside(again ?? fresh.querySelector("form") ?? fresh, result.data.message, false);
}

function enhanceInlineForms(root) {
  const forms = root.matches?.("form[data-inline]") ? [root] : root.querySelectorAll("form[data-inline]");
  for (const form of forms) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitInline(form);
    });
  }
}

enhanceInlineForms(document);

for (const select of document.querySelectorAll("select[data-autosubmit]")) {
  select.addEventListener("change", () => select.form.submit());
}

// Settings: the welcome message's preview, filled with the example values
// the server put on the preview box.
for (const textarea of document.querySelectorAll("textarea[data-welcome-preview]")) {
  const preview = document.getElementById(textarea.dataset.welcomePreview);
  const examples = JSON.parse(preview.dataset.exampleValues);
  textarea.addEventListener("input", () => {
    const filled = fillWelcomeMessage(textarea.value.trim(), examples);
    preview.textContent = filled || t("script.welcome_preview_empty");
  });
}

// Contacts, add form: no welcome message for a blocked contact. The
// checkbox exists with data-welcome-checkbox only when a message is set.
for (const select of document.querySelectorAll("select[data-welcome-status]")) {
  const checkbox = select.form.querySelector("[data-welcome-checkbox]");
  if (!checkbox) continue;
  select.addEventListener("change", () => {
    checkbox.disabled = select.value === "blocked";
    checkbox.checked = select.value === "allowed";
  });
}
