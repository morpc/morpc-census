"""Build the runtime decennial dim files from the naming-pipeline outputs.

Reads:
    morpc_census/dec_dim_names.json    {dim_###: name}     (name_dec_dimension_sets.py)
    scripts/dec_dimension_sets.json    {dim_<i>: {...}}     (build_dec_dimension_sets.py)

Writes:
    morpc_census/dec_dims.json         {dim_###: {name, variables}}
    morpc_census/dec_group_dims.json   {group_code: [dim_###, ...]}

``dec_dims.json`` mirrors ``acs_dims.json``.  ``dec_group_dims.json`` is built
by inverting each dim's ``groups`` listing: the source keys are compound
``{slug}/{vintage}/{group}`` identifiers, so the bare group code (the final
path segment) is recovered and the same bare code accumulates the union of dim
ids it uses across every vintage/survey.  At runtime ``_match_col_names``
disambiguates the resulting candidate set by Jaccard over the actual values.

Usage:
    python scripts/build_dec_dims.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
NAMES_FILE = HERE.parent / "morpc_census" / "dec_dim_names.json"
SETS_FILE = HERE / "dec_dimension_sets.json"
OUT_DIMS = HERE.parent / "morpc_census" / "dec_dims.json"
OUT_GROUP_DIMS = HERE.parent / "morpc_census" / "dec_group_dims.json"


def _pad(key: str) -> str:
    prefix, num = key.rsplit("_", 1)
    return f"{prefix}_{int(num):03d}"


def main() -> None:
    dim_names = json.loads(NAMES_FILE.read_text())
    dim_sets = json.loads(SETS_FILE.read_text())

    # dec_dims.json -------------------------------------------------------
    dims: dict[str, dict] = {}
    for padded, name in sorted(dim_names.items(), key=lambda kv: int(kv[0][4:])):
        unpadded = f"dim_{int(padded[4:])}"
        variables = dim_sets.get(unpadded, {}).get("variables", [])
        dims[padded] = {"name": name, "variables": variables}
    OUT_DIMS.write_text(json.dumps(dims, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(dims)} dims to {OUT_DIMS.name}")

    # dec_group_dims.json -------------------------------------------------
    group_dims: dict[str, list[str]] = {}
    for unpadded, entry in dim_sets.items():
        padded = _pad(unpadded)
        for compound_key in entry.get("groups", [{}])[0]:
            bare = compound_key.rsplit("/", 1)[-1]
            ids = group_dims.setdefault(bare, [])
            if padded not in ids:
                ids.append(padded)
    # stable, readable ordering
    group_dims = {
        code: sorted(ids, key=lambda d: int(d[4:]))
        for code, ids in sorted(group_dims.items())
    }
    OUT_GROUP_DIMS.write_text(json.dumps(group_dims, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(group_dims)} groups to {OUT_GROUP_DIMS.name}")


if __name__ == "__main__":
    main()
