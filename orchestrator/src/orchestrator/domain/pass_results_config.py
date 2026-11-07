"""The pass-results configuration: which provas show a result line, and how (LIVESTACK-47).

One JSON document, edited as text on the admin Settings page and stored
as typed (app_settings key "pass_results_config"). It is checked here on
every save; a bad one is refused with the path of the bad key and the old
one stays in force. Shape (docs/design.md §31 has a commented example):

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
                     "penalties": true, "corrected_column": "Tempo Corrigido"},
          "clock": {"column": "Tempo", "decimals": 1}   # read by LIVESTACK-48
        }
      }
    }

Keys starting with "_" are ignored anywhere, so a "_comment" can say why
something is set (JSON has no comments). Any other unknown key is an
error: a typo such as "show_by_defalt" would otherwise be silently ignored.
An empty text means "nothing configured".
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

PROVA_ID = re.compile(r"^[A-Za-z0-9]{1,3}$")
# Icon class names as the results API writes them (<img class='cone'>).
PENALTY_CLASS = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_DECIMALS = 3
MAX_UNIT_LENGTH = 12
MAX_NAME_LENGTH = 60
MAX_COLUMN_LENGTH = 100
RUNS_MODES = ("list_columns", "rows")


class ConfigError(ValueError):
    """Why a configuration was refused: where (`path`) and what (`problem`, `params`).

    `problem` is a code the review UI turns into pt-BR; the message is English.
    """

    def __init__(self, path: list[str], problem: str, message: str, **params: Any) -> None:
        super().__init__(f"{'.'.join(path) or '(top level)'}: {message}")
        self.path = path
        self.problem = problem
        self.params = params

    def details(self) -> dict[str, Any]:
        return {"path": ".".join(self.path), "problem": self.problem, **self.params}


@dataclass(frozen=True)
class PenaltyNames:
    one: str
    other: str

    def for_count(self, count: int) -> str:
        return self.one if count == 1 else self.other


@dataclass(frozen=True)
class ResultSpec:
    """What the result line shows: a column's value, its penalties, the corrected value."""

    column: str
    unit: str = ""
    decimals: int = 0
    penalties: bool = False
    corrected_column: str | None = None


@dataclass(frozen=True)
class ClockSpec:
    """The running clock's official time (LIVESTACK-48 reads it; validated here)."""

    column: str
    decimals: int = 0


@dataclass(frozen=True)
class ProvaPassConfig:
    prova_id: str
    resultado: str
    runs: str = "list_columns"
    show_by_default: bool = True
    result: ResultSpec | None = None
    clock: ClockSpec | None = None


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
    top = _object(document, [], allowed={"penalties", "provas"}, required={"provas"})
    penalties = {
        name: _penalty_names(value, ["penalties", name])
        for name, value in _entries(top.get("penalties", {}), ["penalties"])
    }
    for name in penalties:
        if not PENALTY_CLASS.match(name):
            raise ConfigError(["penalties", name], "bad_name", "not an icon class name")
    provas = {}
    for prova_id, value in _entries(top["provas"], ["provas"]):
        if not PROVA_ID.match(prova_id):
            raise ConfigError(["provas", prova_id], "bad_id", "a prova id is 1 to 3 letters/digits")
        provas[prova_id] = _prova(prova_id, value, ["provas", prova_id])
    return PassResultsConfig(penalties=penalties, provas=provas)


# --- One part each ---


def _penalty_names(value: Any, path: list[str]) -> PenaltyNames:
    entry = _object(value, path, allowed={"one", "other"}, required={"one", "other"})
    return PenaltyNames(
        one=_text(entry["one"], [*path, "one"], MAX_NAME_LENGTH),
        other=_text(entry["other"], [*path, "other"], MAX_NAME_LENGTH),
    )


def _prova(prova_id: str, value: Any, path: list[str]) -> ProvaPassConfig:
    entry = _object(
        value,
        path,
        allowed={"resultado", "runs", "show_by_default", "result", "clock"},
        required=set(),
    )
    resultado = _text(entry.get("resultado", prova_id), [*path, "resultado"], 3)
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
    return ProvaPassConfig(
        prova_id=prova_id,
        resultado=resultado,
        runs=runs,
        show_by_default=_bool(entry.get("show_by_default", True), [*path, "show_by_default"]),
        result=result,
        clock=clock,
    )


def _result(value: Any, path: list[str]) -> ResultSpec:
    entry = _object(
        value,
        path,
        allowed={"column", "unit", "decimals", "penalties", "corrected_column"},
        required={"column"},
    )
    corrected = entry.get("corrected_column")
    return ResultSpec(
        column=_text(entry["column"], [*path, "column"], MAX_COLUMN_LENGTH),
        unit=_text(entry.get("unit", ""), [*path, "unit"], MAX_UNIT_LENGTH, allow_empty=True),
        decimals=_decimals(entry.get("decimals", 0), [*path, "decimals"]),
        penalties=_bool(entry.get("penalties", False), [*path, "penalties"]),
        corrected_column=(
            None
            if corrected is None
            else _text(corrected, [*path, "corrected_column"], MAX_COLUMN_LENGTH)
        ),
    )


def _clock(value: Any, path: list[str]) -> ClockSpec:
    entry = _object(value, path, allowed={"column", "decimals"}, required={"column"})
    return ClockSpec(
        column=_text(entry["column"], [*path, "column"], MAX_COLUMN_LENGTH),
        decimals=_decimals(entry.get("decimals", 0), [*path, "decimals"]),
    )


# --- Value checks ---


def _object(value: Any, path: list[str], *, allowed: set[str], required: set[str]) -> dict:
    """An object with only `allowed` keys (plus "_comments"), and every `required` one."""
    if not isinstance(value, dict):
        raise ConfigError(path, "wrong_type", "must be an object {…}", expected="object")
    for key in value:
        if key not in allowed and not key.startswith("_"):
            raise ConfigError([*path, key], "unknown_key", "unknown key", allowed=sorted(allowed))
    for key in sorted(required):
        if key not in value:
            raise ConfigError([*path, key], "missing_key", "this key is required")
    return value


def _entries(value: Any, path: list[str]) -> list[tuple[str, Any]]:
    """The entries of a map such as "provas", "_comment" keys left out."""
    if not isinstance(value, dict):
        raise ConfigError(path, "wrong_type", "must be an object {…}", expected="object")
    return [(key, item) for key, item in value.items() if not key.startswith("_")]


def _text(value: Any, path: list[str], max_length: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(path, "wrong_type", "must be text in quotes", expected="text")
    value = value.strip()
    if not value and not allow_empty:
        raise ConfigError(path, "empty_text", "must not be empty")
    if len(value) > max_length:
        raise ConfigError(
            path, "too_long", f"at most {max_length} characters", max_length=max_length
        )
    return value


def _bool(value: Any, path: list[str]) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(path, "wrong_type", "must be true or false", expected="boolean")
    return value


def _decimals(value: Any, path: list[str]) -> int:
    # bool is an int in Python; `true` is not a number of decimals.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(path, "wrong_type", "must be a whole number", expected="whole_number")
    if not 0 <= value <= MAX_DECIMALS:
        raise ConfigError(
            path, "out_of_range", f"must be 0 to {MAX_DECIMALS}", minimum=0, maximum=MAX_DECIMALS
        )
    return value
