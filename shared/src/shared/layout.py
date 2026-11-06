"""Folder layout and file names of clips and raws (design §7, LIVESTACK-41 item 12).

    <clip root>/
        latest.m3u                       newest clips first, for OBS
        <prova>/<car>/<prova>-<car 3 digits>-p<passagem>.mp4
        e.g. enduro/42/enduro-042-p3.mp4, after a reprocess enduro-042-p3-v2.mp4

`prova` is turned into a "slug" (lowercase ASCII, dashes) so the path is
the same on Windows and Linux whatever accents the reviewer typed:
"Suspensão" → "suspensao", "Enduro de Resistência" → "enduro-de-resistencia".

A file must say what it is once it is copied out of its folder (decision
by João, 2026-11-05). So clips are named after prova, car and passagem
(clip_file_stem), and raws on the workers after the sender and the time
the bot received them (raw_file_name). Each format lives in exactly one
function, so changing it is a one-line change. Files already on disk are
never renamed.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

LATEST_PLAYLIST_NAME = "latest.m3u"
DEFAULT_PLAYLIST_LENGTH = 50
# Raw names use the event's local time (volunteers think in it).
RAW_NAME_TIME_ZONE = ZoneInfo("America/Sao_Paulo")
# How many characters of the job id end a raw's name: unique enough among
# an event's few hundred videos, and short enough to read; the review UI
# shows the same prefix.
RAW_NAME_JOB_ID_CHARACTERS = 8
# A sender with neither a name nor a phone number (never expected).
UNKNOWN_SENDER_SLUG = "remetente"
# Long WhatsApp names are cut (Windows paths have a length limit).
MAX_SENDER_SLUG_CHARACTERS = 40


def slugify(text: str) -> str:
    """Lowercase ASCII words joined by dashes; "" when nothing usable is left.

    NFKD splits "ã" into "a" + a combining tilde; encoding to ASCII with
    "ignore" then drops the tilde (and emoji) and keeps the "a".
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def slugify_prova(prova: str) -> str:
    """Make a folder-safe name out of a prova name."""
    slug = slugify(prova)
    if not slug:
        raise ValueError(f"prova {prova!r} has no letters or digits to build a folder name from")
    return slug


def clip_file_stem(prova_slug: str, car_number: int, passagem: int) -> str:
    """THE clip name format (item 12): "enduro-042-p3".

    The car is zero-padded to 3 digits so files sort by car. To change the
    format, change this line; everything else builds on it.
    """
    return f"{prova_slug}-{car_number:03d}-p{passagem}"


def clip_relative_path(prova: str, car_number: int, passagem: int) -> str:
    """The clip's path relative to the clip root, always with forward slashes.

    Example: clip_relative_path("Enduro", 42, 3) == "enduro/42/enduro-042-p3.mp4".
    """
    return clip_relative_path_in_folder(slugify_prova(prova), car_number, passagem)


def clip_relative_path_in_folder(prova_slug: str, car_number: int, passagem: int) -> str:
    """Same, from a prova folder name that is already a slug.

    The orchestrator stores each prova's slug when the prova is created and
    keeps it across renames (orchestrator/domain/provas_catalog.py), so it
    builds paths from that stored slug instead of the current name.
    """
    stem = clip_file_stem(prova_slug, car_number, passagem)
    return str(PurePosixPath(prova_slug, str(car_number), f"{stem}.mp4"))


def versioned_clip_relative_path(clip_path: str, version: int) -> str:
    """The name of a later version of a clip, beside the first one.

    Version 1 keeps the path; later versions (after a reprocess) get
    "-v<n>" before the extension, so no version replaces another:
        versioned_clip_relative_path("enduro/42/enduro-042-p3.mp4", 2)
            == "enduro/42/enduro-042-p3-v2.mp4"
    Used by the master's archive (every version) and by the studio when the
    file on air is locked and a reprocessed clip must go beside it.
    """
    if version < 1:
        raise ValueError("clip versions start at 1")
    if version == 1:
        return clip_path
    path = PurePosixPath(clip_path)
    if path.stem.isdigit():
        # A clip named before item 12 ("enduro/42/3.mp4"): its versions were
        # archived as "3.v2.mp4", and files on disk are never renamed.
        return str(path.with_name(f"{path.stem}.v{version}{path.suffix}"))
    return str(path.with_name(f"{path.stem}-v{version}{path.suffix}"))


def archive_relative_path(clip_path: str, version: int) -> str:
    """Where the master archives one version of a clip (design §11 as amended)."""
    return versioned_clip_relative_path(clip_path, version)


def raw_file_name(
    sender_name: str | None,
    sender_phone: str | None,
    received_at: datetime,
    job_id: str,
    extension: str,
) -> str:
    """THE raw name format on workers (item 12), e.g.
    "joao-guilherme-bresolin-20261104-232909-2f2510a3.mp4".

    Sender = the contact's display name, else the phone number; the time
    is when the bot received the video, in São Paulo time; the start of
    the job id keeps names unique and traceable to the review UI.
    `extension` includes the dot (".mp4"). The VPS inbox keeps
    "<job_id>.<ext>": this name is only for files on workers' disks and
    for downloads.
    """
    sender = slugify(sender_name or "")[:MAX_SENDER_SLUG_CHARACTERS].rstrip("-")
    sender = sender or re.sub(r"\D", "", sender_phone or "")
    local_time = received_at.astimezone(RAW_NAME_TIME_ZONE).strftime("%Y%m%d-%H%M%S")
    short_id = job_id[:RAW_NAME_JOB_ID_CHARACTERS]
    return f"{sender or UNKNOWN_SENDER_SLUG}-{local_time}-{short_id}{extension.lower()}"


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
