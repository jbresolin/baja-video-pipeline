"""Two reviewers on the same video (LIVESTACK-51 item 1): the orchestrator's side.

On the 2026-11-07 test João and Alessander opened the same video and both
worked on it; the version check only stopped the second save. Now:

* while a job page is open it pings `PUT /jobs/{id}/presence` every ~20 s;
  the answer lists the *other* reviewers on that job and since when;
* `DELETE /jobs/{id}/presence` when the page closes;
* an entry with no ping for 45 s is gone (a closed laptop never keeps a
  video away from the others: nothing is ever locked);
* `GET /review/queue` says, per job, who is on it (`JobSummary.reviewing`).

The harness signs in two users: "ana" (a reviewer, `reviewer_auth`) and
"joao" (an admin, `admin_auth`); both may review. Its clock is manual:
`advance(seconds)`, starting at 2026-10-25 12:00 UTC.
"""

from orchestrator.domain.presence import PresenceBoard
from orchestrator.testing.harness import ADMIN_NAME, REVIEWER_NAME
from orchestrator.testing.scenarios import receive_job


def ping(harness, job_id, auth):
    response = harness.client.put(f"/jobs/{job_id}/presence", auth=auth)
    assert response.status_code == 200, response.text
    return response.json()["others"]


def queue_reviewing(harness) -> dict[str, list[str]]:
    response = harness.client.get(
        "/review/queue", params={"state": "received"}, auth=harness.reviewer_auth
    )
    return {
        item["id"]: [reviewer["username"] for reviewer in item["reviewing"]]
        for item in response.json()["items"]
    }


def test_the_second_reviewer_is_told_who_is_already_on_the_video(harness):
    job_id = receive_job(harness)

    # Ana opens the video first: nobody else is on it. Her page then pings
    # every 20 s for two minutes.
    assert ping(harness, job_id, harness.reviewer_auth) == []
    for _ in range(6):
        harness.clock.advance(20)
        ping(harness, job_id, harness.reviewer_auth)
    # João opens it: he is told Ana is on it, and since when (her arrival).
    others = ping(harness, job_id, harness.admin_auth)
    assert others == [{"username": REVIEWER_NAME, "since": "2026-10-25T12:00:00Z"}]
    # And Ana's next ping names João: both know about each other.
    harness.clock.advance(20)
    assert [r["username"] for r in ping(harness, job_id, harness.reviewer_auth)] == [ADMIN_NAME]


def test_since_is_when_the_reviewer_arrived_not_their_last_ping(harness):
    job_id = receive_job(harness)
    ping(harness, job_id, harness.reviewer_auth)
    for _ in range(3):  # a minute of pings
        harness.clock.advance(20)
        ping(harness, job_id, harness.reviewer_auth)

    others = ping(harness, job_id, harness.admin_auth)

    assert others[0]["since"] == "2026-10-25T12:00:00Z"


def test_a_reviewer_whose_pings_stop_is_gone_after_45_seconds(harness):
    # Ana closes her laptop: no goodbye, the pings just stop.
    job_id = receive_job(harness)
    ping(harness, job_id, harness.reviewer_auth)

    # 44 s later she still counts (a ping may be late)...
    harness.clock.advance(44)
    assert queue_reviewing(harness)[job_id] == [REVIEWER_NAME]
    # ...at 45 s she does not: the video is free for anyone, with no lock.
    harness.clock.advance(1)
    assert ping(harness, job_id, harness.admin_auth) == []
    assert queue_reviewing(harness)[job_id] == [ADMIN_NAME]


def test_leaving_the_page_ends_it_at_once(harness):
    job_id = receive_job(harness)
    ping(harness, job_id, harness.reviewer_auth)

    response = harness.client.delete(f"/jobs/{job_id}/presence", auth=harness.reviewer_auth)

    assert response.status_code == 204
    assert ping(harness, job_id, harness.admin_auth) == []


def test_the_queue_says_who_is_on_each_video(harness):
    first = receive_job(harness, b"first video")
    second = receive_job(harness, b"second video")
    ping(harness, first, harness.reviewer_auth)

    reviewing = queue_reviewing(harness)

    # Everyone on the job is listed (the review UI leaves out the signed-in
    # user); a job nobody has open has an empty list.
    assert reviewing == {first: [REVIEWER_NAME], second: []}


def test_presence_needs_a_session_and_a_job_that_exists(harness):
    job_id = receive_job(harness)

    assert harness.client.put(f"/jobs/{job_id}/presence").status_code == 401
    assert (
        harness.client.put("/jobs/no-such-job/presence", auth=harness.reviewer_auth).status_code
        == 404
    )


# --- The board itself (no HTTP) ---


def test_the_same_user_in_two_tabs_is_one_entry(harness):
    board = PresenceBoard()
    now = harness.clock.now()

    board.ping("job-1", "bruno", now)
    board.ping("job-1", "bruno", now)

    assert [reviewer.username for reviewer in board.everyone(now)["job-1"]] == ["bruno"]
