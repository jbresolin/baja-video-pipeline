"""Tests for shared.layout: where clips go on the studio box, and latest.m3u."""

import pytest

from shared.layout import (
    PlaylistEntry,
    clip_relative_path,
    is_safe_relative_path,
    render_latest_playlist,
    slugify_prova,
)


@pytest.mark.parametrize(
    ("prova", "expected"),
    [
        ("Enduro", "enduro"),
        # Accents are removed, so the folder name is plain ASCII everywhere.
        ("Suspensão", "suspensao"),
        ("Aceleração", "aceleracao"),
        # Spaces and punctuation become single dashes; ends are trimmed.
        ("Enduro de Resistência", "enduro-de-resistencia"),
        ("  Tração / Lama! ", "tracao-lama"),
    ],
)
def test_slugify_prova(prova, expected):
    assert slugify_prova(prova) == expected


def test_slugify_refuses_names_without_letters_or_digits():
    # "!!!" would give an empty folder name, which would put clips in the
    # wrong place, so it must be an error instead.
    with pytest.raises(ValueError):
        slugify_prova("!!!")


def test_clip_relative_path_follows_prova_car_passagem():
    # Design §7: <prova>/<car>/<passagem>.mp4, always with forward slashes
    # (also on Windows, where the studio PC runs).
    assert clip_relative_path("Enduro", 42, 3) == "enduro/42/3.mp4"
    assert clip_relative_path("Suspensão", 7, 1) == "suspensao/7/1.mp4"


@pytest.mark.parametrize(
    ("path", "safe"),
    [
        ("enduro/42/3.mp4", True),
        # Climbing out of the clip root with ".." must be refused.
        ("../../Windows/evil.mp4", False),
        ("enduro/../../x.mp4", False),
        # Absolute paths would ignore the clip root completely.
        ("/etc/x.mp4", False),
        # Backslashes are Windows separators; refuse them to keep one format.
        ("enduro\\42\\3.mp4", False),
        # Only .mp4 files are clips.
        ("enduro/42/3.exe", False),
        ("", False),
    ],
)
def test_is_safe_relative_path(path, safe):
    assert is_safe_relative_path(path) is safe


def test_every_generated_path_is_safe():
    # Whatever we build ourselves must pass our own safety check.
    assert is_safe_relative_path(clip_relative_path("Tração / Lama", 101, 12))


def test_latest_playlist_lists_newest_first_with_titles():
    entries = [
        PlaylistEntry("enduro/42/3.mp4", "Enduro", 42, 3, team_name="Equipe X"),
        PlaylistEntry("enduro/7/1.mp4", "Enduro", 7, 1),
    ]
    text = render_latest_playlist(entries)
    lines = text.splitlines()

    # Line 1 is the required header for "extended" M3U playlists.
    assert lines[0] == "#EXTM3U"
    # Then, per clip: an info line, then the path. Order is kept as given
    # (newest first), so OBS plays the newest clip first.
    assert lines[1] == "#EXTINF:-1,Enduro - carro 42 Equipe X - passagem 3"
    assert lines[2] == "enduro/42/3.mp4"
    assert lines[3] == "#EXTINF:-1,Enduro - carro 7 - passagem 1"
    assert lines[4] == "enduro/7/1.mp4"
    assert text.endswith("\n")


def test_latest_playlist_is_capped():
    entries = [PlaylistEntry(f"enduro/{n}/1.mp4", "Enduro", n, 1) for n in range(10)]
    text = render_latest_playlist(entries, max_entries=3)
    # 1 header line + 3 clips × 2 lines each = 7 lines.
    assert len(text.splitlines()) == 7
    # The three kept are the first three given (the newest).
    assert "enduro/2/1.mp4" in text
    assert "enduro/3/1.mp4" not in text
