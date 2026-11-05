"""The `hidden` attribute really hides things in the review UI.

Background for a beginner: in HTML, `<div hidden>` means "do not show this".
The page's JavaScript shows and hides things (the "someone changed this job"
banner, the shortcuts help, ...) by switching that attribute on and off.

The catch: the browser's built-in rule for `hidden` is the weakest kind of CSS
rule. If our stylesheet gives an element a `display` value through its class
(`.banner { display: flex }`), that class wins and `hidden` is ignored. That is
exactly the bug João saw on 2026-11-05: the banner stayed on screen forever,
even though the page had correctly decided there was nothing to warn about.

The fix is one rule at the top of app.css that makes `hidden` always win.
These tests make sure it stays there.
"""

import re
from pathlib import Path

STYLESHEET = (
    Path(__file__).resolve().parents[2]
    / "review_ui"
    / "src"
    / "review_ui"
    / "static"
    / "css"
    / "app.css"
)


def test_the_stylesheet_forces_hidden_elements_to_stay_hidden():
    # Read the stylesheet the browser loads.
    css = STYLESHEET.read_text(encoding="utf-8")

    # Look for the rule `[hidden] { display: none !important; }`, allowing any
    # spacing. `!important` is what lets it beat class rules like `.banner`.
    rule = re.search(r"\[hidden\]\s*\{\s*display\s*:\s*none\s*!important\s*;?\s*\}", css)

    assert rule, "app.css must contain `[hidden] { display: none !important; }`"


def test_the_rule_comes_before_any_class_that_sets_display():
    # Order does not matter for !important, but keeping the rule at the top
    # makes it the first thing a reader sees. This guards against someone
    # "tidying" it into the middle of the file and forgetting why it exists.
    css = STYLESHEET.read_text(encoding="utf-8")
    # Remove /* comments */ first: the explanation above the rule mentions
    # `display: flex` itself, and comments are not rules.
    rules_only = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    hidden_rule_at = rules_only.index("[hidden]")
    first_display_at = rules_only.index("display:")
    assert hidden_rule_at < first_display_at, (
        "keep the [hidden] rule above every other display rule"
    )
