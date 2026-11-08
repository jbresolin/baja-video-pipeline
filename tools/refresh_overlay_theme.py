"""Copy BajaOvrLay's on-air look into the worker (the clip overlay's theme).

    uv run python tools/refresh_overlay_theme.py <bajaovrlay checkout> <commit>

The worker draws the clip overlay with Pillow, but it must look like
BajaOvrLay's lower third ("Tarja", theme Ardósia). Its look lives in that
repo, so this script copies the inputs into worker/src/worker/overlay_theme/:

* tokens.css, verbatim: every colour, size and radius the overlay uses is
  parsed from it at run time (worker/overlay_tokens.py), never retyped;
* Bebas Neue, converted from the repo's woff2 to a TTF (Pillow on Windows
  is not guaranteed to read woff2), with its OFL LICENSE beside it;
* ORIGIN.md, recording the repo, the commit, each source path and the
  SHA-256 of every source and vendored file.

The review UI draws a preview of the same overlay in the browser
(LIVESTACK-48), so tokens.css, the TTF and its LICENSE are also copied,
unchanged, into review_ui/src/review_ui/overlay_theme/ (the review UI
image has no worker code); a test keeps the two copies equal.

Refreshing the look = run this on a newer BajaOvrLay commit, rerun the tests
(the golden image will tell you what changed), commit. The checkout is only
read; nothing in it is executed. Needs the dev dependency fonttools[woff].
"""

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
THEME_FOLDER = REPO_ROOT / "worker" / "src" / "worker" / "overlay_theme"
PREVIEW_FOLDER = REPO_ROOT / "review_ui" / "src" / "review_ui" / "overlay_theme"
SOURCE_REPO = "jbresolin/bajaovrlay"

TOKENS_SOURCE = "packages/theme/ardosia/tokens.css"
FONT_SOURCE = "packages/theme/fonts/bebas-neue/bebas-neue-latin-400-normal.woff2"
LICENSE_SOURCE = "packages/theme/fonts/bebas-neue/LICENSE"

TOKENS_TARGET = "tokens.css"
FONT_TARGET = "BebasNeue-Regular.ttf"
LICENSE_TARGET = "BebasNeue-LICENSE.txt"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def convert_woff2_to_ttf(source: Path, target: Path) -> None:
    from fontTools.ttLib import TTFont  # dev dependency, only needed here

    # recalcTimestamp=False keeps the head table's date, so the TTF is the same
    # bytes every time it is converted (its SHA-256 in ORIGIN.md stays stable).
    font = TTFont(source, recalcTimestamp=False)
    font.flavor = None  # plain sfnt (TrueType outlines) instead of WOFF2
    font.save(target)


def origin_text(commit: str, rows: list[tuple[str, str, str, str]]) -> str:
    lines = [
        "# Where the overlay theme comes from",
        "",
        "Written by `tools/refresh_overlay_theme.py`; do not edit by hand.",
        "",
        f"* Repository: `{SOURCE_REPO}` (public)",
        f"* Commit: `{commit}`",
        "",
        "| Vendored file | Source path | Source SHA-256 | Vendored SHA-256 |",
        "| --- | --- | --- | --- |",
    ]
    for target, source, source_hash, target_hash in rows:
        lines.append(f"| `{target}` | `{source}` | `{source_hash}` | `{target_hash}` |")
    lines += [
        "",
        "`tokens.css` and the LICENSE are byte-for-byte copies. The TTF is the",
        "woff2 decompressed by fontTools (same glyphs and metrics, sfnt container).",
        "Bebas Neue is under the SIL Open Font License 1.1 (`BebasNeue-LICENSE.txt`).",
        "",
    ]
    return "\n".join(lines)


def refresh(
    checkout: Path,
    commit: str,
    theme_folder: Path = THEME_FOLDER,
    preview_folder: Path | None = None,
) -> None:
    """Vendor the theme into `theme_folder`, and copy it to `preview_folder` when given."""
    theme_folder.mkdir(parents=True, exist_ok=True)
    copies = [(TOKENS_SOURCE, TOKENS_TARGET), (LICENSE_SOURCE, LICENSE_TARGET)]
    rows = []
    for source, target in copies:
        shutil.copyfile(checkout / source, theme_folder / target)
        rows.append(
            (target, source, sha256_of(checkout / source), sha256_of(theme_folder / target))
        )
    convert_woff2_to_ttf(checkout / FONT_SOURCE, theme_folder / FONT_TARGET)
    rows.append(
        (
            FONT_TARGET,
            FONT_SOURCE,
            sha256_of(checkout / FONT_SOURCE),
            sha256_of(theme_folder / FONT_TARGET),
        )
    )
    (theme_folder / "ORIGIN.md").write_text(origin_text(commit, rows), encoding="utf-8")
    if preview_folder is not None:
        preview_folder.mkdir(parents=True, exist_ok=True)
        for target in (TOKENS_TARGET, FONT_TARGET, LICENSE_TARGET):
            shutil.copyfile(theme_folder / target, preview_folder / target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path, help="a checkout of jbresolin/bajaovrlay")
    parser.add_argument("commit", help="the commit that checkout is at (recorded in ORIGIN.md)")
    args = parser.parse_args(argv)
    refresh(args.checkout, args.commit, preview_folder=PREVIEW_FOLDER)
    print(f"vendored {SOURCE_REPO}@{args.commit} into {THEME_FOLDER} and {PREVIEW_FOLDER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
