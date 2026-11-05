"""Helpers for the "no English left in the review UI" guard tests (LIVESTACK-39).

The review UI speaks pt-BR. These helpers find English UI words in what a
person reads: the visible text of a rendered page (plus the attributes a
browser shows, like title and aria-label), and the string literals of the
page scripts.

What is NOT checked, on purpose:
* <code> and <kbd>: ids, paths, codec names, key names, error codes.
* Anything inside an element with lang="en": the orchestrator's or a
  worker's own words (a failure message, an event reason). The UI shows
  them as they came and marks them as English for screen readers.
* <script> and <style>.
"""

import re
from html.parser import HTMLParser

# Common English UI words. A beginner's note: the check is word by word, in
# lower case, so "Save", "save" and "SAVE" all count. Words that are also
# Portuguese ("a", "no", "do", "time", "status", "online", "site", "manual")
# are left out, or the guard would flag correct pt-BR text.
ENGLISH_UI_WORDS = {
    # The examples in the brief.
    "save", "cancel", "queue", "someone", "reload", "download", "ready", "job", "jobs",
    # Small words that only appear in English sentences.
    "the", "and", "of", "to", "is", "are", "was", "were", "not", "yet", "you", "your",
    "this", "that", "with", "from", "for", "by", "at", "on", "it", "its", "has", "have",
    "will", "can", "cannot", "please", "again", "here", "there", "nothing", "never", "ago",
    "none", "only", "any", "all", "more", "than", "when", "while", "until", "once",
    # Words the review UI used before LIVESTACK-39.
    "retry", "reprocess", "restore", "discard", "discarded", "received", "delivered",
    "uploaded", "upload", "claimed", "awaiting", "review", "reviewed", "done", "failed",
    "failure", "clip", "clips", "fleet", "team", "car", "state", "filter", "apply", "copy",
    "copied", "sign", "signed", "back", "forward", "pause", "frame", "second", "seconds",
    "minute", "minutes", "hour", "hours", "cut", "length", "choose", "show", "number",
    "event", "request", "requested", "render", "rendering", "holder", "raw", "raws", "free",
    "disk", "inbox", "outbox", "storage", "used", "cap", "idle", "heartbeat", "details",
    "file", "size", "duration", "resolution", "rotation", "recorded", "colour", "color",
    "bitrate", "video", "audio", "thumbnail", "sender", "unknown", "unavailable", "found",
    "error", "changed", "keyboard", "shortcuts", "low", "res", "archive",
    "archived", "kept", "studio", "path", "loading", "lookup", "looking", "connection",
    "refresh", "live", "retrying", "saved", "marked", "variable", "channels", "transfer",
    "primaries", "role", "reviewer", "users", "contacts", "settings", "enrollment",
    "revoke", "revoked", "allowed", "blocked", "add", "remove", "rename", "sync", "synced",
}  # fmt: skip

# Proper nouns and technical terms that stay as they are in pt-BR text.
# (None of them is in the list above today; the allowlist documents the
# decision and wins if one is ever added there.)
ALLOWLIST = {
    "whatsapp", "proxy", "hdr", "sdr", "h264", "hevc", "aac", "worker", "workers",
    "online", "offline", "admin", "token", "tokens", "tailnet", "tailscale", "webhook",
    "evolution", "mbps", "fps", "khz", "gb", "ok", "baja", "bajaworker", "gps", "api",
    "proxies",
    "ctrl", "shift", "enter", "esc", "tags", "pixel",
}  # fmt: skip

# Attributes a browser shows to people (tooltips, screen readers, placeholders, dialogs).
SHOWN_ATTRIBUTES = {"title", "aria-label", "placeholder", "alt", "data-confirm"}
SKIPPED_TAGS = {"code", "kbd", "script", "style"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
             "source", "track", "wbr"}  # fmt: skip

# A token: letters and digits, possibly joined by "-", "_" or "." (ids,
# slugs and file names such as "job-awai", "w-studio", "VID_0042.mp4").
_TOKEN = re.compile(r"[^\W_]+(?:[-_.][^\W_]+)*")
_PLAIN_WORD = re.compile(r"^[^\W\d_]+$")


class _VisibleText(HTMLParser):
    """Collects what a person reads on a page, leaving out the skipped parts."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        # Open elements, each with "is this one skipped?".
        self._open: list[tuple[str, bool]] = []

    def _skipping(self) -> bool:
        return any(skipped for _, skipped in self._open)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        skipped = tag in SKIPPED_TAGS or attributes.get("lang") == "en"
        if not (skipped or self._skipping()):
            for name in SHOWN_ATTRIBUTES & attributes.keys():
                self.parts.append(attributes[name] or "")
        if tag not in VOID_TAGS:
            self._open.append((tag, skipped))

    def handle_endtag(self, tag):
        # Close up to the matching open tag (forgiving, like browsers).
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                del self._open[index:]
                break

    def handle_data(self, data):
        if not self._skipping():
            self.parts.append(data)


def visible_text(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return " ".join(parser.parts)


def english_words_in(text: str) -> set[str]:
    """The English UI words found in `text` (lower case).

    Only plain words count: a token joined by "-", "_" or "." or holding a
    digit is a name from the data (a job id, a worker id, a file name).
    """
    words = {token.lower() for token in _TOKEN.findall(text) if _PLAIN_WORD.match(token)}
    return (words & ENGLISH_UI_WORDS) - ALLOWLIST


# --- Page scripts ---

# A JavaScript string literal: "…", '…' or `…` (no nesting needed here).
_JS_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`')
_JS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
# Literals that are code, not words for people: identifiers ("ready",
# "keydown", "bvp-review-flash"; action names are API values and stay
# English), catalog keys ("script.copy") and URL paths ("/review/api/…").
_CODE_LITERAL = re.compile(r"^(?:[a-z][\w.\-]*|/[\w/.\- ]*)$")


def js_string_literals(source: str) -> list[str]:
    """Every string literal in a script, comments removed, ${…} parts dropped."""
    without_comments = _JS_COMMENT.sub("", source)
    literals = [match.group(0)[1:-1] for match in _JS_STRING.finditer(without_comments)]
    return [re.sub(r"\$\{[^}]*\}", " ", literal) for literal in literals]


def english_in_js(source: str) -> dict[str, set[str]]:
    """{literal: English words in it} for the literals that would show English."""
    found = {}
    for literal in js_string_literals(source):
        if _CODE_LITERAL.match(literal.strip()):
            continue
        words = english_words_in(literal)
        if words:
            found[literal] = words
    return found
