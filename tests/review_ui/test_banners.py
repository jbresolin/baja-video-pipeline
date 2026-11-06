"""Banners keep their text in a block element (LIVESTACK-42 item 10).

Background for a beginner: a "banner" is the coloured box at the top of a
page ("Depois de revogar, remova o PC do tailnet à mão…", "Alterações
salvas.", "Alguém alterou este vídeo…"). Some banners are laid out as a
flex row (text on the left, a button on the right). In a flex row every
direct child becomes its own column, and so does every loose piece of
text. The tailnet note on Admin → Workers was written straight into its
banner, so its plain text, its link, its <code> and its <em> each became a
narrow column: unreadable.

The rule these tests enforce: inside an element with class "banner", the
direct children are block elements (a <p>, a <div>, a list, a form) or an
action button, and there is never loose text. Then the text is always one
paragraph, however the banner itself is laid out.

The tests render every page that can show a banner and check each banner.
"""

from html.parser import HTMLParser

from orchestrator.testing.scenarios import send_video
from shared.models import WorkerRole
from shared.testing.credentials import PLACEHOLDER_WRONG_PASSWORD
from tests.review_ui.admin_browser import post_form
from tests.review_ui.test_login import sign_in
from tests.review_ui.test_templates_render import CASES, render

# Elements that start a block of their own, so text inside them is one paragraph.
BLOCK_TAGS = {"p", "div", "ul", "ol", "form", "h2", "h3", "section", "details"}
# A button beside the text is the banner's action ("Recarregar").
ACTION_TAGS = {"button"}
# Tags that never have a closing tag in HTML, so they never become a parent.
VOID_TAGS = {"br", "img", "input", "meta", "link", "hr", "wbr", "source", "track"}


class BannerChecker(HTMLParser):
    """Walks the HTML and notes every problem directly inside a banner.

    It keeps a stack of open elements. For each open element it remembers
    whether that element is a banner, so it knows when a tag or a piece of
    text is a *direct* child of a banner (its parent is the banner itself).
    """

    def __init__(self) -> None:
        super().__init__()
        self.open_elements: list[tuple[str, bool]] = []  # (tag, is_banner)
        self.banner_count = 0
        self.problems: list[str] = []

    def _parent_is_banner(self) -> bool:
        return bool(self.open_elements) and self.open_elements[-1][1]

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if self._parent_is_banner() and tag not in BLOCK_TAGS | ACTION_TAGS:
            self.problems.append(f"<{tag}> directly inside a banner")
        if tag in VOID_TAGS:
            return
        is_banner = "banner" in classes
        if is_banner:
            self.banner_count += 1
        self.open_elements.append((tag, is_banner))

    def handle_endtag(self, tag):
        # Pop back to the matching open tag (HTML lets some tags close implicitly).
        for index in range(len(self.open_elements) - 1, -1, -1):
            if self.open_elements[index][0] == tag:
                del self.open_elements[index:]
                return

    def handle_data(self, data):
        if self._parent_is_banner() and data.strip():
            self.problems.append(f"loose text directly inside a banner: {data.strip()[:40]!r}")


def check_banners(html: str) -> BannerChecker:
    checker = BannerChecker()
    checker.feed(html)
    checker.close()
    return checker


# --- The checker itself, so a passing test means something ---


def test_the_checker_catches_the_broken_tailnet_note():
    # This is how the Workers page used to write the note: text, a link and
    # <code> loose inside a flex banner.
    broken = '<div class="banner warning">Remova <a href="#">aqui</a> a <code>baja-w</code></div>'

    problems = check_banners(broken).problems

    assert "loose text directly inside a banner: 'Remova'" in problems
    assert "<a> directly inside a banner" in problems
    assert "<code> directly inside a banner" in problems


def test_the_checker_accepts_text_in_a_paragraph_and_a_button_beside_it():
    good = '<div class="banner warning"><p>Remova <a href="#">aqui</a></p><button>Ok</button></div>'

    result = check_banners(good)

    assert result.banner_count == 1
    assert result.problems == []


# --- Every page that can show a banner ---


def test_the_reviewer_templates_keep_banner_text_in_a_block():
    # The job page always carries the (hidden) "someone changed this job"
    # banner; the login page shows an error banner in its test case.
    checked = 0
    for name, build_context in CASES.items():
        result = check_banners(render(name, build_context()))
        assert result.problems == [], f"{name}: {result.problems}"
        checked += result.banner_count
    assert checked >= 2


def test_the_login_error_banner_keeps_its_text_in_a_block(anonymous_ui):
    page = sign_in(anonymous_ui, password=PLACEHOLDER_WRONG_PASSWORD)

    result = check_banners(page.text)

    assert result.banner_count == 1
    assert result.problems == []


def test_every_admin_page_keeps_banner_text_in_a_block(admin, harness):
    # Records on every page, a "saved" banner (?feito=), an error banner (a
    # refused change) and the new-token banner, besides the Workers page's
    # fixed tailnet note.
    send_video(harness, sender_phone="5511987654321")
    harness.enroll(WorkerRole.WORKER, "pc-da-maria")
    pages = {
        f"/review/admin/{name}?feito=settings_updated": admin.get(
            f"/review/admin/{name}?feito=settings_updated"
        ).text
        for name in ("users", "contacts", "provas", "teams", "enrollment", "workers", "settings")
    }
    pages["new token"] = post_form(
        admin,
        "/review/admin/enrollment/create",
        volunteer_name="Maria",
        role="worker",
        valid_for_hours="72",
    ).text
    # The same prova name twice: the orchestrator refuses the second one.
    post_form(admin, "/review/admin/provas/create", name="Abertura")
    refused = post_form(admin, "/review/admin/provas/create", name="ABERTURA")
    assert refused.status_code == 409
    pages["refused change"] = refused.text

    for where, html in pages.items():
        result = check_banners(html)
        # Each page shows at least the "saved" banner (or the error / token one).
        assert result.banner_count >= 1, where
        assert result.problems == [], f"{where}: {result.problems}"


def test_the_workers_page_shows_the_tailnet_note_as_one_paragraph(admin):
    html = admin.get("/review/admin/workers").text

    # The note's link and code sit inside one <p> inside the banner.
    assert '<div class="banner warning"><p><strong>Depois de revogar' in html
    assert check_banners(html).problems == []
