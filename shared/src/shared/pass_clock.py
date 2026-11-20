"""The running clock's rule: what it shows at a moment of the video (LIVESTACK-48).

The clock starts at 0 on the largada (the start of the run, marked by the
reviewer in raw-video seconds), runs, and freezes on the official time of
the passagem (the results API's raw run time), staying there to the end
of the clip. Before the largada it is not shown. At every moment:

    shown = min(elapsed, official), rounded down to the configured decimals

written the Brazilian way: "0,0", "9,9", "10,0", "66,0" (decimal comma, no
leading zeros, exactly `decimals` digits after the comma).

Times are compared in whole microseconds, each rounded half up on its own
(raw time and largada), so the same moment gives the same text in Python,
in ffmpeg's expression (worker render, shared.ffmpeg_clock) and in the
browser's preview (review_ui/static/js/pass_clock.js). The three are held
together by one file of test vectors, tests/fixtures/pass_clock/vectors.json,
which the Python tests, the JavaScript test and the real-ffmpeg test read.

The clock counts in *units* of the last decimal shown: with 1 decimal,
66 s is 660 units. The official time is turned into units once, by the
orchestrator (official_units), so no float rounding happens later.

The result (LIVESTACK-53): with a clock, the result pill appears when the
clock freezes, never while it runs, and stays to the end of the clip. The
freeze is a whole number of microseconds after the largada (66,0 is
66 000 000 µs), so "is the result shown" is one comparison of whole
numbers, true exactly from the frame whose clock shows the official time.

The speed (LIVESTACK-67): "Aceleração e Velocidade" adds a second pill
under the clock, the run's top speed, from the frame where the reviewer
marked the car crossing the speed trap to the end of the clip. Its rule is
the largada's: shown when the raw time, in whole microseconds, is at or
after the mark's (speed_shown_at; the vectors' `speed_cases`).

The speed N seconds before the end (LIVESTACK-68): a prova may show the
speed for the last N seconds of the clip instead of from a mark. Where
that starts is worked out once (speed_start_before_end; the vectors'
`speed_before_end_cases`) and then used exactly like a mark, so the
worker needs no new rule.
"""

import math
from decimal import ROUND_FLOOR, Decimal

MICROSECONDS_PER_SECOND = 1_000_000
DECIMAL_COMMA = ","


def microseconds(seconds: float) -> int:
    """`seconds` in whole microseconds, half up (like JavaScript's Math.round)."""
    return math.floor(seconds * MICROSECONDS_PER_SECOND + 0.5)


def units_per_second(decimals: int) -> int:
    """1 for whole seconds, 10 for tenths, 100 for hundredths, ..."""
    return 10**decimals


def official_units(official_s: Decimal, decimals: int) -> int:
    """The official time in clock units, rounded down: 59.44 s with 1 decimal → 594."""
    step = Decimal(1).scaleb(-decimals)
    return int(official_s.quantize(step, rounding=ROUND_FLOOR).scaleb(decimals))


def elapsed_microseconds(raw_time_s: float, largada_s: float) -> int:
    """Microseconds since the largada (negative before it)."""
    return microseconds(raw_time_s) - microseconds(largada_s)


