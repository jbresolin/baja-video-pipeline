"""The studio's clip folder is readable by OBS (worker.studio.clip_folder, LIVESTACK-41 item 5).

The installer locks C:\\BajaWorker to SYSTEM + Administrators; OBS runs as
the logged-in user. So the studio keeps its clips in a folder of its own
(C:\\BajaClips by default) and grants the local Users group read access.
The icacls call is checked through a fake command runner (no Windows here).
"""

import subprocess

from worker.studio.clip_folder import (
    USERS_GROUP_SID,
    grant_users_read_command,
    prepare_clip_root,
    suggested_clip_root,
)


def test_the_folder_is_created_and_users_get_inherited_read_access(tmp_path):
    clip_root = tmp_path / "BajaClips"
    commands = []

    def fake_icacls(arguments):
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "processed 1 file", "")

    problem = prepare_clip_root(clip_root, windows=True, run=fake_icacls)

    assert problem is None
    assert clip_root.is_dir()
    # By SID, not by name: on a Portuguese Windows the group is "Usuários".
    # (OI)(CI) makes every clip and latest.m3u inherit it; RX = read.
    assert commands == [grant_users_read_command(clip_root)]
    assert f"{USERS_GROUP_SID}:(OI)(CI)RX" in commands[0]
    assert commands[0][:2] == ["icacls", str(clip_root)]


def test_a_failed_grant_is_reported_in_portuguese_but_does_not_stop_the_studio(tmp_path):
    def failing_icacls(arguments):
        return subprocess.CompletedProcess(arguments, 5, "", "Acesso negado.")

    problem = prepare_clip_root(tmp_path / "BajaClips", windows=True, run=failing_icacls)

    assert problem is not None
    assert "OBS" in problem and "Acesso negado." in problem


def test_outside_windows_only_the_folder_is_created(tmp_path):
    def must_not_run(arguments):
        raise AssertionError("no icacls outside Windows")

    assert prepare_clip_root(tmp_path / "box", windows=False, run=must_not_run) is None
    assert (tmp_path / "box").is_dir()


def test_the_suggestion_outside_windows_is_a_box_folder_in_the_working_folder(tmp_path):
    # On Windows the form proposes C:\BajaClips; elsewhere (dev, tests) the
    # old default stays, so local runs need no extra folder.
    assert suggested_clip_root(tmp_path) == str(tmp_path / "box")
