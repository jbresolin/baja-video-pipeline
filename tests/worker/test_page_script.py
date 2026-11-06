"""The localhost page never reloads under the volunteer (LIVESTACK-41 item 14).

Before: the page carried <meta http-equiv="refresh" content="5">, so it
reloaded every 5 seconds, also while the volunteer was pasting the token,
and the half-filled form was wiped. Now worker/page_assets/page.js fetches
GET /api/page and swaps only the live region; the form region is replaced
only when the server wants another form and nobody is using the current one.

Two halves:
  * Python: no view of the page (token form, studio folder form, broken
    worker.json, status) asks the browser to reload, the script has no
    location.reload, and the live region never contains a form field;
  * Node: page.js's applyPageUpdate run against a tiny fake document,
    to show that what the volunteer typed survives a refresh. Skipped
    when Node.js is not installed (CI installs it).
"""

import json
import shutil
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from shared.models import WorkerRole
from shared.testing.asgi_transport import SyncAsgiTransport
from shared.testing.credentials import PLACEHOLDER_WORKER_TOKEN
from worker.config import WorkerConfig, save_config
from worker.page_assets import page_script
from worker.setup_page import build_setup_app
from worker.status import StatusBoard

PORT = 8700
PAGE_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "worker/src/worker/page_assets/page.js"


def page_client(config_path: Path) -> httpx.Client:
    app = build_setup_app(config_path, StatusBoard(), threading.Event(), PORT)
    return httpx.Client(transport=SyncAsgiTransport(app), base_url=f"http://127.0.0.1:{PORT}")


def enrolled(role=WorkerRole.WORKER, **settings) -> WorkerConfig:
    return WorkerConfig(
        worker_id="w-1", bearer_token=PLACEHOLDER_WORKER_TOKEN, role=role, **settings
    )


# Every view the page can show, as the worker.json that leads to it.
VIEWS = {
    "token form": None,
    "studio folder form": enrolled(WorkerRole.STUDIO),
    "status page": enrolled(),
    "broken worker.json": "{ not json",
}


@pytest.mark.parametrize("view", VIEWS)
def test_no_view_asks_the_browser_to_reload(tmp_path, view):
    config_path = tmp_path / "worker.json"
    content = VIEWS[view]
    if isinstance(content, WorkerConfig):
        save_config(config_path, content)
    elif isinstance(content, str):
        config_path.write_text(content, encoding="utf-8")

    html = page_client(config_path).get("/").text

    # No automatic reload of the whole page of any kind...
    assert "http-equiv" not in html.lower()
    assert "location.reload" not in html
    # ...but the in-place refresher is there.
    assert "refreshPage" in html and "id='live'" in html and "id='forms'" in html


def test_the_script_itself_never_reloads_the_page():
    script = page_script()
    assert "location.reload" not in script
    assert "location.href" not in script
    assert "/api/page" in script


def test_the_live_region_never_holds_a_form_field(tmp_path):
    # Whatever is in #live is replaced every few seconds, so a field there
    # would lose its text. The JSON the script fetches shows the split.
    config_path = tmp_path / "worker.json"
    update = page_client(config_path).get("/api/page").json()

    assert update["form_key"] == "token"
    assert "name='token'" in update["form_html"]
    for tag in ("<input", "<form", "<textarea", "<select"):
        assert tag not in update["live_html"]


def test_the_api_page_is_localhost_only(tmp_path):
    client = page_client(tmp_path / "worker.json")
    assert client.get("/api/page", headers={"Host": "evil.example:8700"}).status_code == 403


# --- page.js in Node -----------------------------------------------------------

