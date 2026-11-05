"""Which URL the worker POSTs to when it enrolls.

Background for a beginner: the installer stamps an "enroll URL" into the
worker's config. It is supposed to be just the public website, for example
"https://live.example.org", and the worker adds "/enroll" to it. If someone
writes the full endpoint instead ("https://live.example.org/enroll"), simply
adding "/enroll" again would give ".../enroll/enroll", a page that does not
exist, and enrollment would fail forever with "404 Not found". These tests
check that every reasonable spelling ends up at exactly ONE "/enroll".
"""

import pytest

from worker.api_client import enroll_endpoint


@pytest.mark.parametrize(
    "configured",
    [
        "https://live.example.org",  # the intended spelling: just the site
        "https://live.example.org/",  # same, with a trailing slash
        "https://live.example.org/enroll",  # the full endpoint, as people paste it
        "https://live.example.org/enroll/",  # full endpoint plus trailing slash
    ],
)
def test_every_spelling_reaches_the_single_enroll_endpoint(configured):
    assert enroll_endpoint(configured) == "https://live.example.org/enroll"


def test_a_site_whose_path_merely_contains_enroll_is_left_alone():
    # Only a final "/enroll" path segment is treated as the endpoint. A base
    # that happens to end in other text keeps it, and still gets "/enroll".
    assert enroll_endpoint("https://live.example.org/reenroll") == (
        "https://live.example.org/reenroll/enroll"
    )
