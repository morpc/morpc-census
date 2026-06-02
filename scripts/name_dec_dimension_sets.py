"""Assign human-readable names to dec_dimension_sets.json entries.

Unlike the ACS greedy namer (name_dimension_sets.py), which assigns each
concept to a single dim and removes it from all others, the decennial namer
assigns each dim its own highest-count concept component *independently*.

The ACS dedup builds a canonical, collision-free dimension vocabulary.  For
runtime column naming that distinction is unnecessary and harmful: the same
true dimension (e.g. Sex) appears as several value-sets that differ only in
member order (``[Female, Male]`` vs ``[Male, Female]``), and all of them
should be named "Sex".  ``DimensionTable._match_col_names`` already disambig-
uates by Jaccard over the actual column values, so duplicate names are correct
and give far better coverage than the greedy approach.

Reads:
    scripts/dec_dimension_sets.json    from build_dec_dimension_sets.py

Writes:
    morpc_census/dec_dim_names.json    {dim_###: name}

Usage:
    python scripts/name_dec_dimension_sets.py
"""
from __future__ import annotations

import json
from pathlib import Path

from name_dimension_sets import _pad

HERE = Path(__file__).parent
DIM_SETS = HERE / "dec_dimension_sets.json"
OUT = HERE.parent / "morpc_census" / "dec_dim_names.json"


def assign_names(dims: dict[str, dict]) -> dict[str, str]:
    """Name each dim independently: single value -> that value; else top concept."""
    assignments: dict[str, str] = {}
    for key, entry in dims.items():
        variables = entry["variables"]
        if len(variables) == 1:
            assignments[key] = variables[0]
            continue
        components = entry.get("concept_components") or {}
        if components:
            # concept_components is already frequency-sorted (most_common order).
            assignments[key] = next(iter(components))
    return assignments


def main() -> None:
    dims = json.loads(DIM_SETS.read_text())

    print(f"Assigning names to {len(dims)} dimensions...")
    assignments = assign_names(dims)

    n_single = sum(1 for k in dims if len(dims[k]["variables"]) == 1 and k in assignments)
    n_concept = sum(1 for k in assignments if len(dims[k]["variables"]) > 1)
    unresolved = [k for k in dims if k not in assignments]

    print(f"  single-variable (auto): {n_single}")
    print(f"  concept-assigned:       {n_concept}")
    print(f"  unresolved:             {len(unresolved)}")

    output = {_pad(key): (assignments.get(key) or _pad(key)) for key in dims}
    OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
