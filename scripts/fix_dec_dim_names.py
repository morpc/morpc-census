"""Fix semantically-wrong dim names in dec_dim_names.json.

Reads morpc_census/dec_dim_names.json and morpc_census/dec_dims.json,
applies priority-ordered corrections, writes the corrected names file,
then rebuilds the runtime files via build_dec_dims.py.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple


REPO_ROOT = Path(__file__).resolve().parent.parent
NAMES_PATH = REPO_ROOT / "morpc_census" / "dec_dim_names.json"
DIMS_PATH = REPO_ROOT / "morpc_census" / "dec_dims.json"


class Correction(NamedTuple):
    dim_id: str
    old_name: str
    new_name: str


def _vars(dims: dict, dim_id: str) -> set[str]:
    return set(dims[dim_id]["variables"])


# ---------------------------------------------------------------------------
# Rule functions – each returns the new name or None if the rule doesn't match
# ---------------------------------------------------------------------------

AGE_BRACKET_SET = {"Under 18 years", "18 to 64 years", "65 years and over"}

CHILD_AGE_VALUES = {
    "Under 3 years",
    "3 and 4 years",
    "5 years",
    "6 to 11 years",
    "12 and 13 years",
    "14 years",
    "15 to 17 years",
}


def fix_hispanic_or_latino_comma(variables: set[str]) -> str | None:
    """Rule 1: 'Hispanic or Latino,' → correct name based on variable content."""
    if "Hispanic or Latino" in variables or "Not Hispanic or Latino" in variables:
        return "Hispanic or Latino"
    if any(";" in v for v in variables):
        return "Multirace Combination"
    if any("Population of" in v and "races" in v for v in variables):
        return "Multirace Combination"
    # Individual race names alone (e.g. 'Black or African American alone')
    if any(v.endswith(" alone") for v in variables):
        return "Multirace Combination"
    return None


def fix_sex_misnamed(variables: set[str]) -> str | None:
    """Rule 2: 'Sex' dims that are actually age brackets."""
    if variables == AGE_BRACKET_SET:
        return "Age Group"
    return None


def fix_age_misnamed(variables: set[str]) -> str | None:
    """Rule 3: 'Age' dims that are actually institutionalization or relationship."""
    has_inst = "Institutionalized population" in variables
    has_noninst = "Noninstitutionalized population" in variables
    has_year_range = any(
        "year" in v.lower() and ("to" in v or "under" in v or "over" in v or "and over" in v)
        for v in variables
    )

    if has_inst and has_noninst and not has_year_range:
        # Check for relationship values
        relationship_markers = {"Own child", "Related child", "Householder or spouse", "Nonrelatives"}
        if relationship_markers & variables:
            return "Relationship to Householder"
        return "Institutionalization Status"

    # Pure relationship values (no institutionalization)
    if {"Own child", "Related child", "Householder or spouse", "Nonrelatives"} <= variables:
        return "Relationship to Householder"

    return None


def fix_household_type_misnamed(variables: set[str]) -> str | None:
    """Rule 4: 'Household Type' dims that are actually something else."""
    # Residence Type
    residence_core = {"In households", "In group quarters"}
    if residence_core <= variables and variables - residence_core <= {"Coverage improvement adjustment"}:
        return "Residence Type"

    # Householder Sex
    if variables == {"Female householder, no spouse present", "Male householder, no spouse present"}:
        return "Householder Sex"

    # Own child / Other relatives
    if variables == {"Own child", "Other relatives"}:
        return "Relationship to Householder"

    # Institutionalized + family households → Living Arrangement
    if "Institutionalized population" in variables and "In family households" in variables:
        return "Living Arrangement"

    # Child Relationship
    if any("Natural-born or adopted" in v or v == "Step" for v in variables):
        return "Child Relationship"

    # Household Size (binary)
    if variables == {"2-or-more-person household", "1-person household"}:
        return "Household Size"

    # Nonrelative Type
    nonrelative_markers = {"Roomer or boarder", "Housemate or roommate", "Unmarried partner"}
    if nonrelative_markers & variables:
        return "Nonrelative Type"

    # Relationship to Householder – full list with Householder or spouse + Related child
    if (
        "Householder or spouse" in variables
        and "Related child" in variables
        and "Nonrelatives" in variables
    ):
        return "Relationship to Householder"

    # Relationship to Householder – full Householder + Spouse list (≥5 items)
    if (
        "Householder" in variables
        and "Spouse" in variables
        and len(variables) >= 5
        and "In family households" not in variables
        and "Institutionalized population" not in variables
        and "In married-couple family" not in variables
        and "In husband-wife family" not in variables
    ):
        return "Relationship to Householder"

    # Family Type – married/husband-wife family with 'In other family'
    if ("In married-couple family" in variables or "In husband-wife family" in variables) and (
        "In other family" in variables
    ):
        return "Family Type"

    # Family Type – male/female householder no spouse
    if (
        "In male householder, no wife present family" in variables
        and "In female householder, no husband present family" in variables
    ):
        return "Family Type"

    # Living Arrangement – nonfamily + family households
    if "In nonfamily households" in variables and "In family households" in variables:
        return "Living Arrangement"

    # Living Arrangement – grandchild + male householder context (no full rel list)
    if "Grandchild" in variables and any("male householder" in v.lower() for v in variables):
        if "Householder" not in variables:
            return "Living Arrangement"

    # Same-sex household type
    if any("Same-sex" in v for v in variables):
        return "Same-Sex Household Type"

    # Age of Own Children
    if variables <= CHILD_AGE_VALUES and variables & {"Under 3 years", "3 and 4 years"}:
        return "Age of Own Children"

    # Relationship to Householder – Child (not Biological child) list with relatives
    if (
        "Child" in variables
        and "Householder" in variables
        and "Biological child" not in variables
        and "Other relatives" in variables
    ):
        return "Relationship to Householder"

    return None


def fix_relationship_misnamed(variables: set[str]) -> str | None:
    """Rule 5: 'Relationship' dims that are actually something else."""
    # Residence Type
    residence_core = {"In households", "In group quarters"}
    if residence_core <= variables and variables - residence_core <= {"Coverage improvement adjustment"}:
        return "Residence Type"

    # Living Alone Status (only those two values)
    if variables == {"Living alone", "Not living alone"}:
        return "Living Alone Status"

    # Sex and Living Arrangement
    if {"Living alone", "Not living alone"} <= variables and {"Male", "Female"} <= variables:
        return "Sex and Living Arrangement"

    # Sex and Age Group
    if "Under 18 years" in variables and {"Male", "Female"} <= variables:
        return "Sex and Age Group"

    # Living Arrangement – family + nonfamily households
    if "In family households" in variables and "In nonfamily households" in variables:
        return "Living Arrangement"

    # Living Arrangement – married/husband-wife family with 'In other family'
    if ("In married-couple family" in variables or "In husband-wife family" in variables) and (
        "In other family" in variables
    ):
        return "Living Arrangement"

    # Living Arrangement – grandchild + no husband/wife present
    if "Grandchild" in variables and any(
        "no husband present" in v or "no wife present" in v for v in variables
    ):
        return "Living Arrangement"

    # Relationship to Householder – own child + other relatives
    if "Own child" in variables and "Other relatives" in variables:
        return "Relationship to Householder"

    # Relationship to Householder – full list with Biological/Adopted child
    if "Biological child" in variables or "Adopted child" in variables:
        return "Relationship to Householder"

    # Relationship to Householder – Householder + Stepchild
    if "Householder" in variables and "Stepchild" in variables:
        return "Relationship to Householder"

    # Relationship to Householder – Spouse or partner
    if "Spouse or partner" in variables:
        return "Relationship to Householder"

    # Relationship to Householder – simpler list (Householder + Spouse + relatives, ≥5)
    if (
        "Householder" in variables
        and "Spouse" in variables
        and len(variables) >= 5
        and "In family households" not in variables
    ):
        return "Relationship to Householder"

    return None


def fix_tenure_misnamed(variables: set[str]) -> str | None:
    """Rule 6: 'Tenure' dims that need detail label."""
    detail_set = {"Owned with a mortgage or a loan", "Owned free and clear", "Renter occupied"}
    if variables == detail_set:
        return "Tenure (Detail)"
    return None


def fix_household_size_misnamed(variables: set[str]) -> str | None:
    """Rule 7: 'Household Size' dims with average household size."""
    if any("Average household size" in v for v in variables):
        return "Average Household Size"
    return None


# ---------------------------------------------------------------------------
# Main correction logic
# ---------------------------------------------------------------------------

def apply_corrections(
    names: dict[str, str],
    dims: dict[str, dict],
) -> list[Correction]:
    corrections: list[Correction] = []

    for dim_id, current_name in names.items():
        variables = _vars(dims, dim_id)
        new_name: str | None = None

        if current_name == "Hispanic or Latino,":
            new_name = fix_hispanic_or_latino_comma(variables)
        elif current_name == "Sex":
            new_name = fix_sex_misnamed(variables)
        elif current_name == "Age":
            new_name = fix_age_misnamed(variables)
        elif current_name == "Household Type":
            new_name = fix_household_type_misnamed(variables)
        elif current_name == "Relationship":
            new_name = fix_relationship_misnamed(variables)
        elif current_name == "Tenure":
            new_name = fix_tenure_misnamed(variables)
        elif current_name == "Household Size":
            new_name = fix_household_size_misnamed(variables)

        if new_name is not None and new_name != current_name:
            corrections.append(Correction(dim_id, current_name, new_name))

    return corrections


def print_report(corrections: list[Correction]) -> None:
    by_category: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in corrections:
        by_category[(c.old_name, c.new_name)].append(c.dim_id)

    total = len(corrections)
    print(f"\n{'='*60}")
    print(f"CORRECTION REPORT — {total} dims renamed")
    print(f"{'='*60}")

    for (old, new), dim_ids in sorted(by_category.items()):
        count = len(dim_ids)
        examples = ", ".join(dim_ids[:3])
        if len(dim_ids) > 3:
            examples += f", ... (+{len(dim_ids) - 3} more)"
        print(f"\n  {repr(old)} → {repr(new)}")
        print(f"    count : {count}")
        print(f"    dims  : {examples}")

    print()


def main() -> None:
    with NAMES_PATH.open() as f:
        names: dict[str, str] = json.load(f)

    with DIMS_PATH.open() as f:
        dims: dict[str, dict] = json.load(f)

    corrections = apply_corrections(names, dims)
    print_report(corrections)

    for c in corrections:
        names[c.dim_id] = c.new_name

    with NAMES_PATH.open("w") as f:
        json.dump(names, f, indent=2)
        f.write("\n")

    print(f"Wrote corrected names to {NAMES_PATH}")
    print("Rebuilding runtime files via build_dec_dims.py ...")

    subprocess.run(
        ["python", "scripts/build_dec_dims.py"],
        cwd=str(REPO_ROOT),
        check=True,
    )

    print("Done.")


if __name__ == "__main__":
    main()
