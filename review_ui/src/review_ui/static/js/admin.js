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
import { errorMessage, localizeTimes, sendJson } from "./common.js";
import { t } from "./text.js";

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

for (const form of document.querySelectorAll("form[data-confirm]")) {
  if (form.dataset.inline) continue; // submitInline asks itself
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
}

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
