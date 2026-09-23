from unittest.mock import patch

import pandas as pd
import pytest

from morpc_census.geos import Scope, SumLevel, geoinfo_for_hierarchical_geos

# Place-county parts (155) require state and place, but place does not nest under county, so a county-based
# scope must find the places that intersect it, query each place's county parts, and keep the in-scope ones.

SCOPE = Scope(name="twocounty", for_param="county:041,049", in_param="state:39")

SCOPE_TABLE = pd.DataFrame({
    "GEO_ID": ["0500000US39041", "0500000US39049"],
    "state": ["39", "39"],
    "county": ["041", "049"],
})

# Columbus (18000) spans Delaware (041), Fairfield (045), and Franklin (049); Ashley (02582) is in Delaware only.
# 99999 is a place elsewhere in Ohio, which only the fallback's statewide place list includes.
PARTS = {"18000": ["041", "045", "049"], "02582": ["041"], "99999": ["003"]}


def _parts_table(place):
    geoids = [f"1550000US39{place}{county}" for county in PARTS[place]]
    return pd.DataFrame({"GEO_ID": geoids, "NAME": geoids})


def _fake_geoinfo(calls):
    def fake(param_dict, *args, **kwargs):
        calls.append(param_dict)
        if "ucgid" in param_dict:
            # A pseudo query lists a place once per county it intersects.
            geoids = ["1600000US3918000", "1600000US3918000", "1600000US3902582"]
            return pd.DataFrame({"GEO_ID": geoids, "NAME": geoids, "ucgid": geoids})
        if param_dict["for"] == "place:*":
            return pd.DataFrame({"place": ["18000", "02582", "99999"]})
        place = [p for p in param_dict["in"] if p.startswith("place:")][0].split(":")[1]
        return _parts_table(place)
    return fake


def _run(calls, pseudo_available=True):
    pseudo = (patch("morpc_census.geos.pseudos_from_scope_sumlevel", return_value=["x"]) if pseudo_available
              else patch("morpc_census.geos.pseudos_from_scope_sumlevel", side_effect=ValueError))
    with patch.object(SumLevel, "get_query_req", return_value={"requires": ["state", "place"], "wildcard": None}), \
         patch("morpc_census.geos.geoids_from_scope", return_value=SCOPE_TABLE), \
         patch("morpc_census.geos.geoinfo_from_params", side_effect=_fake_geoinfo(calls)), \
         pseudo:
        return geoinfo_for_hierarchical_geos(SCOPE, SumLevel("155"))


def _part_requests(calls):
    return [c for c in calls if c.get("for") == "county (or part):*"]


def test_place_county_parts_are_limited_to_scope_counties():
    calls = []
    result = _run(calls)
    # Fairfield (045) is outside the scope, so its part of Columbus is dropped.
    assert sorted(result["GEO_ID"]) == ["1550000US3902582041", "1550000US3918000041", "1550000US3918000049"]


def test_each_place_is_queried_once_with_a_single_place():
    calls = []
    _run(calls)
    places = [[p for p in c["in"] if p.startswith("place:")][0] for c in _part_requests(calls)]
    # The API rejects a list of places, and a place spanning two scope counties must not be queried twice.
    assert sorted(places) == ["place:02582", "place:18000"]


def test_fallback_without_pseudo_also_queries_single_places():
    calls = []
    result = _run(calls, pseudo_available=False)
    places = [[p for p in c["in"] if p.startswith("place:")][0] for c in _part_requests(calls)]
    assert all("," not in p for p in places)
    assert len(places) == len(set(places))
    assert sorted(result["GEO_ID"]) == ["1550000US3902582041", "1550000US3918000041", "1550000US3918000049"]
