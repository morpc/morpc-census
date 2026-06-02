"""Interactive terminal utility for naming Census dimension sets.

Navigation:
  Tab / Enter (no change) — advance without saving
  Type + Enter            — save new name and advance
  Ctrl-C                  — exit
"""

from __future__ import annotations

import json
import readline
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DIMS_FILE = HERE / "dimension_sets.json"
NAMES_FILE = HERE.parent / "morpc_census" / "acs_dim_names.json"
SIM_FILE = HERE / "dim_similarity.csv"

BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RESET = "\033[0m"


def _unpad(key: str) -> str:
    """dim_025 → dim_25"""
    return f"dim_{int(key[4:])}"


def _is_unnamed(key: str, names: dict) -> bool:
    return names.get(key, key) == key



def _top_similar(
    name: str,
    sim_df: pd.DataFrame,
    name_to_key: dict[str, str],
    n: int = 3,
) -> list[tuple[str, str]]:
    """Return top-n similar dims as (padded_key, name) pairs, excluding self."""
    if sim_df.empty or name not in sim_df.index:
        return []
    row = sim_df.loc[name].drop(labels=[name], errors="ignore").sort_values(ascending=False)
    results = []
    for other_name, score in row.items():
        if score <= 0:
            break
        padded = name_to_key.get(str(other_name))
        if padded:
            results.append((padded, str(other_name)))
        if len(results) >= n:
            break
    return results


def main() -> None:
    dim_sets: dict = json.loads(DIMS_FILE.read_text())
    names: dict[str, str] = json.loads(NAMES_FILE.read_text())

    try:
        sim_df = pd.read_csv(SIM_FILE, index_col=0)
    except Exception:
        sim_df = pd.DataFrame()

    # Include all dims in reverse map (unnamed dims have name == key)
    name_to_key: dict[str, str] = {v: k for k, v in names.items()}

    all_keys = sorted(names.keys(), key=lambda k: int(k[4:]))
    unnamed_keys = [k for k in all_keys if _is_unnamed(k, names)]
    named_keys   = [k for k in all_keys if not _is_unnamed(k, names)]
    ordered_keys = unnamed_keys + named_keys

    saved_count = 0
    readline.parse_and_bind("tab: accept-line")

    try:
        for key in ordered_keys:
            current_name = names[key]
            entry = dim_sets.get(_unpad(key), {})
            variables = entry.get("variables", [])
            all_concepts = list(entry.get("concept_components", {}).keys())
            concepts  = list(entry.get("concept_components", {}).items())[:3]
            n_groups  = len(entry.get("groups", [{}])[0])
            unnamed_remaining = sum(1 for k in names if _is_unnamed(k, names))

            similar = _top_similar(current_name, sim_df, name_to_key)

            # ── Display ──────────────────────────────────────────────────
            print("\033[2J\033[H", end="")

            status = f"{YELLOW}(unnamed){RESET}" if _is_unnamed(key, names) else f"{GREEN}(named){RESET}"
            print(f"{BOLD}{CYAN}{key}{RESET}  {status}  {DIM}{n_groups} groups  •  {unnamed_remaining} unnamed remaining{RESET}")

            if similar:
                print()
                print(f"  {BOLD}Most similar:{RESET}")
                for sim_key, sim_name in similar:
                    sim_entry = dim_sets.get(_unpad(sim_key), {})
                    sim_vars  = " | ".join(sim_entry.get("variables", [])[:5])
                    label = f'"{sim_name}"' if sim_name != sim_key else "(unnamed)"
                    print(f"    {sim_key}  {label:<34}  {DIM}{sim_vars}{RESET}")

            if concepts:
                print()
                concept_str = "  /  ".join(f"{cname} × {count}" for cname, count in concepts)
                print(f"  {BOLD}Concepts:{RESET}  {concept_str}")

            if groups := entry.get("groups", [{}])[0]:
                MAX_GROUPS = 8
                items = list(groups.items())
                print()
                print(f"  {BOLD}Groups ({len(items)}):{RESET}")
                for code, concept in items[:MAX_GROUPS]:
                    print(f"    {DIM}{code}{RESET}  {concept}")
                if len(items) > MAX_GROUPS:
                    print(f"    {DIM}(+{len(items) - MAX_GROUPS} more){RESET}")

            if variables:
                MAX_VARS = 12
                shown = variables[:MAX_VARS]
                tail = f"  {DIM}(+{len(variables) - MAX_VARS} more){RESET}" if len(variables) > MAX_VARS else ""
                print()
                print(f"  {BOLD}Variables ({len(variables)}):{RESET}  {' | '.join(shown)}{tail}")

            # ── Prompt ───────────────────────────────────────────────────
            readline.clear_history()
            for concept in reversed(all_concepts):
                readline.add_history(concept)
            print()
            result = input("  Name: ").strip()

            if result and result != current_name:
                old_name = names[key]
                names[key] = result
                NAMES_FILE.write_text(json.dumps(names, indent=2))
                saved_count += 1
                # Keep reverse map current
                name_to_key.pop(old_name, None)
                name_to_key[result] = key

    except KeyboardInterrupt:
        print(f"\n\nSaved {saved_count} name{'s' if saved_count != 1 else ''}. Exiting.")
        sys.exit(0)

    print(f"\nAll {len(ordered_keys)} dimensions reviewed. Saved {saved_count} names.")


if __name__ == "__main__":
    main()
