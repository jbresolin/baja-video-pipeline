"""Tests for shared.layout: where clips go on the studio box, and latest.m3u."""

from datetime import UTC, datetime

import pytest

from shared.layout import (
    PlaylistEntry,
    archive_relative_path,
    clip_file_stem,
    clip_relative_path,
    is_safe_relative_path,
    raw_file_name,
    render_latest_playlist,
    slugify_prova,
    unversioned_clip_relative_path,
    versioned_clip_relative_path,
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
    # Design §7: <prova>/<car>/ folders, always with forward slashes (also on
    # Windows, where the studio PC runs). The file itself says what it is
    # (LIVESTACK-41 item 12): prova, car with 3 digits, passagem.
    assert clip_relative_path("Enduro", 42, 3) == "enduro/42/enduro-042-p3.mp4"
    assert clip_relative_path("Suspensão", 7, 1) == "suspensao/7/suspensao-007-p1.mp4"


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


# --- LIVESTACK-41 item 12: file names people can read -----------------------

JOB_ID = "2f2510a3c0de4b1e9f00112233445566"
# 23:29:09 in São Paulo (UTC-3) on 2026-11-04 = 02:29:09 UTC the next day.
RECEIVED_AT = datetime(2026, 11, 5, 2, 29, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    ("prova_slug", "car", "passagem", "expected"),
    [
        ("enduro", 42, 3, "enduro-042-p3"),
        # Car 1 and car 123: always 3 digits, so files sort by car.
        ("aceleracao-e-velocidade", 1, 1, "aceleracao-e-velocidade-001-p1"),
        ("suspensao", 123, 2, "suspensao-123-p2"),
        # Passagem 10 and more is written as is.
        ("enduro", 7, 12, "enduro-007-p12"),
    ],
)
def test_clip_file_stem(prova_slug, car, passagem, expected):
    assert clip_file_stem(prova_slug, car, passagem) == expected


def test_later_versions_get_a_dash_v_suffix_beside_the_first():
    first = "enduro/42/enduro-042-p3.mp4"
    assert versioned_clip_relative_path(first, 1) == first
    assert versioned_clip_relative_path(first, 2) == "enduro/42/enduro-042-p3-v2.mp4"
    assert archive_relative_path(first, 3) == "enduro/42/enduro-042-p3-v3.mp4"


def test_clips_named_before_the_change_keep_their_old_version_names():
    # Files on disk are never renamed: an old "3.mp4" archived its second
    # version as "3.v2.mp4", and the job page must keep saying so.
    assert archive_relative_path("enduro/42/3.mp4", 2) == "enduro/42/3.v2.mp4"


@pytest.mark.parametrize(
    ("studio_path", "first_name"),
    [
        # A reprocess is delivered under its version name (LIVESTACK-43);
        # the clip version keeps the first name, so archive and download
        # names are built from it and never get "-v2-v2".
        ("enduro/42/enduro-042-p3-v2.mp4", "enduro/42/enduro-042-p3.mp4"),
        ("enduro/42/enduro-042-p12-v10.mp4", "enduro/42/enduro-042-p12.mp4"),
        ("enduro/42/3.v2.mp4", "enduro/42/3.mp4"),
        # Names without a version suffix come back unchanged.
        ("enduro/42/enduro-042-p3.mp4", "enduro/42/enduro-042-p3.mp4"),
        ("enduro/42/3.mp4", "enduro/42/3.mp4"),
        # A prova whose slug ends like a version is not mistaken for one:
        # only a "-v<n>" right after "-p<passagem>" is a version.
        ("teste-v2/42/teste-v2-042-p3.mp4", "teste-v2/42/teste-v2-042-p3.mp4"),
    ],
)
def test_the_first_name_of_any_version(studio_path, first_name):
    assert unversioned_clip_relative_path(studio_path) == first_name
    # And back: version 2 of the first name is the "-v2" name again.
    assert versioned_clip_relative_path(first_name, 1) == first_name


@pytest.mark.parametrize(
    ("display_name", "phone", "expected_start"),
    [
        ("João Guilherme Bresolin", "5511987654321", "joao-guilherme-bresolin-"),
        # Emoji and punctuation disappear; accents become plain letters.
        ("Zé 🏁 da Equipe!!", "5511987654321", "ze-da-equipe-"),
        # No display name (or nothing usable left of it): the phone number.
        (None, "5511987654321", "5511987654321-"),
        ("🏁🏁", "+55 (11) 98765-4321", "5511987654321-"),
        # Neither (never expected): still a valid name.
        (None, None, "remetente-"),
    ],
)
def test_raw_file_name_says_who_sent_it(display_name, phone, expected_start):
    name = raw_file_name(display_name, phone, RECEIVED_AT, JOB_ID, ".MP4")
    assert name.startswith(expected_start)
    # Then the time the bot received it, in São Paulo time, the short job
    # id (traceable to the review UI) and the extension in lowercase.
    assert name.endswith("-20261104-232909-2f2510a3.mp4")


def test_a_very_long_name_is_cut_for_windows_paths():
    name = raw_file_name("Maria " * 30, None, RECEIVED_AT, JOB_ID, ".mov")
    sender = name.removesuffix("-20261104-232909-2f2510a3.mov")
    assert len(sender) <= 40 and not sender.endswith("-")


def test_the_new_clip_names_pass_the_studio_safety_check():
    assert is_safe_relative_path(
        versioned_clip_relative_path(clip_relative_path("Enduro", 1, 10), 3)
    )
