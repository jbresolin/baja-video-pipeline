"""Reading one run of one car out of a results table (LIVESTACK-47).

`resultado.php?evt=26BR&id=26BR_MAN&display=1` answers (docs/results-api.md
of baja-sae-brasil-online; tests/fixtures/results_api/ has a real one):

    {"columns": [{"header": "Tempo", "kind": "list"}, ...],
     "rows": [{"team": {"number": 26, ...},
               "cells": [..., {"raw": [59, "66 (4 )"],
                               "display": "59<br /> 66 (4<img class='cone'>)"}, ...]}]}

Cells are in the same order as the columns. A prova with several runs
gives each car one row whose `list` cells hold one value per run, in run
order (runs mode "list_columns"); the API also allows one row per run
(mode "rows": run n is the car's n-th row in the answer's order).

`raw` is a number only when the cell is exactly one; "66 (4 )" is text
because the penalty icons were removed from it. The penalties are only in
`display`: a count followed by an icon, `8<img class='cone'>/2<img
class='gate'>`. Dashes ("-", "---"), empty, "SPOILERS" and "?" mean no
result for that run.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from shared.models import PassResultProblem

RUNS_LIST_COLUMNS = "list_columns"
RUNS_ROWS = "rows"

# Texts that mean "nothing to show for this run": a run not done, an empty
# cell, or a value hidden by spoilers. Compared in capitals. Dashes of any
# kind and number ("-", "---", "—") also mean a run not done.
NO_RESULT_TEXTS = frozenset({"", "SPOILERS", "?"})
DASHES = re.compile(r"^[-–—]+$")
# "66", "66,5", "66.5", optionally followed by the penalties' leftovers
# "(4 )" or "(8 /2 )" (raw values lose the icons, not the counts).
NUMBER_TEXT = re.compile(r"^(-?\d+(?:[.,]\d+)?)\s*(?:\([^()]*\))?$")
LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Struck-through text is not part of the value (results-rules.md, as for raw).
STRUCK = re.compile(r"<(s|del|strike)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
# A penalty: its count, then its icon.
PENALTY = re.compile(r"(\d+)\s*(<img\b[^>]*>)", re.IGNORECASE)
ICON_CLASS = re.compile(r"\bclass\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)


class BadTableError(ValueError):
    """The answer does not look like a results table at all."""


@dataclass(frozen=True)
class RunCell:
    """One run's value in one column: its raw value and its display HTML (if sent)."""

    raw: Any
    display: str | None


@dataclass(frozen=True)
class Lookup:
    """A run cell, or why there is none (`problem`, with an English `detail`)."""

    cell: RunCell | None = None
    problem: PassResultProblem | None = None
    detail: str | None = None


@dataclass(frozen=True)
class RunNumber:
    """A run's value as a number, or why it has none."""

    value: Decimal | None = None
    problem: PassResultProblem | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ResultsTable:
    columns: list[str | None]
    rows: list[dict]

    def column_index(self, header: str) -> int | None:
        """The first column with this header (two with the same header share a value)."""
        for index, name in enumerate(self.columns):
            if name == header:
                return index
        return None

    def rows_of_car(self, car_number: int) -> list[dict]:
        """The car's rows in the answer's order."""
        return [row for row in self.rows if _car_number(row) == car_number]


def parse_table(body: Any) -> ResultsTable:
    """The answer of resultado.php as a ResultsTable; BadTableError if it is not one."""
    if not isinstance(body, dict):
        raise BadTableError("the answer is not a JSON object")
    columns, rows = body.get("columns"), body.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise BadTableError("the answer has no 'columns' and 'rows' lists")
    headers = [column.get("header") if isinstance(column, dict) else None for column in columns]
    return ResultsTable(columns=headers, rows=[row for row in rows if isinstance(row, dict)])


def _car_number(row: dict) -> int | None:
    team = row.get("team")
    number = team.get("number") if isinstance(team, dict) else None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def _cell(row: dict, index: int) -> dict:
    cells = row.get("cells")
    if not isinstance(cells, list) or index >= len(cells) or not isinstance(cells[index], dict):
        return {}
    return cells[index]


def display_lines(display: Any) -> list[str]:
    """A cell's display HTML split into its lines (one per run in a list column)."""
    if display is None:
        return []
    return [line.strip() for line in LINE_BREAK.split(str(display))]


def find_run(
    table: ResultsTable, runs_mode: str, column: str, car_number: int, passagem: int
) -> Lookup:
    """The cell of `column` for the car's run number `passagem` (counted from 1)."""
    index = table.column_index(column)
    if index is None:
        return Lookup(problem=PassResultProblem.COLUMN_MISSING, detail=f"no column '{column}'")
    rows = table.rows_of_car(car_number)
    if not rows:
        return Lookup(problem=PassResultProblem.CAR_NOT_IN_TABLE, detail=f"car {car_number}")
    if runs_mode == RUNS_ROWS:
        if passagem > len(rows):
            return _no_such_run(len(rows))
        cell = _cell(rows[passagem - 1], index)
        display = cell.get("display")
        return Lookup(cell=RunCell(cell.get("raw"), None if display is None else str(display)))
    cell = _cell(rows[0], index)
    raw = cell.get("raw")
    runs = raw if isinstance(raw, list) else [raw]
    if passagem > len(runs):
        return _no_such_run(len(runs))
    lines = display_lines(cell.get("display"))
    # Lines and runs must pair up one to one; otherwise the penalties of
    # one run could be shown with another's time, so none are used.
    display = lines[passagem - 1] if len(lines) == len(runs) else None
    return Lookup(cell=RunCell(runs[passagem - 1], display))


def _no_such_run(found: int) -> Lookup:
    return Lookup(problem=PassResultProblem.NO_SUCH_RUN, detail=f"{found} run(s) in the table")


def run_number(raw: Any) -> RunNumber:
    """A run's raw value as a number: 59, "66 (4 )" → 66, "66,5" → 66.5; else why not."""
    if raw is None:
        return RunNumber(problem=PassResultProblem.NO_RESULT_YET, detail="empty")
    if isinstance(raw, bool | list | dict):
        return RunNumber(problem=PassResultProblem.NOT_A_NUMBER, detail=repr(raw))
    if isinstance(raw, int | float):
        return RunNumber(value=Decimal(str(raw)))
    text = " ".join(str(raw).split())
    if text.upper() in NO_RESULT_TEXTS or DASHES.match(text):
        return RunNumber(problem=PassResultProblem.NO_RESULT_YET, detail=repr(text))
    match = NUMBER_TEXT.match(text)
    if match is None:
        return RunNumber(problem=PassResultProblem.NOT_A_NUMBER, detail=repr(text))
    return RunNumber(value=Decimal(match.group(1).replace(",", ".")))


def penalties_in(display_line: str | None) -> list[tuple[str, int]]:
    """The penalties of one run's display line, in order: [("cone", 8), ("gate", 2)].

    The kind is the icon's class (its first word); a kind seen twice is
    added up. Zero counts are left out.
    """
    if not display_line:
        return []
    totals: dict[str, int] = {}
    for count, icon in PENALTY.findall(STRUCK.sub("", display_line)):
        found = ICON_CLASS.search(icon)
        kind = found.group(2).split()[0] if found and found.group(2).split() else "?"
        totals[kind] = totals.get(kind, 0) + int(count)
    return [(kind, count) for kind, count in totals.items() if count > 0]
