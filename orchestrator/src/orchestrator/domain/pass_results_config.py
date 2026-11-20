"""The pass-results configuration: which provas show a result line, and how (LIVESTACK-47).

One JSON document, edited as text on the admin Settings page and stored
as typed (app_settings key "pass_results_config"). It is checked here on
every save; a bad one is refused with the path of the bad key and the old
one stays in force. Shape (docs/design.md §32 has a commented example):

    {
      "penalties": {                      # penalty icon class → its names
        "cone": {"one": "cone", "other": "cones"},
        "gate": {"one": "saída de pista", "other": "saídas de pista"}
      },
      "provas": {
        "MAN": {                          # the results API's prova id
          "resultado": "MAN",             # → resultado "<event>_MAN"; default: the key
          "runs": "list_columns",         # or "rows" (one row per run)
          "show_by_default": true,        # the job page's toggle, until a reviewer sets it
          "result": {"column": "Tempo", "unit": "s", "decimals": 0,
                     "penalties": true, "corrected_column": "Tempo Corrigido",
                     "extra": [{"column": "Obstáculos",
                                "unit": {"one": "obstáculo", "other": "obstáculos"}}]},
          "clock": {"column": "Tempo", "decimals": 1,   # read by LIVESTACK-48
                    "unit": "s"}          # after the digits, default "s" (LIVESTACK-53)
        },
        "AVF": {                          # LIVESTACK-67: a clock plus the top speed
          "resultado": "ACE",
          "clock": {"column": "Aceleração", "decimals": 2, "unit": "s"},
          "speed": {"resultado": "VEL",   # another table of the same prova (required)
                    "column": "Velocidade",
                    "unit": "km/h",       # default "" (no unit)
                    "decimals": 1}        # 0–3, default 0
        }
      }
    }

A `speed` block needs a `clock` block: the speed is drawn under the clock
(docs/design.md §40). Run n of the speed column pairs with run n of the
clock column, read the prova's `runs` way.

Keys starting with "_" are ignored anywhere, so a "_comment" can say why
something is set (JSON has no comments). Any other unknown key is an
error: a typo such as "show_by_defalt" would otherwise be silently ignored.
An empty text means "nothing configured".
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from orchestrator.domain.pass_results_config_values import (
    ConfigError,
    checked_bool,
    checked_decimals,
    checked_object,
    checked_text,
    map_entries,
)

PROVA_ID = re.compile(r"^[A-Za-z0-9]{1,3}$")
# Icon class names as the results API writes them (<img class='cone'>).
PENALTY_CLASS = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_UNIT_LENGTH = 12
MAX_NAME_LENGTH = 60
MAX_COLUMN_LENGTH = 100
# More than this would not fit the pill anyway.
MAX_EXTRAS = 4
RUNS_MODES = ("list_columns", "rows")


@dataclass(frozen=True)
class PenaltyNames:
    one: str
    other: str

    def for_count(self, count: int) -> str:
        return self.one if count == 1 else self.other


@dataclass(frozen=True)
class ExtraSpec:
    """One more column's value at the end of the line: "· 8 obstáculos".

    `unit` is fixed text, or names by count like a penalty's
    ({"one": "obstáculo", "other": "obstáculos"}: "1 obstáculo", "8 obstáculos").
    """

    column: str
    unit: str | PenaltyNames = ""
    decimals: int = 0


@dataclass(frozen=True)
class ResultSpec:
    """What the result line shows: a column's value, its penalties, the corrected value,
    then the `extra` columns' values."""

    column: str
    unit: str = ""
    decimals: int = 0
    penalties: bool = False
    corrected_column: str | None = None
    extra: tuple[ExtraSpec, ...] = ()


@dataclass(frozen=True)
class ClockSpec:
    """The running clock's official time (LIVESTACK-48 reads it; validated here)."""

    column: str
    decimals: int = 0
    # Written after the digits, "66,0 s" (LIVESTACK-53); "" = none.
    unit: str = "s"


