// Auto-refresh: fetch an HTML fragment every few seconds and hand it to the
// page. Pauses while the tab is hidden (an idle laptop does not keep the
// VPS busy) and refreshes at once when the tab comes back.

const liveStatus = () => document.getElementById("live-status");

function setLive(text, level) {
  const element = liveStatus();
  if (!element) return;
  element.textContent = text;
  element.className = `live ${level}`;
  element.hidden = false;
}

// Turn an HTML string into nodes without putting it in the page yet.
export function parseFragment(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template.content;
}

// container: the element whose data-poll-url / data-poll-seconds say what to fetch.
// onFragment(html): called with each fresh fragment.
export function startPolling(container, onFragment) {
  const url = container.dataset.pollUrl;
  const seconds = Number(container.dataset.pollSeconds) || 5;
  let busy = false;

  async function refresh() {
    if (document.hidden || busy) return;
    busy = true;
    try {
      const response = await fetch(url, { credentials: "same-origin", headers: { Accept: "text/html" } });
      if (response.ok) {
        onFragment(await response.text());
        setLive("live", "ok");
      } else {
        // Keep showing the last good data; say so instead of going blank.
        setLive(`refresh failed (HTTP ${response.status})`, "error");
      }
    } catch {
      setLive("offline, retrying", "error");
    } finally {
      busy = false;
    }
  }

  setInterval(refresh, seconds * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  setLive("live", "ok");
  return { refresh };
}
