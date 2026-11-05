"""The sign-in and sign-out pages (/review/login, /review/logout).

The password goes to the orchestrator; on success the UI sets the
bvp_session cookie and sends the browser on. The cookie's flags are what
keep the session safe in a browser:

    HttpOnly   page scripts cannot read it (a script injected into a page
               could not steal the session)
    Secure     sent over HTTPS only
    SameSite=Lax  not sent on form posts started by another site
    Path=/     also sent for /proxies/* and /thumbs/*, which Caddy checks
               with the orchestrator (forward_auth) before serving
"""

from review_ui.session import safe_next_path

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def sign_in(browser, username="ana", password="secret", next_path="/review/clips", **headers):
    return browser.post(
        "/review/login",
        content=f"username={username}&password={password}&next={next_path}",
        headers={**FORM, **headers},
    )


def test_the_login_page_is_in_portuguese_and_remembers_where_to_go(anonymous_ui):
    html = anonymous_ui.get("/review/login?next=/review/fleet").text

    assert "<h1>Entrar</h1>" in html
    assert 'name="next" value="/review/fleet"' in html
    # Not signed in yet: no navigation to pages that would need a session.
    assert 'href="/review/clips"' not in html


def test_the_right_password_sets_a_safe_session_cookie_and_goes_on(anonymous_ui, fake_orchestrator):
    response = sign_in(anonymous_ui)

    assert response.status_code == 303
    assert response.headers["location"] == "/review/clips"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("bvp_session=ana-session-token;")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag in cookie
    # The password went to the orchestrator as JSON, never stored by the UI.
    [login] = fake_orchestrator.requests_to("POST", "/auth/login")
    assert login.json_body == {"username": "ana", "password": "secret"}


def test_with_the_cookie_the_browser_can_now_open_pages(anonymous_ui):
    sign_in(anonymous_ui)
    # httpx keeps cookies like a browser does.
    assert anonymous_ui.get("/review/").status_code == 200


def test_a_wrong_password_shows_the_form_again_with_a_message(anonymous_ui):
    response = sign_in(anonymous_ui, password="wrong")

    assert response.status_code == 401
    assert "Usuário ou senha incorretos" in response.text
    assert "set-cookie" not in response.headers


def test_the_browsers_address_is_passed_on_for_the_rate_limit(anonymous_ui, fake_orchestrator):
    # Caddy sets X-Forwarded-For; the orchestrator limits sign-ins per address.
    sign_in(anonymous_ui, **{"X-Forwarded-For": "203.0.113.9"})

    [login] = fake_orchestrator.requests_to("POST", "/auth/login")
    assert login.headers["x-forwarded-for"] == "203.0.113.9"


def test_after_login_the_browser_only_goes_to_pages_of_this_site():
    # A crafted link like /review/login?next=https://evil.example must not
    # send people to another site after they typed their password.
    assert safe_next_path("/review/jobs/abc") == "/review/jobs/abc"
    for bad in ("https://evil.example/", "//evil.example/review", "/elsewhere", "", None):
        assert safe_next_path(bad) == "/review/"
    # Nor back to the login page itself.
    assert safe_next_path("/review/login") == "/review/"


def test_a_login_form_posted_from_another_site_is_refused(anonymous_ui):
    response = sign_in(anonymous_ui, Origin="https://evil.example")
    assert response.status_code == 403
    cross_site = sign_in(anonymous_ui, **{"Sec-Fetch-Site": "cross-site"})
    assert cross_site.status_code == 403


def test_logout_ends_the_session_and_clears_the_cookie(ui, fake_orchestrator):
    response = ui.post("/review/logout", content="", headers=FORM)

    assert response.status_code == 303
    assert response.headers["location"] == "/review/login"
    assert 'bvp_session=""' in response.headers["set-cookie"]
    assert fake_orchestrator.requests_to("POST", "/auth/logout")