@dataclass(frozen=True)
class SpeedSpec:
    """The top speed under the clock (LIVESTACK-67), from a second table of the prova.

    `resultado` names that table ("VEL" → "26BR_VEL"); run n of `column`
    there pairs with run n of the clock's column.
    """

    resultado: str
    column: str
    unit: str = ""
    decimals: int = 0


@dataclass(frozen=True)
class ProvaPassConfig:
    prova_id: str
    resultado: str
    runs: str = "list_columns"
    show_by_default: bool = True
    result: ResultSpec | None = None
    clock: ClockSpec | None = None
    speed: SpeedSpec | None = None


@dataclass(frozen=True)
class PassResultsConfig:
    penalties: dict[str, PenaltyNames] = field(default_factory=dict)
    provas: dict[str, ProvaPassConfig] = field(default_factory=dict)

    def for_prova(self, prova_id: str | None) -> ProvaPassConfig | None:
        return self.provas.get(prova_id) if prova_id else None


EMPTY_CONFIG = PassResultsConfig()


def parse_config(text: str) -> PassResultsConfig:
    """The configuration in `text`, or ConfigError. Blank text = nothing configured."""
    if not text.strip():
        return EMPTY_CONFIG
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(
            [], "not_json", f"not JSON: {error.msg}", line=error.lineno, column=error.colno
        ) from error
    top = checked_object(document, [], allowed={"penalties", "provas"}, required={"provas"})
    penalties = {
        name: _penalty_names(value, ["penalties", name])
        for name, value in map_entries(top.get("penalties", {}), ["penalties"])
    }
    for name in penalties:
        if not PENALTY_CLASS.match(name):
            raise ConfigError(["penalties", name], "bad_name", "not an icon class name")
    provas = {}
    for prova_id, value in map_entries(top["provas"], ["provas"]):
        if not PROVA_ID.match(prova_id):
            raise ConfigError(["provas", prova_id], "bad_id", "a prova id is 1 to 3 letters/digits")
        provas[prova_id] = _prova(prova_id, value, ["provas", prova_id])
    return PassResultsConfig(penalties=penalties, provas=provas)


# --- One part each ---


def _penalty_names(value: Any, path: list[str]) -> PenaltyNames:
    entry = checked_object(value, path, allowed={"one", "other"}, required={"one", "other"})
    return PenaltyNames(
        one=checked_text(entry["one"], [*path, "one"], MAX_NAME_LENGTH),
        other=checked_text(entry["other"], [*path, "other"], MAX_NAME_LENGTH),
    )


def _prova(prova_id: str, value: Any, path: list[str]) -> ProvaPassConfig:
    entry = checked_object(
        value,
        path,
        allowed={"resultado", "runs", "show_by_default", "result", "clock", "speed"},
        required=set(),
    )
    resultado = checked_text(entry.get("resultado", prova_id), [*path, "resultado"], 3)
    if not PROVA_ID.match(resultado):
        raise ConfigError([*path, "resultado"], "bad_id", "a resultado id is 1 to 3 letters/digits")
    runs = entry.get("runs", "list_columns")
    if runs not in RUNS_MODES:
        raise ConfigError(
            [*path, "runs"], "not_one_of", f"must be one of {RUNS_MODES}", choices=list(RUNS_MODES)
        )
    result = _result(entry["result"], [*path, "result"]) if "result" in entry else None
    clock = _clock(entry["clock"], [*path, "clock"]) if "clock" in entry else None
    if result is None and clock is None:
        raise ConfigError(path, "nothing_to_show", "needs a 'result' or a 'clock' block")
    speed = _speed(entry["speed"], [*path, "speed"]) if "speed" in entry else None
    if speed is not None and clock is None:
        # The speed pill sits under the clock and is drawn only with it.
        raise ConfigError([*path, "speed"], "speed_needs_clock", "needs a 'clock' block too")
    return ProvaPassConfig(
        prova_id=prova_id,
        resultado=resultado,
        runs=runs,
        show_by_default=checked_bool(
            entry.get("show_by_default", True), [*path, "show_by_default"]
        ),
        result=result,
        clock=clock,
        speed=speed,
    )


