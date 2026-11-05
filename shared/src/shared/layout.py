"""Folder layout of finished clips on the studio box (design §7).

    <clip root>/
        latest.m3u                 newest clips first, for OBS
        <prova>/<car>/<passagem>.mp4

`prova` is turned into a "slug" (lowercase ASCII, dashes) so the path is
the same on Windows and Linux whatever accents the reviewer typed:
"Suspensão" → "suspensao", "Enduro de Resistência" → "enduro-de-resistencia".
"""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath

LATEST_PLAYLIST_NAME = "latest.m3u"
DEFAULT_PLAYLIST_LENGTH = 50


def slugify_prova(prova: str) -> str:
    """Make a folder-safe name out of a prova name."""
    # NFKD splits "ã" into "a" + a combining tilde; encoding to ASCII with
    # "ignore" then drops the tilde and keeps the "a".
    ascii_text = unicodedata.normalize("NFKD", prova).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if not slug:
        raise ValueError(f"prova {prova!r} has no letters or digits to build a folder name from")
    return slug


def clip_relative_path(prova: str, car_number: int, passagem: int) -> str:
    """The clip's path relative to the clip root, always with forward slashes.

    Example: clip_relative_path("Enduro", 42, 3) == "enduro/42/3.mp4".
    """
    return clip_relative_path_in_folder(slugify_prova(prova), car_number, passagem)


def clip_relative_path_in_folder(prova_slug: str, car_number: int, passagem: int) -> str:
    """Same, from a prova folder name that is already a slug.

    The orchestrator stores each prova's slug when the prova is created and
    keeps it across renames (orchestrator/domain/provas_catalog.py), so it
    builds paths from that stored slug instead of the current name.
    """
    return str(PurePosixPath(prova_slug, str(car_number), f"{passagem}.mp4"))


def archive_relative_path(clip_path: str, version: int) -> str:
    """Where the master archives one version of a clip (design §11 as amended).

    Version 1 keeps the box path; later versions (after a reprocess) get a
    ".v<n>" before the extension, so no version replaces another:
        archive_relative_path("enduro/42/3.mp4", 1) == "enduro/42/3.mp4"
        archive_relative_path("enduro/42/3.mp4", 2) == "enduro/42/3.v2.mp4"
    """
    if version < 1:
        raise ValueError("clip versions start at 1")
    if version == 1:
        return clip_path
    path = PurePosixPath(clip_path)
    return str(path.with_name(f"{path.stem}.v{version}{path.suffix}"))


def is_safe_relative_path(relative_path: str) -> bool:
    """True if the path stays inside the clip root.

    The studio receiver must check paths it is sent (Clip-Relative-Path
    header) so a bad value like "../../Windows/x.mp4" cannot escape.
    """
    path = PurePosixPath(relative_path)
    return (
        bool(relative_path)
        and not path.is_absolute()
        and "\\" not in relative_path
        and ".." not in path.parts
        and path.suffix == ".mp4"
    )


@dataclass(frozen=True)
class PlaylistEntry:
    relative_path: str
    prova: str
    car_number: int
    passagem: int
    team_name: str | None = None


def render_latest_playlist(
    entries_newest_first: list[PlaylistEntry],
    max_entries: int = DEFAULT_PLAYLIST_LENGTH,
) -> str:
    """Text of latest.m3u: extended M3U, newest clip first, relative paths.

    Each clip gets an "#EXTINF:-1,<title>" line (-1 = unknown duration)
    followed by its path, which is how OBS's VLC source reads playlists.
    """
    lines = ["#EXTM3U"]
    for entry in entries_newest_first[:max_entries]:
        team = f" {entry.team_name}" if entry.team_name else ""
        title = f"{entry.prova} - carro {entry.car_number}{team} - passagem {entry.passagem}"
        lines.append(f"#EXTINF:-1,{title}")
        lines.append(entry.relative_path)
    return "\n".join(lines) + "\n"
