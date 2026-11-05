"""The pt-BR catalog (review_ui/catalog/) has a text for everything the UI shows.

Labels are looked up by the API's English values ("state.awaiting_review",
"action.save", "error.not_found"), so each test below walks one list of
values (an enum, the orchestrator's event kinds, the keys the templates and
scripts ask for) and checks that the catalog has words for every one. A new
state or error code added to the API then fails here, not on a reviewer's
screen as a raw key.
"""

import re
import string
from pathlib import Path

import pytest

from orchestrator.domain.review_queue import NO_HOLDER_ONLINE
from review_ui.catalog import CATALOG, SCRIPT_TEXT, has_text, text
from review_ui.job_view import NO_HOLDER_ONLINE_REASON, describe_waiting_reason
from review_ui.templating import PACKAGE_DIR, STATIC_DIR, TEMPLATES_DIR
from shared.models import (
    DeliveryRoute,
    ErrorCode,
    JobState,
    ReceivedAs,
    ReviewAction,
    TaskKind,
    UserRole,
    WorkerRole,
)

ORCHESTRATOR_SOURCE = PACKAGE_DIR.parents[2] / "orchestrator" / "src" / "orchestrator"


def missing(keys) -> list[str]:
    return sorted(key for key in keys if not has_text(key))


# --- Every value of the API's lists has words ---


def test_every_job_state_has_a_label_and_an_explanation():
    # The label ("aguardando revisão") goes on badges and filters; the
    # explanation is the first sentence of the job page's status strip.
    assert missing(f"state.{state.value}" for state in JobState) == []
    assert missing(f"explain.{state.value}" for state in JobState) == []


def test_the_state_labels_are_the_agreed_ones():
    # The glossary of the LIVESTACK-39 pull request; the API values stay English.
    labels = {state.value: text(f"state.{state.value}") for state in JobState}

    assert labels == {
        "received": "recebido",
        "proxy_ready": "proxy pronto",
        "awaiting_review": "aguardando revisão",
        "ready": "pronto",
        "claimed": "em processamento",
        "uploaded": "enviado",
        "delivered": "entregue",
        "done": "concluído",
        "failed": "falhou",
        "discarded": "descartado",
    }


def test_every_review_action_has_a_button_and_a_done_message():
    assert missing(f"action.{action.value}" for action in ReviewAction) == []
    assert missing(f"action_done.{action.value}" for action in ReviewAction) == []


def test_every_task_kind_reads_in_a_sentence():
    # "<worker> está renderizando o clipe." and "falhou ao renderizar o clipe".
    assert missing(f"task.doing.{kind.value}" for kind in TaskKind) == []
    assert missing(f"task.verb.{kind.value}" for kind in TaskKind) == []


@pytest.mark.parametrize(
    ("prefix", "values"),
    [
        ("error", [code.value for code in ErrorCode]),
        ("received_as", [received.value for received in ReceivedAs]),
        ("worker_role", [role.value for role in WorkerRole]),
        ("role", [role.value for role in UserRole]),
        ("delivery.route", [route.value for route in DeliveryRoute]),
    ],
)
def test_every_value_of_the_other_api_lists_has_words(prefix, values):
    assert missing(f"{prefix}.{value}" for value in values) == []


def test_every_event_kind_the_orchestrator_records_has_words():
    # Setup: read the kinds from the orchestrator's code: kind="..." and the
    # admin changes (record_admin_change(connection, now, actor, "<kind>", ...)).
    source = "\n".join(path.read_text() for path in ORCHESTRATOR_SOURCE.rglob("*.py"))
    kinds = set(re.findall(r'kind="([a-z_]+)"', source))
    kinds |= set(re.findall(r'record_admin_change\(\s*\w+,\s*\w+,\s*\w+,\s*"([a-z_]+)"', source))
    kinds |= {"lease_ended"}  # the default of transitions.end_lease's `kind`
    kinds -= {"state_changed"}  # shown as "<old state> → <new state>" instead

    # Enough kinds were found for the check to mean something...
    assert len(kinds) > 25
    # ...and each has words (the kind itself stays English in the DB).
    assert missing(f"event.{kind}" for kind in kinds) == []


