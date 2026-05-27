"""Build dimension_sets.json from acs_variable_groups.json.

Two-stage pipeline (runs in one pass):

  Stage 1 — parse & align
    For each ACS group, parse the ``E`` variable labels into dimension columns via a
    rightward-shift algorithm that handles ragged Census label trees, then extract the
    ordered unique values per column.
    Intermediate: group_variable_sets.json  {group_code: [[col0 values], ...]}

  Stage 2 — collapse
    Collapse identical value-sets across groups into one entry per distinct ordered
    tuple, keyed by positional identifier (dim_0, dim_1, ...), most-used first.
    Final:        dimension_sets.json        {dim_<i>: {variables, groups}}

Usage:
    python scripts/build_dimension_sets.py
"""
from __future__ import annotations

import json
import re
from collections import Counter, OrderedDict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
SOURCE = HERE / "acs_variable_groups.json"
OUT_SETS = HERE / "group_variable_sets.json"
OUT_DIMS = HERE / "dimension_sets.json"

ESTIMATE_PREFIX = "Estimate!!"


# ---------------------------------------------------------------------------
# Stage 1 helpers — label parsing and shift-align
# ---------------------------------------------------------------------------

def _segments(label: str) -> list[str]:
    """Strip the Estimate prefix, split on ``!!``, drop the ``:`` subtotal marker."""
    if label.startswith(ESTIMATE_PREFIX):
        label = label[len(ESTIMATE_PREFIX):]
    elif label == "Estimate":
        label = "Total"
    segs = [seg.rstrip(":").strip() for seg in label.split("!!")]
    return [seg for seg in segs if seg]


def _shift_align(rows: list[list]) -> list[list]:
    """Push each value rightward until it no longer appears in any column to its right.

    Values that appear at different depths in different rows of the same Census tree (e.g.
    a leaf value under branches of varying depth) get pushed to the deepest column where
    they ever appear, so all instances of the same value align in one column. Mutates
    ``rows`` in place to a fixpoint and returns them.
    """
    if not rows:
        return rows
    ncols = len(rows[0])
    max_cols = ncols * 2
    changed = True
    while changed:
        changed = False
        right_of: list[set] = [set() for _ in range(ncols)]
        acc: set = set()
        for j in range(ncols - 1, -1, -1):
            right_of[j] = set(acc)
            for row in rows:
                if row[j] is not None:
                    acc.add(row[j])
        for i in range(ncols - 1):
            rv = right_of[i]
            for row in rows:
                v = row[i]
                if v is None or v not in rv:
                    continue
                # If the value also appears later in this same row, shifting is futile
                # (e.g. repeated "Total (dollars)" holders in median tables).
                if v in row[i + 1:]:
                    continue
                if row[-1] is not None:
                    if ncols >= max_cols:
                        continue
                    for other in rows:
                        other.append(None)
                    ncols += 1
                    right_of.append(set())
                row.insert(i, None)
                row.pop()
                changed = True
    return rows


def _aligned_table(variables: dict[str, str]) -> pd.DataFrame | None:
    """Shift-aligned table of variable label segments (None for empty cells)."""
    seg_rows: dict[str, list[str]] = {}
    for code, label in variables.items():
        if not code.endswith("E"):
            continue
        seg_rows[code[:-1]] = _segments(label)
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


def _unique_sets(df: pd.DataFrame) -> list[list[str]]:
    """Ordered (Census-order) unique non-empty values per aligned column."""
    sets: list[list[str]] = []
    for col in df.columns:
        seen: list[str] = []
        for value in df[col]:
            clean = value.rstrip(":").strip()
            if clean and clean not in seen:
                seen.append(clean)
        if seen:
            sets.append(seen)
    return sets


# ---------------------------------------------------------------------------
# Stage 1 — parse all groups
# ---------------------------------------------------------------------------

def build_group_sets(source: dict) -> dict[str, list[list[str]]]:
    """Return {group_code: [[col0 values], [col1 values], ...]} for every group."""
    result: dict[str, list[list[str]]] = {}
    for code, meta in source.items():
        df = _aligned_table(meta.get("variables", {}))
        if df is not None:
            result[code] = _unique_sets(df)
    return result


# ---------------------------------------------------------------------------
# Concept parsing — mirrors name_dimension_sets.py
# ---------------------------------------------------------------------------

_QUALIFIER_RE = re.compile(
    r'(?:'
    r'\bin the\b'
    r'|\bfor the\b'
    r'|\bfor (?=[A-Z])'
    r'|--'
    r')'
)

# Split on " and " only when followed by a capital letter, so compound dimension
# phrases like "Nativity and Citizenship Status" or "Language Spoken at Home and
# Ability to Speak English" are separated, while lowercase-joined lists like
# "Agriculture, forestry, fishing and hunting" are left intact.
_AND_RE = re.compile(r' and (?=[A-Z])')


