"""Fetch universe descriptions from the Census API and insert them into acs_variable_groups.json."""

import json
import urllib.request
from pathlib import Path

GROUPS_URL = "https://api.census.gov/data/2024/acs/acs5/groups.json"
GROUPS_FILE = Path(__file__).parent / "acs_variable_groups.json"


def fetch_census_groups() -> dict[str, str]:
    """Return a mapping of group code -> universe string from the Census API."""
    with urllib.request.urlopen(GROUPS_URL) as resp:
        data = json.loads(resp.read())
    return {
        g["name"]: g.get("universe ", "")  # Census API key has a trailing space
        for g in data.get("groups", [])
        if g.get("name")
    }


def main() -> None:
    with open(GROUPS_FILE) as f:
        groups = json.load(f)

    census = fetch_census_groups()
    missing = []

    for code, entry in groups.items():
        universe = census.get(code, "")
        if not universe:
            missing.append(code)
        entry["universe"] = universe

    with open(GROUPS_FILE, "w") as f:
        json.dump(groups, f, indent=3)
    print(f"Updated {len(groups)} groups.")
    if missing:
        print(f"No universe found for {len(missing)} groups: {missing[:10]}{'...' if len(missing) > 10 else ''}")


if __name__ == "__main__":
    main()
