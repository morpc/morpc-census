"""Build dec_dimension_sets.json from dec_variable_groups.json.

The decennial analogue of build_dimension_sets.py.  The algorithm is identical
(parse labels into aligned columns, extract ordered unique values, collapse to
one entry per distinct ordered tuple), so the shift-align / collapse / concept
helpers are imported from build_dimension_sets.  Only the parts that differ for
decennial data are reimplemented here:

  * Labels have no ``Estimate!!`` prefix.  Modern (2020) labels look like
    `` !!Total:!!Population of one race`` (leading space); legacy (2000/2010)
    labels look like ``Total!!Population of one race``.  Both split cleanly on
    ``!!`` once empty/``:``-suffixed segments are dropped.
  * ``variables`` in dec_variable_groups.json is already filtered to count
    variables, so there is no ``E``-suffix filter and the code is used as-is
    (no trailing-character strip).
  * Source entries use a compound ``{slug}/{vintage}/{group}`` key.  Several
    vintages/surveys reuse the same bare group code (e.g. ``P12`` in dec/dhc
    2020 and dec/sf1 2010), so stage 1 is keyed by the compound key and the
    bare group code is recovered when inverting to dec_group_dims.json.
  * Concepts are Title-Cased in the source; connector words are lowercased and
    ``[NN]`` count suffixes stripped so the shared concept parser splits them.

Writes:
    scripts/dec_group_variable_sets.json   {compound_key: [[col0 values], ...]}
    scripts/dec_dimension_sets.json        {dim_<i>: {variables, ...}}

Usage:
    python scripts/build_dec_dimension_sets.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from build_dimension_sets import (  # reuse the identical helpers
    _shift_align,
    _unique_sets,
    build_dimension_sets,
)

HERE = Path(__file__).parent
SOURCE = HERE / "dec_variable_groups.json"
OUT_SETS = HERE / "dec_group_variable_sets.json"
OUT_DIMS = HERE / "dec_dimension_sets.json"

# Connector words kept lowercase so the concept parser (' by ', ' and [Cap]',
# 'for [Cap]') can split Title-Cased decennial concepts.
_CONNECTORS = {
    "by", "and", "for", "the", "of", "in", "with",
    "a", "an", "or", "to", "on", "at", "as",
}


def _normalize_concept(concept: str) -> str:
    """Lowercase connector words and strip ``[NN]`` count suffixes.

    e.g. ``"Sex By Age For Selected Age Categories"`` ->
         ``"Sex by Age for Selected Age Categories"``.
    """
    clean = re.sub(r"\s*\[[^\]]*\]", "", concept or "").strip()
    words = clean.split()
    out = []
    for i, word in enumerate(words):
        lower = word.lower()
        out.append(lower if i > 0 and lower in _CONNECTORS else word)
    return " ".join(out)


def _segments(label: str) -> list[str]:
    """Split a decennial label into non-empty, ``:``-stripped segments."""
    segs = [seg.rstrip(":").strip() for seg in label.split("!!")]
    return [seg for seg in segs if seg]


def _aligned_table(variables: dict[str, str]) -> "pd.DataFrame | None":
    """Shift-aligned table of variable label segments (None for empty cells).

    Unlike the ACS version, every variable is kept (the dict is pre-filtered)
    and the code is used verbatim as the row index.
    """
    seg_rows: dict[str, list[str]] = {
        code: _segments(label) for code, label in variables.items()
    }
    seg_rows = {code: segs for code, segs in seg_rows.items() if segs}
    if not seg_rows:
        return None

    width = max(len(segs) for segs in seg_rows.values())
    ncols = width + 2
    rows = [segs + [None] * (ncols - len(segs)) for segs in seg_rows.values()]
    _shift_align(rows)

    df = pd.DataFrame(rows, index=list(seg_rows))
    df = df.loc[:, df.notna().any(axis=0)]
    df.columns = range(df.shape[1])
    return df.where(df.notna(), "")


def build_group_sets(source: dict) -> dict[str, list[list[str]]]:
    """Return {compound_key: [[col0 values], [col1 values], ...]} for every group."""
    result: dict[str, list[list[str]]] = {}
    for key, meta in source.items():
        df = _aligned_table(meta.get("variables", {}))
        if df is not None:
            result[key] = _unique_sets(df)
    return result


def main() -> None:
    source = json.loads(SOURCE.read_text())
    concepts = {key: _normalize_concept(meta.get("concept", "")) for key, meta in source.items()}
    universes = {key: meta.get("universe", "") for key, meta in source.items()}

    print("Stage 1: parsing and aligning variable labels...")
    group_sets = build_group_sets(source)
    skipped = len(source) - len(group_sets)
    OUT_SETS.write_text(json.dumps(group_sets, indent=2, ensure_ascii=False) + "\n")
    print(f"  {len(group_sets)} groups written to {OUT_SETS.name} ({skipped} skipped, no vars)")

    print("Stage 2: collapsing to unique value-sets...")
    dims = build_dimension_sets(group_sets, concepts, universes)
    OUT_DIMS.write_text(json.dumps(dims, indent=2, ensure_ascii=False) + "\n")
    n = len(dims)
    print(f"  {n} unique value-sets written to {OUT_DIMS.name}")
    print(f"  used by >=5 groups: {sum(1 for v in dims.values() if len(v['groups'][0]) >= 5)}")
    print(f"  used by >=2 groups: {sum(1 for v in dims.values() if len(v['groups'][0]) >= 2)}")
    print(f"  singletons:         {sum(1 for v in dims.values() if len(v['groups'][0]) == 1)}")

    print("\nTop 8 by group count:")
    for key, entry in list(dims.items())[:8]:
        vals = entry["variables"]
        n_groups = len(entry["groups"][0])
        print(f"  {key!r}: groups={n_groups} vals={len(vals)}  {vals[:6]}")


if __name__ == "__main__":
    main()
