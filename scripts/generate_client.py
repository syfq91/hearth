#!/usr/bin/env python
"""Generate typed TypeScript client definitions from docs/openapi.json.

Usage:
    uv run python scripts/generate_client.py          # write schema.d.ts
    uv run python scripts/generate_client.py --check  # fail if stale or missing
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_INPUT = ROOT / "docs" / "openapi.json"
CLIENT_OUTPUT = ROOT / "packages" / "client" / "src" / "schema.d.ts"


def generate_typescript(output_path: Path) -> None:
    """Run openapi-typescript against docs/openapi.json."""
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("npx is not available in PATH; cannot generate client artifact.")

    cmd = [
        npx,
        "--yes",
        "openapi-typescript",
        str(SCHEMA_INPUT),
        "-o",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"openapi-typescript failed (exit {result.returncode}):\n{result.stderr}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when packages/client/src/schema.d.ts is out of date",
    )
    args = parser.parse_args()

    if not SCHEMA_INPUT.exists():
        print(f"Error: {SCHEMA_INPUT} does not exist.", file=sys.stderr)
        return 1

    if args.check:
        if not CLIENT_OUTPUT.exists():
            print(f"{CLIENT_OUTPUT} is missing; run scripts/generate_client.py", file=sys.stderr)
            return 1

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_output = Path(tmpdir) / "schema.d.ts"
            generate_typescript(tmp_output)

            current = CLIENT_OUTPUT.read_text(encoding="utf-8")
            generated = tmp_output.read_text(encoding="utf-8")
            if current != generated:
                print(
                    f"{CLIENT_OUTPUT} is stale; run scripts/generate_client.py",
                    file=sys.stderr,
                )
                return 1

        print(f"{CLIENT_OUTPUT} is up to date")
        return 0

    CLIENT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    generate_typescript(CLIENT_OUTPUT)
    print(f"wrote {CLIENT_OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
