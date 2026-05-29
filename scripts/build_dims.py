"""Merge dim_names.json + dimension_sets.json → morpc_census/dims.json.

Run after build_dimension_sets.py or after editing dim_names.json via dim_namer.py.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE       = Path(__file__).parent
NAMES_FILE = HERE.parent / "morpc_census" / "dim_names.json"
SETS_FILE  = HERE / "dimension_sets.json"
OUT_FILE   = HERE.parent / "morpc_census" / "dims.json"


def main() -> None:
    dim_names = json.loads(NAMES_FILE.read_text())
    dim_sets  = json.loads(SETS_FILE.read_text())

    dims: dict[str, dict] = {}
    for padded, name in sorted(dim_names.items(), key=lambda kv: int(kv[0][4:])):
        unpadded  = f"dim_{int(padded[4:])}"
        variables = dim_sets.get(unpadded, {}).get("variables", [])
        dims[padded] = {"name": name, "variables": variables}

    OUT_FILE.write_text(json.dumps(dims, indent=2) + "\n")
    print(f"Written {len(dims)} dims to {OUT_FILE.name}")


if __name__ == "__main__":
    main()
