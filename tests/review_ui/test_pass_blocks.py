"""The result and clock blocks follow the form, without a save (LIVESTACK-53).

The story: on a new video the reviewer chooses the prova and types car
and passagem. "Resultado da passagem" and "Cronômetro" used to appear
only after a save and a reload, so the largada could not be marked
before saving. Now the page asks GET /review/api/jobs/{id}/pass-blocks
with what the form says, and js/pass_blocks.js puts the blocks in, swaps
them or takes them out, keeping what the reviewer already set.

These tests check, from the outside in:
* the page always has the two slots the script fills;
* the UI's endpoint passes the form's values on to the orchestrator and
  answers the blocks' HTML (or null) and the overlay preview's texts;
* against the REAL orchestrator: a new video gets its clock block for
  values that were never saved;
* in Node: the script's decisions (what to ask, which values to keep,
  when a block comes and goes), on stand-ins for the page's elements.
"""

import json
import shutil
import subprocess

import pytest

from orchestrator.domain.pass_results_fetch import poll_due_tables
from orchestrator.testing.scenarios import hold_raw, make_proxy, receive_job
from review_ui.templating import STATIC_DIR
from shared.models import PassPreview, WorkerRole
from tests.orchestrator.pass_results_data import job_in_review, set_up_manobrabilidade
from tests.review_ui.sample_data import failing_fetch, pass_clock, pass_result

BLOCKS_URL = "/review/api/jobs/job-awaiting-1/pass-blocks"


def manobrabilidade_preview(**clock_changes) -> PassPreview:
    """What the orchestrator answers for car 26's second Manobrabilidade run,
    before any largada is marked: the result line and a clock stopping at 66,0."""
    clock = pass_clock(largada_s=None, render=None, **clock_changes)
    return PassPreview(pass_result=pass_result(), pass_clock=clock)


# --- The page and the UI's endpoint (fake orchestrator) ---


def test_the_page_has_both_slots_even_without_blocks(ui):
    # The sample job has no prova: neither block is drawn, but the slots
    # the script fills are there, with the endpoint's address.
    page = ui.get("/review/jobs/job-awaiting-1").text

    assert 'id="pass-result-slot" data-url="/review/api/jobs/job-awaiting-1/pass-blocks"' in page
    assert 'id="pass-clock-slot"' in page
    assert 'name="largada_s"' not in page


def test_the_endpoint_passes_the_form_values_on(ui, fake_orchestrator):
    ui.get(BLOCKS_URL, params={"prova": "Manobrabilidade", "car_number": 26, "passagem": 2})

    # The last request the orchestrator got is the preview, with the values.
    asked = fake_orchestrator.requests[-1]
    assert asked.path == "/jobs/job-awaiting-1/pass-preview"
    assert sorted(asked.query) == [
        ("car_number", "26"),
        ("passagem", "2"),
        ("prova", "Manobrabilidade"),
    ]


def test_an_empty_prova_is_left_out_so_the_saved_one_counts(ui, fake_orchestrator):
    # The select's "choose a prova" is an empty value; a save leaves the
    # prova as it is then, so the preview must ask for the saved one too.
    ui.get(BLOCKS_URL, params={"prova": "", "passagem": 2})

    assert dict(fake_orchestrator.requests[-1].query) == {"passagem": "2"}


def test_a_prova_with_a_clock_answers_both_blocks_and_the_preview_texts(ui, fake_orchestrator):
    fake_orchestrator.pass_previews["Manobrabilidade"] = manobrabilidade_preview()

    answer = ui.get(BLOCKS_URL, params={"prova": "Manobrabilidade"}).json()

    # The blocks as the page draws them: the result box and the clock with
    # an empty largada field, waiting for the mark.
    assert 'id="pass-result"' in answer["result_html"]
    assert 'name="show_pass_result" checked' in answer["result_html"]
    assert 'name="largada_s"' in answer["clock_html"]
    assert 'value=""' in answer["clock_html"]
    assert "Marque a largada" in answer["clock_html"]
    # And what the overlay preview draws with.
    assert answer["preview"] == {
        "result_text": "66 s · 4 cones → 86 s",
        "clock": {"official_units": 660, "decimals": 1, "unit": "s"},
    }


def test_a_prova_without_a_clock_answers_no_blocks(ui):
    # Nothing configured for "Enduro" in the fake: both blocks go away.
    answer = ui.get(BLOCKS_URL, params={"prova": "Enduro"}).json()

    assert answer["result_html"] is None
    assert answer["clock_html"] is None
    assert answer["preview"] == {"result_text": None, "clock": None}


def test_a_clock_without_a_result_line_says_the_fetch_status_itself(ui, fake_orchestrator):
    # A prova with only a clock block: the result block is absent, so the
    # clock block carries the "inacessível desde …" line (as on the page).
    fake_orchestrator.pass_previews["Aceleração"] = PassPreview.model_validate(
        {
            "pass_result": {},
            "pass_clock": pass_clock(fetch=failing_fetch()).model_dump(),
        }
    )

    answer = ui.get(BLOCKS_URL, params={"prova": "Aceleração"}).json()

    assert answer["result_html"] is None
    assert 'id="pass-clock-fetch"' in answer["clock_html"]


def test_an_unknown_job_is_an_error_not_a_page(ui):
    response = ui.get("/review/api/jobs/no-such-job/pass-blocks", params={"passagem": 1})
    assert response.status_code == 404


# --- Against the real orchestrator ---


