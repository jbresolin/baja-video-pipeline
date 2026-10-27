"""Write orchestrator/openapi.yaml from the contract stubs.

uv run python tools/generate_openapi.py           # (re)write the file
uv run python tools/generate_openapi.py --check   # exit 1 if it is stale
"""

import argparse
import sys
from pathlib import Path

import yaml

# Make "tools.openapi_contract" importable when run as a script from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.openapi_contract.app import build_contract_app  # noqa: E402

OPENAPI_PATH = REPO_ROOT / "orchestrator" / "openapi.yaml"


def render_openapi_yaml() -> str:
    document = build_contract_app().openapi()
    # sort_keys=False keeps FastAPI's order (paths in declaration order).
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only check, do not write")
    arguments = parser.parse_args()

    fresh = render_openapi_yaml()
    if arguments.check:
        current = OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.exists() else ""
        if current != fresh:
            print(f"{OPENAPI_PATH} is out of date; run tools/generate_openapi.py", file=sys.stderr)
            return 1
        print(f"{OPENAPI_PATH} is up to date")
        return 0

    OPENAPI_PATH.write_text(fresh, encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
