#!/usr/bin/env python
"""Export the OpenAPI schema to docs/openapi.json.

Usage:
    uv run python scripts/export_openapi.py          # write the file
    uv run python scripts/export_openapi.py --check  # fail if stale
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

OUTPUT = ROOT / "docs" / "openapi.json"


def render_schema() -> str:
    """Build the application schema with deterministic key ordering."""
    settings = Settings(
        env="testing",
        log_level="WARNING",
        data_dir=ROOT / ".openapi-tmp",
        db_path=ROOT / ".openapi-tmp" / "hearth.db",
        secret_key="openapi-export-only",
    )
    schema = create_app(settings).openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when docs/openapi.json is out of date",
    )
    args = parser.parse_args()

    rendered = render_schema()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            print(f"{OUTPUT} is stale; run scripts/export_openapi.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is up to date")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
