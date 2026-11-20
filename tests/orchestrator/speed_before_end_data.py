"""The "speed N seconds before the clip's end" configuration the LIVESTACK-68 tests use.

What this module is for: the brief's AVF configuration (tests/orchestrator/
avf_data.py) with `"seconds_before_end": 3` added to its speed block, so
the top speed under the clock is shown for the last 3 seconds of the clip
with no mark; and a helper that saves a configuration on the Settings page
(the admin switching a prova between the two ways mid-event).
"""

import copy
import json

from tests.orchestrator.avf_data import AVF_CONFIG

# The brief's example: the speed for the last 3 seconds of the clip.
SECONDS_BEFORE_END = 3


def avf_config_text(seconds_before_end: float | None = SECONDS_BEFORE_END) -> str:
    """The AVF configuration as typed text; None leaves the key out (the mark is used)."""
    config = copy.deepcopy(AVF_CONFIG)
    if seconds_before_end is not None:
        config["provas"]["AVF"]["speed"]["seconds_before_end"] = seconds_before_end
    return json.dumps(config, ensure_ascii=False, indent=2)


BEFORE_END_CONFIG_TEXT = avf_config_text()


def save_config(harness, config_text: str) -> None:
    """The admin saves this configuration on the Settings page."""
    saved = harness.client.put(
        "/admin/pass-results/config",
        json={"config_text": config_text},
        auth=harness.admin_auth,
    )
    assert saved.status_code == 200, saved.text