# --- The orchestrator's English reasons the UI translates ---


def test_the_no_holder_reason_matches_the_orchestrators_sentence():
    # The UI recognises this exact sentence; if the orchestrator rewords
    # it, the UI would silently fall back to showing the English.
    assert NO_HOLDER_ONLINE_REASON == NO_HOLDER_ONLINE


def test_a_task_failed_on_a_worker_reason_is_put_in_words():
    # Setup: the reason as orchestrator/domain/task_results.py writes it.
    source = (ORCHESTRATOR_SOURCE / "domain" / "task_results.py").read_text()
    assert "f\"{kind} failed on {worker_row['name']}: {request.message[:200]}\"" in source

    described = describe_waiting_reason("make_proxy failed on pc-da-maria: ffmpeg exited")

    assert described.text == "Falhou ao renderizar o proxy em pc-da-maria:"
    # The worker's own message is kept, to be shown marked as English.
    assert described.detail == "ffmpeg exited"


def test_an_unknown_reason_is_kept_as_it_came():
    described = describe_waiting_reason("something new the orchestrator says")

    assert (described.text, described.detail) == ("", "something new the orchestrator says")


# --- Every key the code asks for exists ---

TEMPLATE_KEY = re.compile(r"""\bt\(\s*["']([\w.]+)["']""")
TEMPLATE_PLURAL_KEY = re.compile(r"""\btn\(\s*["']([\w.]+)["']""")
PYTHON_KEY = re.compile(r"""\btext(?:_plural)?\(\s*["']([\w.]+)["']""")
JS_KEY = re.compile(r"""\bt\(\s*["']([\w.]+)["']""")


def keys_used(pattern: re.Pattern, paths) -> set[str]:
    return {key for path in paths for key in pattern.findall(Path(path).read_text())}


def test_every_key_the_templates_ask_for_exists():
    templates = list(TEMPLATES_DIR.rglob("*.html"))
    # A key ending in "." is a prefix completed by a value (t("role." ~ user.role));
    # those are covered by the enum tests above.
    plain = {key for key in keys_used(TEMPLATE_KEY, templates) if not key.endswith(".")}
    plural = keys_used(TEMPLATE_PLURAL_KEY, templates)

    assert len(plain) > 100  # the scan really found the template texts
    assert missing(plain) == []
    assert missing({f"{key}.one" for key in plural} | {f"{key}.other" for key in plural}) == []


def test_every_key_the_python_code_asks_for_exists():
    used = keys_used(PYTHON_KEY, PACKAGE_DIR.rglob("*.py"))

    assert len(used) > 30
    assert missing(used) == []


def test_every_key_the_page_scripts_ask_for_is_sent_to_them():
    # The scripts only get the "script.*" part of the catalog (base.html).
    used = keys_used(JS_KEY, (STATIC_DIR / "js").glob("*.js"))
    # review_form.js builds these from the field names: script.value.<field>.
    used |= {f"script.value.{field}" for field in ("prova", "car_number", "passagem")}
    used |= {f"script.value.{field}" for field in ("cut_in_s", "cut_out_s")}

    assert len(used) > 20
    assert sorted(key for key in used if key not in SCRIPT_TEXT) == []


# --- The catalog's own shape ---


def test_keys_are_english_and_placeholders_are_simple_names():
    for key, entry in CATALOG.items():
        # Keys: lower-case English words, dots and underscores.
        assert re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)*", key), key
        # Placeholders: {name} only, so Python's format() and js/text.js
        # fill them the same way (no {0}, no {name:.2f}, no stray braces).
        for _, name, spec, conversion in string.Formatter().parse(entry):
            if name is not None:
                assert re.fullmatch(r"[a-z_]+", name) and not spec and not conversion, key
