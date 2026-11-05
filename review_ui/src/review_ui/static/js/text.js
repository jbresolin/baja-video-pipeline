// What the page scripts say, from the pt-BR catalog (review_ui/catalog/).
//
// The server embeds the catalog's "script.*" entries in every page as
// <script type="application/json" id="ui-text"> (base.html), so the
// scripts never carry a sentence of their own. Same {name} placeholders
// as the Python side.

function readCatalog() {
  try {
    return JSON.parse(document.getElementById("ui-text")?.textContent ?? "{}");
  } catch {
    return {};
  }
}

const catalog = readCatalog();

// t("script.copy") → "Copiar"; t("script.error.http", { status: 502 }) fills {status}.
// A missing key shows the key itself, so a typo is visible instead of blank.
export function t(key, values = {}) {
  const entry = catalog[key] ?? key;
  return entry.replace(/\{(\w+)\}/g, (whole, name) => (name in values ? String(values[name]) : whole));
}
