// Helpers shared by the page scripts. Plain ES modules, no build step.
import { clockText, relativeTimeText } from "./format.js";
import { t } from "./text.js";

// True while the reviewer is typing in a field, so single-key shortcuts
// (i, o, j, k, ...) do not fire in the middle of a word.
export function isTyping(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

// Refresh every <time data-local>. The server already wrote it the same way
// (São Paulo time, macros.html); this keeps "relative" ones ("há 5 min (09:18)")
// current between refreshes, whatever time zone the laptop is set to.
export function localizeTimes(root = document) {
  const now = new Date();
  for (const element of root.querySelectorAll("time[data-local]")) {
    const date = new Date(element.getAttribute("datetime"));
    if (Number.isNaN(date.getTime())) continue;
    element.title = clockText(date);
    element.textContent =
      element.dataset.local === "relative" ? relativeTimeText(date, now) : clockText(date);
  }
}

// Call one of the UI's JSON endpoints. Never throws: network failures come
// back as { ok: false, status: 0 } so callers have one path for errors.
export async function sendJson(url, method, body) {
  try {
    const response = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
    });
    let data = null;
    try {
      data = await response.json();
    } catch {
      // Not JSON (e.g. a proxy error page); the status code still tells us enough.
    }
    return { ok: response.ok, status: response.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

// What to tell the reviewer when a call failed. The UI's JSON errors carry
// the sentence to show in "text" (errors.py), already in pt-BR.
export function errorMessage(result) {
  if (result.status === 0) return t("script.error.no_connection");
  return result.data?.text ?? t("script.error.http", { status: result.status });
}

// --- Flash messages: one line at the top of the page, optionally kept
// across a reload (sessionStorage) so "reload and tell the user" works. ---

const FLASH_STORAGE_KEY = "bvp-review-flash";

export function showFlash(text, level = "info") {
  const element = document.getElementById("flash");
  if (!element) return;
  element.textContent = text;
  element.className = `flash ${level}`;
  element.hidden = false;
}

export function flashAfterReload(text, level = "info") {
  try {
    sessionStorage.setItem(FLASH_STORAGE_KEY, JSON.stringify({ text, level }));
  } catch {
    // Private mode without storage: the reload still happens, just silently.
  }
}

export function showStoredFlash() {
  try {
    const stored = sessionStorage.getItem(FLASH_STORAGE_KEY);
    if (!stored) return;
    sessionStorage.removeItem(FLASH_STORAGE_KEY);
    const { text, level } = JSON.parse(stored);
    showFlash(text, level);
  } catch {
    // Ignore a broken stored value.
  }
}

// Copy text to the clipboard. navigator.clipboard needs HTTPS; plain-HTTP
// local development falls back to the old select-and-copy trick.
export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

// Submit a filter form as soon as one of its fields changes.
export function autoSubmit(form) {
  if (form) form.addEventListener("change", () => form.requestSubmit());
}