def test_a_new_video_gets_its_clock_block_before_any_save(reviewer, harness):
    # Setup: Manobrabilidade configured with a clock, its table fetched
    # (another of its videos is in review, as on an event day), and a new
    # video with nothing saved.
    set_up_manobrabilidade(harness)
    job_in_review(harness, car=2, passagem=1)
    poll_due_tables(harness.context)
    worker = harness.enroll(WorkerRole.MASTER, "new-master")
    job_id = receive_job(harness)
    hold_raw(harness, worker, job_id)
    make_proxy(harness, worker, job_id)
    assert 'name="largada_s"' not in reviewer.get(f"/review/jobs/{job_id}").text

    # What the script asks while the reviewer types.
    answer = reviewer.get(
        f"/review/api/jobs/{job_id}/pass-blocks",
        params={"prova": "Manobrabilidade", "car_number": 26, "passagem": 2},
    ).json()

    # The clock block is there, ready for the largada, and says where the
    # clock will stop; the result line is the real one for that run.
    assert 'name="largada_s"' in answer["clock_html"]
    assert "66,0" in answer["clock_html"]
    assert answer["preview"]["result_text"] == "66 s · 4 cones → 86 s"
    # Nothing was saved.
    assert harness.job(job_id)["prova"] is None


# --- The script's decisions, in Node ---

NODE_SCRIPT = """
const { formQuery, carryOverFields, slotKeeper } = await import(process.env.MODULE);

// Stand-ins for the page: a block is a list of inputs; a slot holds at most one block.
function makeBlock(html) {
  const spec = JSON.parse(html);
  return { parentNode: null, inputs: spec.map((input) => ({ ...input })),
           querySelectorAll() { return this.inputs; } };
}
function makeSlot(block) {
  const slot = { children: [],
    get firstElementChild() { return this.children[0] ?? null; },
    replaceChildren(...nodes) {
      for (const node of this.children) node.parentNode = null;
      this.children = nodes;
      for (const node of nodes) node.parentNode = this;
    } };
  if (block) slot.replaceChildren(block);
  return slot;
}
const value = (slot, name) => slot.firstElementChild?.inputs.find((i) => i.name === name);
const clockHtml = (largada, showClock) => JSON.stringify([
  { name: "largada_s", type: "number", value: largada },
  { name: "show_clock", type: "checkbox", checked: showClock },
]);

const out = {};
// What is asked for the form's values.
out.query_full = String(formQuery({ prova: " Manobrabilidade ", carNumber: "26", passagem: "2" }));
out.query_partial = String(formQuery({ prova: "", carNumber: "2a", passagem: "0" }));

// A page loaded with the clock block; the reviewer marks the largada.
const slot = makeSlot(makeBlock(clockHtml("", true)));
const keeper = slotKeeper(slot, makeBlock);
value(slot, "largada_s").value = "12.345";
const touched = new Set(["largada_s"]);

// The prova becomes one without a clock: the block leaves the page.
out.removed_changed = keeper.update(null, touched);
out.removed_empty = slot.firstElementChild === null;
// Back to a prova with a clock whose default is "off": the block returns
// with the largada the reviewer marked, and the box the reviewer never
// touched takes the new prova's default.
out.back_changed = keeper.update(clockHtml("", false), touched);
out.back_largada = value(slot, "largada_s").value;
out.back_show = value(slot, "show_clock").checked;
// The same answer again (car typed): the block is swapped in place.
out.again_changed = keeper.update(clockHtml("", false), touched);
// A page loaded without the block gets it from the first answer.
const empty = makeSlot(null);
out.first_changed = slotKeeper(empty, makeBlock).update(clockHtml("", true), new Set());
out.first_show = value(empty, "show_clock").checked;

// carryOverFields alone: a ticked box the reviewer unticked stays unticked.
const incoming = [{ name: "show_pass_result", type: "checkbox", checked: true }];
carryOverFields([{ name: "show_pass_result", type: "checkbox", checked: false }], incoming,
                new Set(["show_pass_result"]));
out.carried_box = incoming[0].checked;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def node_results() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed; the JavaScript half needs it")
    finished = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT],
        env={"MODULE": (STATIC_DIR / "js" / "pass_blocks.js").as_uri(), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_js_asks_with_the_complete_values_only(node_results):
    # Trimmed prova, car and passagem when they are whole numbers...
    assert node_results["query_full"] == "prova=Manobrabilidade&car_number=26&passagem=2"
    # ...and nothing for an empty prova, a half-typed car or a passagem 0:
    # the orchestrator then uses the saved values, as a save would.
    assert node_results["query_partial"] == ""


def test_js_takes_the_block_out_for_a_prova_without_a_clock(node_results):
    assert node_results["removed_changed"] is True
    assert node_results["removed_empty"] is True


def test_js_brings_the_block_back_with_the_largada_the_reviewer_marked(node_results):
    assert node_results["back_changed"] is True
    assert node_results["back_largada"] == "12.345"
    # Untouched by the reviewer: the new prova's default (off) wins.
    assert node_results["back_show"] is False


def test_js_swaps_a_block_in_place_when_it_stays(node_results):
    # Not a change of shape: the page does not need to redraw the cut bar.
    assert node_results["again_changed"] is False


def test_js_adds_the_block_to_a_page_that_had_none(node_results):
    assert node_results["first_changed"] is True
    assert node_results["first_show"] is True


def test_js_keeps_a_box_the_reviewer_changed(node_results):
    assert node_results["carried_box"] is False
