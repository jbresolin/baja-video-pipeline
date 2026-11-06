"""Which team and school names a clip shows (LIVESTACK-42 item 6).

On the Settings page the admin picks, independently:

* "Nome da equipe nos vídeos": the team's full name ("BAJA UFMG") or its
  short name ("Baja UFMG");
* "Nome da escola nos vídeos": the school's full name ("Universidade
  Federal de Minas Gerais - UFMG") or its sigla ("UFMG").

Defaults are short and sigla (what clips showed before the choice existed).
A missing name falls back to the other one, so a team without a short name
still shows its full name. Every place that shows a clip's team or school
asks clip_names(): the render task (the overlay), the clip manifest and the
team lookup the review UI uses.
"""

import sqlite3
from dataclasses import dataclass

from orchestrator.repositories import app_settings as settings_repo
from shared.models import NameStyle


@dataclass(frozen=True)
class NameStyles:
    team: NameStyle = NameStyle.SHORT
    school: NameStyle = NameStyle.SHORT


@dataclass(frozen=True)
class ClipNames:
    team: str | None
    school: str | None


def name_styles(connection: sqlite3.Connection) -> NameStyles:
    """The two choices as stored by the Settings page (defaults before the first save)."""
    stored = settings_repo.load_settings_values(connection)
    return NameStyles(
        team=NameStyle(stored.get("clip_team_name_style", NameStyle.SHORT)),
        school=NameStyle(stored.get("clip_school_name_style", NameStyle.SHORT)),
    )


def _pick(style: NameStyle, full: str | None, short: str | None) -> str | None:
    """The chosen name, else the other one; blank counts as missing."""
    full = full.strip() if full and full.strip() else None
    short = short.strip() if short and short.strip() else None
    if style == NameStyle.FULL:
        return full or short
    return short or full


def clip_names(
    styles: NameStyles,
    *,
    team_full: str | None,
    team_short: str | None,
    school_full: str | None,
    school_short: str | None,
) -> ClipNames:
    return ClipNames(
        team=_pick(styles.team, team_full, team_short),
        school=_pick(styles.school, school_full, school_short),
    )


def clip_names_for_job(styles: NameStyles, job_row: sqlite3.Row) -> ClipNames:
    """From the names copied onto the job when its car number was set.

    overlay_team_name already is "short name, else full name", and jobs
    reviewed before LIVESTACK-42 have no school_name (only the sigla).
    """
    return clip_names(
        styles,
        team_full=job_row["team_name"],
        team_short=job_row["overlay_team_name"],
        school_full=job_row["school_name"],
        school_short=job_row["overlay_school_short"],
    )
