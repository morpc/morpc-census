"""Fetch decennial Census variable groups and write dec_variable_groups.json.

Walks the hierarchy for each implemented decennial survey:
    survey → vintages → groups → variables (count / N-suffix)

The output mirrors the structure of acs_variable_groups.json and is consumed
by build_dec_dimension_sets.py.

Does NOT use morpc_census.Endpoint or a Census API key.

Usage:
    python scripts/fetch_dec_variable_groups.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "dec_variable_groups.json"

BASE_URL = "https://api.census.gov/data"

# Surveys to fetch, listed with ALL vintages.
# The most-recent vintage is used as the canonical variable source per group;
# earlier vintages are fetched to detect group codes that differ across years
# (e.g. dec/pl uses PL001-PL004 in 2000 but P1-P4 in 2010/2020).
SURVEYS: dict[str, dict] = {
    "dec/pl": {
        "slug": "dec_pl",
        "vintages": [2020, 2010, 2000],
    },
    "dec/dhc": {
        "slug": "dec_dhc",
        "vintages": [2020],
    },
    "dec/sf1": {
        "slug": "dec_sf1",
        "vintages": [2010, 2000],
    },
}

# Universe overrides for groups where the API returns an empty string.
# Keyed by group code prefix (uppercase).
_UNIVERSE_PREFIX: list[tuple[str, str]] = [
    ("PCT",  "Total population"),
    ("PCO",  "Total population"),
    ("HCT",  "Housing units"),
    ("H",    "Housing units"),
    ("P",    "Total population"),
    ("GQ",   "Group quarters population"),
]

_UNIVERSE_CODE: dict[str, str] = {
    "TOTAL_POP":         "Total population",
    "TOTAL_POP_18_OVER": "Population 18 years and over",
    "HOUSING_UNIT":      "Housing units",
    "OCCUPIED_HU":       "Occupied housing units",
    "GQ_POP":            "Group quarters population",
}


def _normalize_universe(raw: str, group_code: str) -> str:
    if raw in _UNIVERSE_CODE:
        return _UNIVERSE_CODE[raw]
    if raw:
        return raw
    upper = group_code.upper()
    for prefix, label in _UNIVERSE_PREFIX:
        if upper.startswith(prefix):
            return label
    return "Total population"


def _fetch_json(url: str, retries: int = 3, delay: float = 1.0) -> dict:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            if attempt < retries - 1:
                print(f"  HTTP {exc.code} — retrying in {delay}s…", file=sys.stderr)
                time.sleep(delay)
            else:
                raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("unreachable")


def fetch_groups(survey: str, vintage: int) -> list[dict]:
    """Return list of group metadata dicts from the groups.json endpoint."""
    url = f"{BASE_URL}/{vintage}/{survey}/groups.json"
    try:
        data = _fetch_json(url)
    except urllib.error.HTTPError as exc:
        print(f"  [skip] groups.json for {survey} {vintage}: HTTP {exc.code}", file=sys.stderr)
        return []
    return data.get("groups", [])


def _filter_variables(raw: dict) -> dict[str, str]:
    """Filter raw variable metadata to data-only count variables.

    Two naming conventions exist in decennial data:

    Modern (2020+, some 2010 surveys):
        Variables have an underscore + N suffix, e.g. ``P1_001N``.
        Annotations end in ``NA`` and are excluded.

    Legacy (2010 and earlier for some surveys, e.g. dec/sf1, dec/pl 2010):
        Variables end in digits, e.g. ``P001001``, ``P012003``.  Race-iteration
        groups embed a race letter, e.g. ``P012A005`` (P + table + race + line).
        Error variables end in ``ERR`` (trailing letters) and are excluded.
    """
    import re

    _GEO_KEYS = {"for", "in", "ucgid", "GEO_ID", "NAME"}
    data_vars = {k: v for k, v in raw.items() if k not in _GEO_KEYS}

    # Detect format: modern has underscore-N patterns
    is_modern = any(k.endswith("N") and "_" in k for k in data_vars)

    result: dict[str, str] = {}
    if is_modern:
        for code, meta in data_vars.items():
            if code.endswith("N") and not code.endswith("NA"):
                result[code] = meta.get("label", "")
    else:
        # Legacy: letters + digits, optional embedded race letter, ending in
        # digits.  Matches P001001 and P012A005; excludes errors like P012001ERR.
        legacy = re.compile(r'^[A-Z]+\d+[A-Z]?\d+$')
        for code, meta in data_vars.items():
            if legacy.match(code):
                result[code] = meta.get("label", "")

    return result


def fetch_variables(survey: str, vintage: int, group_code: str) -> dict[str, str]:
    """Return {var_code: label} for count variables in one group.

    Handles both modern (N-suffix) and legacy (numeric-only) variable formats.
    Geo/query pseudo-variables and annotation/error variables are excluded.
    """
    url = f"{BASE_URL}/{vintage}/{survey}/groups/{group_code}.json"
    try:
        data = _fetch_json(url)
    except urllib.error.HTTPError as exc:
        print(f"  [skip] {group_code} variables: HTTP {exc.code}", file=sys.stderr)
        return {}
    return _filter_variables(data.get("variables", {}))


def build_entry(
    survey: str,
    slug: str,
    vintage: int,
    group_meta: dict,
    variables: dict[str, str],
) -> dict:
    """Build a variable-group entry in the same shape as acs_variable_groups.json."""
    group_code = group_meta["name"]
    concept = group_meta.get("description", "").title()
    raw_universe = group_meta.get("universe", "")
    universe = _normalize_universe(raw_universe, group_code)

    return {
        "survey": survey,
        "slug": slug,
        "vintage": vintage,
        "group": group_code,
        "concept": concept,
        "universe": universe,
        "dimensions": None,           # populated later by build_dec_dimension_sets.py
        "dimensions_verified": False,
        "variables": variables,
    }


def make_key(slug: str, vintage: int, group_code: str) -> str:
    """Compound key: {slug}/{vintage}/{group} — uniquely identifies each (survey, year, group)."""
    return f"{slug}/{vintage}/{group_code}"


def main(dry_run: bool = False) -> None:
    output: dict[str, dict] = {}

    for survey, cfg in SURVEYS.items():
        slug = cfg["slug"]
        vintages = cfg["vintages"]
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Survey: {survey}")

        for vintage in vintages:
            print(f"  Vintage: {vintage}")
            groups = fetch_groups(survey, vintage)
            print(f"    {len(groups)} groups found")

            for group_meta in groups:
                group_code = group_meta["name"]
                print(f"    {group_code}: {group_meta.get('description','')[:60]}", end=" ")

                if not dry_run:
                    variables = fetch_variables(survey, vintage, group_code)
                    time.sleep(0.15)  # be polite to the Census API
                else:
                    variables = {}

                print(f"({len(variables)} vars)")

                key = make_key(slug, vintage, group_code)
                entry = build_entry(survey, slug, vintage, group_meta, variables)
                output[key] = entry

    print(f"\nTotal entries: {len(output)}")

    if not dry_run:
        OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Written → {OUT}")
    else:
        print("[DRY RUN] No file written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List groups without fetching variables or writing output.",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