def _result(value: Any, path: list[str]) -> ResultSpec:
    entry = checked_object(
        value,
        path,
        allowed={"column", "unit", "decimals", "penalties", "corrected_column", "extra"},
        required={"column"},
    )
    corrected = entry.get("corrected_column")
    return ResultSpec(
        column=checked_text(entry["column"], [*path, "column"], MAX_COLUMN_LENGTH),
        unit=checked_text(
            entry.get("unit", ""), [*path, "unit"], MAX_UNIT_LENGTH, allow_empty=True
        ),
        decimals=checked_decimals(entry.get("decimals", 0), [*path, "decimals"]),
        penalties=checked_bool(entry.get("penalties", False), [*path, "penalties"]),
        corrected_column=(
            None
            if corrected is None
            else checked_text(corrected, [*path, "corrected_column"], MAX_COLUMN_LENGTH)
        ),
        extra=_extras(entry.get("extra", []), [*path, "extra"]),
    )


def _extras(value: Any, path: list[str]) -> tuple[ExtraSpec, ...]:
    if not isinstance(value, list):
        raise ConfigError(path, "wrong_type", "must be a list […]", expected="list")
    if len(value) > MAX_EXTRAS:
        raise ConfigError(path, "too_many", f"at most {MAX_EXTRAS} items", maximum=MAX_EXTRAS)
    return tuple(
        _extra(item, [*path[:-1], f"{path[-1]}[{index}]"]) for index, item in enumerate(value)
    )


def _extra(value: Any, path: list[str]) -> ExtraSpec:
    entry = checked_object(value, path, allowed={"column", "unit", "decimals"}, required={"column"})
    unit = entry.get("unit", "")
    return ExtraSpec(
        column=checked_text(entry["column"], [*path, "column"], MAX_COLUMN_LENGTH),
        # Text, or names by count like a penalty's: {"one": …, "other": …}.
        unit=(
            _penalty_names(unit, [*path, "unit"])
            if isinstance(unit, dict)
            else checked_text(unit, [*path, "unit"], MAX_UNIT_LENGTH, allow_empty=True)
        ),
        decimals=checked_decimals(entry.get("decimals", 0), [*path, "decimals"]),
    )


def _clock(value: Any, path: list[str]) -> ClockSpec:
    entry = checked_object(value, path, allowed={"column", "decimals", "unit"}, required={"column"})
    return ClockSpec(
        column=checked_text(entry["column"], [*path, "column"], MAX_COLUMN_LENGTH),
        decimals=checked_decimals(entry.get("decimals", 0), [*path, "decimals"]),
        unit=checked_text(
            entry.get("unit", "s"), [*path, "unit"], MAX_UNIT_LENGTH, allow_empty=True
        ),
    )


def _speed(value: Any, path: list[str]) -> SpeedSpec:
    entry = checked_object(
        value,
        path,
        allowed={"resultado", "column", "unit", "decimals"},
        required={"resultado", "column"},
    )
    resultado = checked_text(entry["resultado"], [*path, "resultado"], 3)
    if not PROVA_ID.match(resultado):
        raise ConfigError([*path, "resultado"], "bad_id", "a resultado id is 1 to 3 letters/digits")
    return SpeedSpec(
        resultado=resultado,
        column=checked_text(entry["column"], [*path, "column"], MAX_COLUMN_LENGTH),
        unit=checked_text(
            entry.get("unit", ""), [*path, "unit"], MAX_UNIT_LENGTH, allow_empty=True
        ),
        decimals=checked_decimals(entry.get("decimals", 0), [*path, "decimals"]),
    )
