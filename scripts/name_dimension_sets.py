"""Assign human-readable names to dimension_sets.json entries.

Algorithm:
  1. Single-variable dims: name = the variable value.
  2. Multi-variable dims: greedy assignment from precomputed concept_components.
       - Find the (dim, concept) pair whose concept has the highest count in
         that dim across all still-unassigned dims.
       - Assign that concept as the name for that dim.
       - Remove that concept from every other dim's candidate pool.
       - Repeat until no candidates remain; leftover dims stay as dim_<i>.

Reads:
    scripts/dimension_sets.json       from build_dimension_sets.py

Writes:
    morpc_census/dim_names.json       {dim_###: name}

Usage:
    python scripts/name_dimension_sets.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
DIM_SETS = HERE / "dimension_sets.json"
OUT = HERE.parent / "morpc_census" / "dim_names.json"


def _pad(key: str) -> str:
    prefix, num = key.rsplit("_", 1)
    return f"{prefix}_{int(num):03d}"


def assign_names(dims: dict[str, dict]) -> dict[str, str]:
    assignments: dict[str, str] = {}

    for key, entry in dims.items():
        if len(entry["variables"]) == 1:
            assignments[key] = entry["variables"][0]

    remaining: dict[str, dict[str, int]] = {
        key: dict(entry["concept_components"])
        for key, entry in dims.items()
        if key not in assignments
    }

    while remaining:
        best_key = best_concept = None
        best_count = 0
        for key, components in remaining.items():
            for concept, count in components.items():
                if count > best_count:
                    best_count = count
                    best_key = key
                    best_concept = concept

        if best_key is None:
            break

        assignments[best_key] = best_concept
        del remaining[best_key]

        for components in remaining.values():
            components.pop(best_concept, None)

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
