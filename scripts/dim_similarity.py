"""Compute a pairwise similarity matrix for dimensions in dimension_sets.json.

Similarity between two dims = Jaccard index of their ACS group sets × 100.

Reads:
    scripts/dimension_sets.json
    morpc_census/acs_dim_names.json

Writes:
    scripts/dim_similarity.csv   (dims × dims, values = Jaccard similarity %)

Usage:
    python scripts/dim_similarity.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
DIM_SETS = HERE / "dimension_sets.json"
DIM_NAMES = HERE.parent / "morpc_census" / "acs_dim_names.json"
OUT = HERE / "dim_similarity.csv"

EXCLUDE = {"dim_000", "dim_002"}


def _pad(key: str) -> str:
    prefix, num = key.rsplit("_", 1)
    return f"{prefix}_{int(num):03d}"


def main() -> None:
    dims = json.loads(DIM_SETS.read_text())
    name_map = json.loads(DIM_NAMES.read_text())

    filtered = {k: v for k, v in dims.items() if _pad(k) not in EXCLUDE}
    keys = list(filtered.keys())
    labels = [name_map.get(_pad(k), _pad(k)) for k in keys]
    group_sets = [set(filtered[k]["groups"][0]) for k in keys]
    sizes = [len(g) for g in group_sets]

    n = len(keys)
    print(f"Building {n}×{n} similarity matrix...")

    mat = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(i, n):
            shared = len(group_sets[i] & group_sets[j])
            union = sizes[i] + sizes[j] - shared
            sim = round(shared / union * 100, 1) if union else 0.0
            mat[i, j] = sim
            mat[j, i] = sim

    df = pd.DataFrame(mat, index=labels, columns=labels)
    df.to_csv(OUT)
    print(f"Wrote {OUT}")

    upper = mat[np.triu_indices(n, k=1)]
    print(f"  Non-zero pairs: {(upper > 0).sum()} / {len(upper)}")
    print(f"  Max similarity: {upper.max():.1f}%")


if __name__ == "__main__":
    main()