# A fake document just big enough for applyPageUpdate: two regions with
# innerHTML and data-* attributes, an "active element" (the focused field),
# and a <details> element whose open state we can watch.
NODE_SCRIPT = r"""
const { applyPageUpdate } = require(process.env.PAGE_SCRIPT);

function region(html, dataset) {
  const element = {
    dataset: { ...dataset },
    _html: "",
    get innerHTML() { return this._html; },
    set innerHTML(value) {
      this._html = value;
      this._details = value.includes("<details") ? { open: false } : null;
    },
    querySelector(selector) { return selector === "details" ? this._details : null; },
    contains(other) { return Boolean(other) && other.parent === this; },
  };
  element.innerHTML = html;
  return element;
}

function page(formKey) {
  const live = region("<h1>old</h1><details>…</details>", {});
  const forms = region("<form>token form</form>", { form: formKey, edited: "no" });
  const tokenField = { parent: forms, value: "" };
  const doc = {
    title: "old",
    body: {},
    activeElement: null,
    getElementById(id) { return id === "live" ? live : id === "forms" ? forms : null; },
  };
  return { doc, live, forms, tokenField };
}

// One answer of GET /api/page.
function update(live_html, form_key, form_html, title = "t") {
  return { title, live_html, form_key, form_html };
}

const results = {};

// 1. The volunteer is pasting the token (the field has the focus) when the
//    refresh arrives with new numbers and the same form.
{
  const { doc, live, forms, tokenField } = page("token");
  doc.activeElement = tokenField;
  tokenField.value = "half-pasted-tok";
  applyPageUpdate(doc, update("<h1>new</h1>", "token", "<form>fresh</form>"));
  results.typing = { live: live.innerHTML, forms: forms.innerHTML, value: tokenField.value };
}

// 2. Same, but the server already wants the next form (enrollment just
//    finished): still not swapped while the field has the focus.
{
  const { doc, forms, tokenField } = page("token");
  doc.activeElement = tokenField;
  applyPageUpdate(doc, update("x", "studio_folder", "<form>studio</form>"));
  results.focusedKeyChange = { forms: forms.innerHTML, key: forms.dataset.form };
}

// 3. The volunteer typed and then clicked elsewhere (no focus, but edited).
{
  const { doc, forms } = page("token");
  forms.dataset.edited = "yes";
  applyPageUpdate(doc, update("x", "studio_folder", "<form>studio</form>"));
  results.editedKeyChange = { forms: forms.innerHTML };
}

// 4. Nobody is using the form: a new form key swaps it in.
{
  const { doc, forms } = page("token");
  applyPageUpdate(doc, update("x", "studio_folder", "<form>studio</form>", "Configuração"));
  results.idleKeyChange = { forms: forms.innerHTML, key: forms.dataset.form, title: doc.title };
}

// 5. "Detalhes" was opened: it stays open after the swap.
{
  const { doc, live } = page("none");
  live.querySelector("details").open = true;
  applyPageUpdate(doc, update("<details>new</details>", "none", ""));
  results.details = { open: live.querySelector("details").open };
}

console.log(JSON.stringify(results));
"""


@pytest.fixture(scope="module")
def node_results() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    finished = subprocess.run(
        [node, "-e", NODE_SCRIPT],
        env={"PAGE_SCRIPT": str(PAGE_SCRIPT_PATH), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_what_the_volunteer_is_typing_survives_a_refresh(node_results):
    typing = node_results["typing"]
    # The live part was refreshed...
    assert typing["live"] == "<h1>new</h1>"
    # ...the form was not touched, so the half-pasted token is still there.
    assert typing["forms"] == "<form>token form</form>"
    assert typing["value"] == "half-pasted-tok"


def test_a_new_form_waits_while_the_old_one_has_the_focus(node_results):
    assert node_results["focusedKeyChange"] == {"forms": "<form>token form</form>", "key": "token"}


def test_a_new_form_waits_while_the_old_one_has_typed_text(node_results):
    assert node_results["editedKeyChange"] == {"forms": "<form>token form</form>"}


def test_an_unused_form_is_swapped_when_the_server_wants_another(node_results):
    assert node_results["idleKeyChange"] == {
        "forms": "<form>studio</form>",
        "key": "studio_folder",
        "title": "Configuração",
    }


def test_an_opened_details_section_stays_open(node_results):
    assert node_results["details"] == {"open": True}