def _concept_heads(concept: str) -> list[str]:
    """Return the head noun phrase for each component of a concept string.

    Components are identified by splitting on ' by ' and ' and [Capital]'.
    Qualifier clauses ("in the ...", "for the ...", "for [Capital]...") are stripped
    from each component's tail to yield the core dimension name.  Type/count
    parentheticals that start with a digit (e.g. "(5 Types)") are preserved;
    other parentheticals are stripped.

    When ' and [Capital]' splits a phrase and the last part carries a shared
    ' of ...' qualifier that earlier bare-noun parts lack, that qualifier is
    propagated to those parts so that e.g. "Presence and Type of Internet
    Subscription" yields ["Presence of Internet Subscription",
    "Type of Internet Subscription"] rather than ["Presence", "Type of ..."].
    """
    clean = re.sub(r'--.*$', '', concept or '').strip()
    clean = re.sub(r'\s*\((?!\d)[^)]*\)', '', clean).strip()
    heads = []
    for phrase in clean.split(' by '):
        phrase = phrase.strip()
        if not phrase:
            continue
        m = _QUALIFIER_RE.search(phrase)
        head = phrase[: m.start()].strip() if m else phrase
        and_parts = [p.strip() for p in _AND_RE.split(head)]
        if len(and_parts) > 1:
            m_of = re.search(r' of ', and_parts[-1])
            if m_of:
                tail = and_parts[-1][m_of.start():]
                and_parts = [
                    p + tail if ' of ' not in p else p
                    for p in and_parts
                ]
        for part in and_parts:
            if part:
                heads.append(part[:1].upper() + part[1:])
    return heads


# ---------------------------------------------------------------------------
# Stage 2 — collapse to unique value-sets
# ---------------------------------------------------------------------------

def build_dimension_sets(
    group_sets: dict[str, list[list[str]]],
    concepts: dict[str, str],
) -> dict[str, dict]:
    """Collapse per-group column value-sets into one entry per distinct ordered tuple.

    Entries are keyed by positional ``dim_<i>`` identifier, most-used first.  Each
    entry includes a ``concept_components`` field: a frequency-sorted dict of all
    parsed phrase heads from every group's concept string, giving a quick view of
    what dimension names the groups associate with this value-set.

    When the number of concept phrase heads exactly matches the number of non-root
    columns in a group, the mapping is unambiguous: each dim receives only the vote
    for its specific phrase position.  When the mapping is ambiguous all heads are
    tallied as before.
    """
    sig_values: dict[tuple, list[str]] = {}
    sig_groups: dict[tuple, OrderedDict] = {}

    for code, columns in group_sets.items():
        for values in columns:
            sig = tuple(values)
            if sig not in sig_values:
                sig_values[sig] = values
                sig_groups[sig] = OrderedDict()
            sig_groups[sig][code] = None

    order = sorted(sig_values, key=lambda s: -len(sig_groups[s]))

    result: dict[str, dict] = {}
    for i, sig in enumerate(order):
        codes = list(sig_groups[sig])
        tally: Counter = Counter()
        for code in codes:
            heads = _concept_heads(concepts.get(code, ""))
            group_cols = group_sets.get(code, [])
            col_idx = next(
                (j for j, c in enumerate(group_cols) if tuple(c) == sig), None
            )
            non_root = [j for j, c in enumerate(group_cols) if len(c) > 1]
            if col_idx is not None and col_idx in non_root:
                phrase_idx = non_root.index(col_idx)
                if len(heads) == len(non_root):
                    # Unambiguous 1:1 phrase-to-column mapping.
                    tally[heads[phrase_idx]] += 1
                elif len(heads) == 2 and len(non_root) > 2:
                    # 2-phrase concept with extra nested columns: first non-root
                    # col is phrase 0; all others are nested under it (phrase 1).
                    tally[heads[0] if phrase_idx == 0 else heads[1]] += 1
                else:
                    for head in heads:
                        tally[head] += 1
            else:
                for head in heads:
                    tally[head] += 1
        result[f"dim_{i}"] = {
            "variables": sig_values[sig],
            "concept_components": dict(tally.most_common()),
            "groups": [{code: concepts.get(code, "") for code in codes}],
        }
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    source = json.loads(SOURCE.read_text())
    concepts = {code: meta.get("concept", "") for code, meta in source.items()}

    print("Stage 1: parsing and aligning variable labels...")
    group_sets = build_group_sets(source)
    skipped = len(source) - len(group_sets)
    OUT_SETS.write_text(json.dumps(group_sets, indent=2, ensure_ascii=False) + "\n")
    print(f"  {len(group_sets)} groups written to {OUT_SETS.name} ({skipped} skipped, no E vars)")

    print("Stage 2: collapsing to unique value-sets...")
    dims = build_dimension_sets(group_sets, concepts)
    OUT_DIMS.write_text(json.dumps(dims, indent=2, ensure_ascii=False) + "\n")
    n = len(dims)
    print(f"  {n} unique value-sets written to {OUT_DIMS.name}")
    print(f"  used by >=5 groups: {sum(1 for v in dims.values() if len(v['groups'][0]) >= 5)}")
    print(f"  used by >=2 groups: {sum(1 for v in dims.values() if len(v['groups'][0]) >= 2)}")
    print(f"  singletons:         {sum(1 for v in dims.values() if len(v['groups'][0]) == 1)}")

    print("\nTop 8 by group count:")
    top = list(dims.items())[:8]
    for key, entry in top:
        vals = entry["variables"]
        n_groups = len(entry["groups"][0])
        print(f"  {key!r}: groups={n_groups} vals={len(vals)}  {vals[:6]}")


if __name__ == "__main__":
    main()
