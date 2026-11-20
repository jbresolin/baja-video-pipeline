"""The value checks of the pass-results configuration, and the error they raise (LIVESTACK-47).

pass_results_config.py reads the document part by part; every value goes
through one of these, which refuse a wrong one with ConfigError: where
(`path`, e.g. ["provas", "MAN", "result", "decimals"]) and what (`problem`,
a code the review UI writes in pt-BR, with its `params`). Moved out of
pass_results_config.py when the speed block (LIVESTACK-67) made it too long.
"""

import math
from typing import Any

MAX_DECIMALS = 3
# The speed shown at most this long before the clip's end (LIVESTACK-68):
# a clip of a run is rarely longer, and a bigger N would just mean "the
# whole clip", which a typo is more likely to cause than a wish.
MAX_SECONDS_BEFORE_END = 30


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


def checked_object(value: Any, path: list[str], *, allowed: set[str], required: set[str]) -> dict:
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


def map_entries(value: Any, path: list[str]) -> list[tuple[str, Any]]:
    """The entries of a map such as "provas", "_comment" keys left out."""
    if not isinstance(value, dict):
        raise ConfigError(path, "wrong_type", "must be an object {…}", expected="object")
    return [(key, item) for key, item in value.items() if not key.startswith("_")]


def checked_text(value: Any, path: list[str], max_length: int, *, allow_empty: bool = False) -> str:
    """Text in quotes, stripped, not empty (unless allowed), at most `max_length` long."""
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


def checked_bool(value: Any, path: list[str]) -> bool:
    """true or false, nothing else."""
    if not isinstance(value, bool):
        raise ConfigError(path, "wrong_type", "must be true or false", expected="boolean")
    return value


def checked_decimals(value: Any, path: list[str]) -> int:
    """A whole number of decimals, 0 to MAX_DECIMALS."""
    # bool is an int in Python; `true` is not a number of decimals.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(path, "wrong_type", "must be a whole number", expected="whole_number")
    if not 0 <= value <= MAX_DECIMALS:
        raise ConfigError(
            path, "out_of_range", f"must be 0 to {MAX_DECIMALS}", minimum=0, maximum=MAX_DECIMALS
        )
    return value


def checked_seconds_before_end(value: Any, path: list[str]) -> float:
    """A number of seconds, more than 0 and at most MAX_SECONDS_BEFORE_END; decimals allowed.

    NaN and Infinity are refused as "not a number": Python's json reads
    them, but no clip has a moment there.
    """
    # bool is an int in Python; `true` is not a number of seconds.
    is_number = isinstance(value, int | float) and not isinstance(value, bool)
    if not is_number or not math.isfinite(value):
        raise ConfigError(path, "wrong_type", "must be a number", expected="number")
    if not 0 < value <= MAX_SECONDS_BEFORE_END:
        raise ConfigError(
            path,
            "more_than_and_at_most",
            f"must be more than 0 and at most {MAX_SECONDS_BEFORE_END}",
            more_than=0,
            maximum=MAX_SECONDS_BEFORE_END,
        )
    return float(value)