def clock_units(elapsed_us: int, official: int, decimals: int) -> int | None:
    """What the clock counts at `elapsed_us` after the largada; None before it."""
    if elapsed_us < 0:
        return None
    return min(elapsed_us * units_per_second(decimals) // MICROSECONDS_PER_SECOND, official)


def clock_text(units: int, decimals: int) -> str:
    """660 units with 1 decimal → "66,0"; 5 with 0 decimals → "5"."""
    whole, fraction = divmod(units, units_per_second(decimals))
    if decimals == 0:
        return str(whole)
    return f"{whole}{DECIMAL_COMMA}{fraction:0{decimals}d}"


def with_unit(text: str, unit: str | None) -> str:
    """The official time with its unit, "66,0 s", for sentences (the overlay draws its own).

    None is a clock recorded before the unit existed (LIVESTACK-53): its
    number meant seconds, so it reads "s"; "" is a clock without a unit.
    """
    unit = "s" if unit is None else unit
    return f"{text} {unit}" if unit else text


def clock_text_at(raw_time_s: float, largada_s: float, official: int, decimals: int) -> str | None:
    """The clock's text at a moment of the raw video, or None when it is not shown."""
    units = clock_units(elapsed_microseconds(raw_time_s, largada_s), official, decimals)
    return None if units is None else clock_text(units, decimals)


def freeze_elapsed_microseconds(official: int, decimals: int) -> int:
    """Microseconds after the largada at which the clock reaches the official time.

    Exact: a unit is 10^(6 - decimals) µs, a whole number for 0 to 3 decimals.
    """
    return official * (MICROSECONDS_PER_SECOND // units_per_second(decimals))


def result_shown(elapsed_us: int, official: int, decimals: int) -> bool:
    """With a clock, the result is shown from the freeze on (LIVESTACK-53).

    The same moment as clock_units() reaching `official`: elapsed · units/s
    ≥ official · 10⁶ is elapsed ≥ official · 10⁶ / units/s, a whole number.
    """
    return elapsed_us >= freeze_elapsed_microseconds(official, decimals)


def result_shown_at(raw_time_s: float, largada_s: float, official: int, decimals: int) -> bool:
    """Whether the result pill is shown at a moment of the raw video, when the clip has a clock."""
    return result_shown(elapsed_microseconds(raw_time_s, largada_s), official, decimals)


def speed_shown_at(raw_time_s: float, speed_mark_s: float) -> bool:
    """Whether the speed pill is shown at a moment of the raw video (LIVESTACK-67).

    From the speed-trap mark on, to the end of the clip; the same whole
    microsecond comparison as the largada's, so the frame the reviewer
    marked (the page writes the mark rounded down to the millisecond)
    shows the speed and the frame before it does not.
    """
    return elapsed_microseconds(raw_time_s, speed_mark_s) >= 0


MICROSECONDS_PER_MILLISECOND = 1_000


def speed_start_before_end(
    *,
    cut_in_s: float | None,
    cut_out_s: float | None,
    duration_s: float | None,
    seconds_before_end: float,
) -> float | None:
    """Where the speed pill starts when it is shown N s before the clip's end (LIVESTACK-68).

    The clip ends at the cut-out, or at the end of the video without one;
    the start is that end minus N, rounded down to the millisecond like a
    mark, and never before the cut-in (a clip shorter than N shows the
    speed throughout). Counted in whole microseconds, so 5.1 − 3 is 2.1 and
    not 2.099 (the vectors' `speed_before_end_cases`; js/pass_clock.js has
    the same rule). None when no end is known: then there is no speed.
    """
    clip_end_s = cut_out_s if cut_out_s is not None else duration_s
    if clip_end_s is None:
        return None
    start_us = microseconds(clip_end_s) - microseconds(seconds_before_end)
    # Python's // rounds towards minus infinity, so this is "rounded down"
    # even for a start before 0 (a clip shorter than N), which the cut-in
    # then replaces anyway.
    start_us = (start_us // MICROSECONDS_PER_MILLISECOND) * MICROSECONDS_PER_MILLISECOND
    cut_in_us = microseconds(cut_in_s or 0.0)
    return max(start_us, cut_in_us) / MICROSECONDS_PER_SECOND


def clip_ends_before_freeze(
    cut_out_s: float, largada_s: float, official: int, decimals: int
) -> bool:
    """True when the clip ends before the clock stops, so its result never appears.

    The clip has frames before its cut-out only, so a freeze exactly at the
    cut-out is already too late.
    """
    return not result_shown(elapsed_microseconds(cut_out_s, largada_s) - 1, official, decimals)
