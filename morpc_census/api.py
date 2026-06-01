"""
Connects to the US Census Bureau API, retrieves survey data, and structures it
as long-format tables backed by frictionless metadata.

Census API root: https://api.census.gov/data/
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
from functools import cached_property
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from morpc_census.geos import Scope, SumLevel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain lookup tables (defined in constants.py, re-exported for backwards compat)
# ---------------------------------------------------------------------------

from morpc_census.constants import (  # noqa: E402
    HIGHLEVEL_GROUP_DESC,
    HIGHLEVEL_DESC_TO_ID,
    AGEGROUP_MAP,
    AGEGROUP_SORT_ORDER,
    RACE_TABLE_MAP,
    EDUCATION_ATTAIN_MAP,
    EDUCATION_ATTAIN_SORT_ORDER,
    INCOME_TO_POVERTY_MAP,
    INCOME_TO_POVERTY_SORT_ORDER,
    NTD_AGEMAP,
    NTD_AGEMAP_ORDER,
)

MISSING_VALUES = [
    "", "-222222222", "-333333333", "-555555555",
    "-666666666", "-888888888", "-999999999", "*****",
]
# Numeric equivalents — sentinel strings are coerced to floats/ints after the
# Census API pivot, so replacement in wide() must cover both forms.
_MISSING_VALUES_NUMERIC = [int(v) for v in MISSING_VALUES if v.lstrip("-").isdigit()]

VARIABLE_TYPES = {
    "E": "estimate",
    "M": "moe",
    "PE": "percent_estimate",
    "PM": "percent_moe",
    "N": "total",
}

# Schema field definitions for each value column type that can appear in LONG
_VALUE_FIELD_DEFS = {
    'estimate': {
        'name': 'estimate', 'type': 'number',
        'description': 'Estimate value for the variable',
    },
    'moe': {
        'name': 'moe', 'type': 'number',
        'description': 'Margin of error for the estimate',
    },
    'percent_estimate': {
        'name': 'percent_estimate', 'type': 'number',
        'description': 'Percent estimate value for the variable',
    },
    'percent_moe': {
        'name': 'percent_moe', 'type': 'number',
        'description': 'Margin of error for the percent estimate',
    },
    'total': {
        'name': 'total', 'type': 'number',
        'description': 'Total value for the variable',
    },
}

# Canonical order of column-MultiIndex levels in DimensionTable.wide().
# 'race' is only present in RaceDimensionTable; levels not in this list go last.
# The value-type level (None name, e.g. 'estimate'/'moe') is always appended last.
_WIDE_COL_LEVEL_ORDER = ['concept', 'universe', 'survey', 'geoidfq', 'name', 'race', 'reference_period']

# ---------------------------------------------------------------------------
# API discovery — fetched lazily so import does not make network calls
# ---------------------------------------------------------------------------

CENSUS_DATA_BASE_URL = 'https://api.census.gov/data'

IMPLEMENTED_ENDPOINTS = [
    'acs/acs1',
    'acs/acs1/profile',
    'acs/acs1/subject',
    'acs/acs5',
    'acs/acs5/profile',
    'acs/acs5/subject',
    'dec/pl',
    'dec/dhc',
    'dec/ddhca',
    'dec/ddhcb',
    'dec/sf1',
    'dec/sf2',
    'dec/sf3',
    'geoinfo',
]

@functools.cache
def get_all_avail_endpoints():
    """Return {endpoint: [vintage, ...]} for every dataset the Census API exposes.

    Result is cached after the first call so subsequent calls are free.
    """
    from morpc.req import get_json_safely
    kw = {'params': {'key': k}} if (k := _get_api_key()) else {}
    result = {}
    for dataset in get_json_safely(CENSUS_DATA_BASE_URL, **kw)['dataset']:
        if 'c_vintage' in dataset:
            endpoint = "/".join(dataset['c_dataset'])
            result.setdefault(endpoint, []).append(dataset['c_vintage'])
    return dict(sorted(result.items()))


@functools.cache
def _get_api_key() -> str | None:
    """Return CENSUS_API_KEY from environment, with .env file as fallback.

    Result is cached — the environment is read once per process. Set
    CENSUS_API_KEY before importing this module (or before the first API call).
    """
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
    return os.environ.get('CENSUS_API_KEY')


# ---------------------------------------------------------------------------
# Census API endpoint classes
# ---------------------------------------------------------------------------

class Endpoint:
    """A Census API survey at a specific vintage year (e.g. ``'acs/acs5'``, 2023).

    Validates the survey name against :data:`IMPLEMENTED_ENDPOINTS` and the year
    against the Census API's available vintages at construction.

    Parameters
    ----------
    survey : str
        Survey/table name, e.g. ``'acs/acs5'``, ``'dec/pl'``.
        See :data:`IMPLEMENTED_ENDPOINTS`.
    year : int
        Vintage year. Validated against the survey's available years.
    """

    def __init__(self, survey: str, year: int) -> None:
        if survey not in IMPLEMENTED_ENDPOINTS:
            raise ValueError(
                f"{survey!r} is not available or not yet implemented. "
                f"See IMPLEMENTED_ENDPOINTS."
            )
        self.survey = survey
        year = int(year)
        if year not in self.vintages:
            raise ValueError(
                f"{year} is not an available vintage for {self.survey!r}. "
                f"Available: {self.vintages}"
            )
        self.year = year

    def __repr__(self) -> str:
        return f"Endpoint({self.survey!r}, {self.year})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Endpoint)
            and self.survey == other.survey
            and self.year == other.year
        )

    def __hash__(self) -> int:
        return hash((self.survey, self.year))

    @cached_property
    def vintages(self) -> list[int]:
        """Available vintage years for this survey (fetched once, then cached)."""
        return get_all_avail_endpoints().get(self.survey, [])

    @property
    def url(self) -> str:
        """Base Census API query URL for this endpoint."""
        return f"{CENSUS_DATA_BASE_URL}/{self.year}/{self.survey}?"

    @cached_property
    def groups(self) -> dict:
        """All variable groups for this endpoint, keyed by group code (fetched once, then cached)."""
        from morpc.req import get_json_safely
        logger.debug(f"Fetching groups for {self.year} {self.survey}")
        kw = {'params': {'key': k}} if (k := _get_api_key()) else {}
        data = get_json_safely(
            f"{CENSUS_DATA_BASE_URL}/{self.year}/{self.survey}/groups.json", **kw
        )
        return dict(sorted({
            g['name']: {
                'description': g['description'],
                'variables': g['variables'],
                'universe': g.get('universe ', '').strip(),  # trailing space in Census API response
            }
            for g in data['groups']
        }.items()))
    
    def search_groups(self, search) -> dict:
        """Search groups for term or string"""
        return {k: v['description'] for k, v in self.groups.items() if search in v['description'].lower()}


class Group:
    """A variable group within a Census API endpoint (e.g. ``'B01001'``).

    Parameters
    ----------
    endpoint : Endpoint
        The survey endpoint this group belongs to.
    code : str
        Group code (e.g. ``'B01001'``). Case-insensitive; stored upper-cased.
        Validated against :attr:`Endpoint.groups` at construction.
    """

    def __init__(self, endpoint: Endpoint, code: str) -> None:
        if not isinstance(endpoint, Endpoint):
            raise TypeError(
                f"endpoint must be an Endpoint instance, got {type(endpoint).__name__!r}."
            )
        self.endpoint = endpoint
        code = code.upper()
        if code not in self.endpoint.groups:
            raise ValueError(
                f"{code!r} is not a valid group in "
                f"{self.endpoint.survey!r} {self.endpoint.year}."
            )
        self.code = code

    def __repr__(self) -> str:
        return f"Group({self.endpoint.survey!r}, {self.endpoint.year}, {self.code!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Group)
            and self.endpoint == other.endpoint
            and self.code == other.code
        )

    def __hash__(self) -> int:
        return hash((self.endpoint.survey, self.endpoint.year, self.code))

    @property
    def description(self) -> str:
        """Group description (read from :attr:`Endpoint.groups` — no extra network call)."""
        return self.endpoint.groups[self.code]['description']

    @property
    def universe(self) -> str:
        """Universe description string (read from :attr:`Endpoint.groups` — no extra network call)."""
        return self.endpoint.groups[self.code].get('universe', '')

    @cached_property
    def variables(self) -> dict:
        """Variable metadata dict for this group (fetched once, then cached)."""
        from morpc.req import get_json_safely
        kw = {'params': {'key': k}} if (k := _get_api_key()) else {}
        data = get_json_safely(
            f"{CENSUS_DATA_BASE_URL}/{self.endpoint.year}/{self.endpoint.survey}"
            f"/groups/{self.code}.json",
            **kw,
        )
        return {
            k: data['variables'][k]
            for k in sorted(data['variables'])
            if k not in ('GEO_ID', 'NAME')
        }

    @cached_property
    def concept_dims(self) -> dict[str, str]:
        """Human-readable names for each ``dim_N`` column produced by :class:`DimensionTable`.

        Checks ``dim_names.json`` for a curated override first; falls back to
        auto-inference from the concept string when no override exists.

        Returns
        -------
        dict[str, str]
            E.g. ``{"dim_0": "Total", "dim_1": "Sex", "dim_2": "Age"}``.
        """
        overrides = _load_dim_names_json()
        if self.code in overrides:
            return overrides[self.code]
        return _infer_dim_names(self)

    @cached_property
    def dim_names(self) -> list[str]:
        """Ordered list of human-readable dimension names for :class:`DimensionTable`.

        Returns
        -------
        list[str]
            E.g. ``["Total", "Sex", "Age"]``. Directly usable as the
            ``dim_names`` parameter of :class:`DimensionTable`.
        """
        cd = self.concept_dims
        return [cd[k] for k in sorted(cd, key=lambda k: int(k.split("_")[1]))]


# ---------------------------------------------------------------------------
# Dim-naming helpers
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _load_dim_names_json() -> dict:
    path = Path(__file__).parent / "dim_names.json"
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


@functools.lru_cache(maxsize=1)
def _load_dims_json() -> dict:
    path = Path(__file__).parent / "dims.json"
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


@functools.lru_cache(maxsize=1)
def _load_group_dims_json() -> dict:
    path = Path(__file__).parent / "group_dims.json"
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _match_col_names(dims_df: "pd.DataFrame", group_code: str) -> list:
    """Match parsed dim columns to human-readable names via dims.json + group_dims.json.

    For each column, scores every candidate dim ID (from group_dims.json) by
    Jaccard similarity between the column's unique non-empty values and the
    dim's variable list in dims.json.  Returns a list parallel to the columns;
    unmatched positions are None (caller falls back to 'dim_N').
    """
    dims_data = _load_dims_json()
    group_dims_data = _load_group_dims_json()
    dim_ids = group_dims_data.get(group_code, [])
    if not dims_data or not dim_ids:
        return []

    used: set = set()
    result: list = []
    for col in dims_df.columns:
        col_vals = {v for v in dims_df[col].unique() if v}
        best_id, best_score = None, -1.0
        for dim_id in dim_ids:
            if dim_id in used:
                continue
            dim_vars = set(dims_data.get(dim_id, {}).get("variables", []))
            if not dim_vars:
                continue
            union = col_vals | dim_vars
            score = len(col_vals & dim_vars) / len(union) if union else 0.0
            if score > best_score:
                best_score, best_id = score, dim_id
        if best_id and best_score > 0:
            used.add(best_id)
            name = re.sub(r'\s*\([^)]*\)', '', dims_data[best_id]["name"]).strip()
            result.append(name)
        else:
            result.append(None)
    return result


def _build_group_label_df(group: Group) -> pd.DataFrame:
    """Build a minimal ``variable``/``variable_label`` DataFrame from group metadata.

    Uses only :attr:`Group.variables` (the groups JSON endpoint) — no census
    data fetch needed.  Only E (estimate) codes are included since DimensionTable
    only needs one label per variable code.
    """
    rows = []
    for code, meta in group.variables.items():
        if not code.endswith("E"):
            continue
        label = meta.get("label", "")
        if not label.startswith("Estimate!!"):
            continue
        rows.append({"variable": code[:-1], "variable_label": label[len("Estimate!!"):]})
    if not rows:
        return pd.DataFrame(columns=["variable", "variable_label"])
    return pd.DataFrame(rows).drop_duplicates(subset="variable")


def _infer_dim_names_from_dims(dims: "pd.DataFrame", concept: str) -> dict[str, str]:
    """Core inference: map dim columns to human names given the dims DataFrame and concept string."""
    n_dims = len(dims.columns)

    concept_clean = re.sub(r"\s*\([^)]*\)", "", concept).strip()
    def _cap(s: str) -> str:
        s = s.strip()
        return s[:1].upper() + s[1:] if s else s

    parts = [_cap(p) for p in concept_clean.split(" by ")]

    if len(parts) == n_dims:
        return {f"dim_{i}": part for i, part in enumerate(parts)}

    root_map: dict[str, str] = {}
    non_roots: list[str] = []
    for col in dims.columns:
        unique_vals = [
            str(v).rstrip(":").strip()
            for v in dims[col].cat.categories
            if str(v).strip(":").strip()
        ]
        if len(unique_vals) == 1:
            root_map[col] = unique_vals[0]
        else:
            non_roots.append(col)

    if len(parts) == len(non_roots):
        result = {**root_map}
        for col, part in zip(non_roots, parts):
            result[col] = part
        return result

    result = {**root_map}
    for i, col in enumerate(non_roots):
        result[col] = f"Dim {i + 1}"
    return result


def _infer_dim_names(group: Group) -> dict[str, str]:
    label_df = _build_group_label_df(group)
    if label_df.empty:
        return {}
    dt = DimensionTable(label_df)
    return _infer_dim_names_from_dims(dt.dims, group.description)


def get_concept_dims_from_long(long_df: "pd.DataFrame") -> dict[str, str]:
    """Derive dim names directly from a long DataFrame (no ``Group`` object needed).

    Extracts the group code and concept from the DataFrame's ``variable`` and
    ``concept`` columns, checks the curated ``dim_names.json`` override, and
    falls back to the same auto-inference algorithm used by
    :attr:`Group.concept_dims`.

    Parameters
    ----------
    long_df : pd.DataFrame
        Output of ``CensusAPI.long`` (must contain ``variable`` and ``concept``
        columns).

    Returns
    -------
    dict[str, str]
        E.g. ``{"dim_0": "Total", "dim_1": "Sex", "dim_2": "Age"}``.
    """
    if long_df.empty:
        return {}

    group_code = ""
    if "variable" in long_df.columns:
        m = re.match(r"^([A-Z][A-Z0-9]+)_", str(long_df["variable"].iloc[0]))
        if m:
            group_code = m.group(1)

    overrides = _load_dim_names_json()
    if group_code in overrides:
        return overrides[group_code]

    concept = str(long_df["concept"].iloc[0]) if "concept" in long_df.columns else ""
    dt = DimensionTable(long_df)
    return _infer_dim_names_from_dims(dt.dims, concept)


def get_dim_variables(group: Group) -> dict[str, list[str]]:
    """Return ordered unique values for each named dimension in *group*.

    Keys are the human-readable dim names from :attr:`Group.concept_dims`;
    values are the unique non-empty category values for that dim column
    (colon-stripped, in Census-defined order).

    Parameters
    ----------
    group : Group

    Returns
    -------
    dict[str, list[str]]
        E.g. ``{"Total": ["Total"], "Sex": ["Male", "Female"], "Age": [...]}``.
    """
    label_df = _build_group_label_df(group)
    if label_df.empty:
        return {}

    dt = DimensionTable(label_df)
    names = group.concept_dims

    result: dict[str, list[str]] = {}
    for col in dt.dims.columns:
        dim_name = names.get(col, col)
        cats = [
            str(v).rstrip(":").strip()
            for v in dt.dims[col].cat.categories
            if str(v).strip(":").strip()
        ]
        result[dim_name] = cats
    return result


# ---------------------------------------------------------------------------
# Naming helper
# ---------------------------------------------------------------------------

def censusapi_name(endpoint: Endpoint, scope: str | Scope, group: str | Group | None = None, sumlevel: str | SumLevel | None = None, variables: list[str] | None = None) -> str:
    """Construct a canonical, machine-readable name for a CensusAPI dataset."""
    from morpc_census.geos import Scope as _Scope, SumLevel as _SumLevel

    scope_name = scope.name if isinstance(scope, _Scope) else scope
    group_code = group.code if isinstance(group, Group) else group  # None when no group

    if sumlevel is not None:
        sl = sumlevel if isinstance(sumlevel, _SumLevel) else _SumLevel(sumlevel)
        sumlevel_part = f"{(sl.hierarchy_string or sl.name).replace('-', '').lower()}-"
    else:
        sumlevel_part = ''

    group_part = f"-{group_code}" if group_code is not None else ''
    var_part = '-select-variables' if variables is not None else ''
    return (
        f"census-{endpoint.survey.replace('/', '-')}-{endpoint.year}"
        f"-{sumlevel_part}{scope_name}{group_part}{var_part}"
    ).lower()


# ---------------------------------------------------------------------------
# Variable-mapping helper (used by DimensionTable)
# ---------------------------------------------------------------------------

def find_replace_variable_map(labels: list[str], variables: list[str], label_map: dict) -> tuple[list[str], list[str]]:
    """Apply label substitutions and return updated labels and new sequential variable codes."""
    labels = list(labels)
    variables = list(variables)

    new_labels = [
        next(
            (label.replace(key, val) for key, val in label_map.items() if key in label),
            label,
        )
        for label in labels
    ]

    var_id = variables[0].split('_')[0]
    variable_map = {}
    for i, label in enumerate(new_labels):
        if label not in variable_map:
            variable_map[label] = f"{var_id}_M{len(variable_map):02d}"

    new_variables = [variable_map[label] for label in new_labels]
    return new_labels, new_variables


# ---------------------------------------------------------------------------
# CensusAPI
# ---------------------------------------------------------------------------

class CensusAPI:
    """Fetches Census API survey data and exposes it as a long-format DataFrame.

    Parameters
    ----------
    endpoint : Endpoint
        Survey and vintage year, e.g. ``Endpoint('acs/acs5', 2023)``.
    scope : str or Scope
        Geographic scope key (e.g. ``'region15'``) or a ``Scope`` instance.
        See ``morpc_census.geos.SCOPES`` for available keys.
    group : str, Group, or None
        Variable group code, e.g. ``'B01001'``. Required if *variables* is
        not provided. When omitted, *variables* must be given and are fetched
        directly without group validation.
    sumlevel : str or SumLevel, optional
        Geographic summary level query name (e.g. ``'county'``, ``'tract'``)
        or a ``SumLevel`` instance.  See ``morpc_census.geos.SumLevel``.
    variables : list of str, optional
        Specific variables to retrieve. Required when *group* is not provided.
        When both are given, variables must be a subset of the group's variables.
    return_long : bool
        If ``True`` (default) compute ``self.long`` immediately after fetch.

    Examples
    --------
    Fetch ACS 5-year age/sex data for counties in the 15-county MORPC region:

    >>> from morpc_census import Endpoint, Group, CensusAPI, SCOPES, SumLevel  # doctest: +SKIP
    >>> ep = Endpoint('acs/acs5', 2023)                                         # doctest: +SKIP
    >>> grp = Group(ep, 'B01001')                                               # doctest: +SKIP
    >>> api = CensusAPI(ep, SCOPES['region15'], group=grp, sumlevel=SumLevel('county'))  # doctest: +SKIP
    >>> api.long.head()                                                         # doctest: +SKIP

    Fetch specific variables without a group:

    >>> api = CensusAPI(ep, 'franklin', variables=['B01001_001E', 'B01001_002E'])  # doctest: +SKIP
    """

    def __init__(
        self,
        endpoint: Endpoint,
        scope: str | Scope,
        group: str | Group | None = None,
        sumlevel: str | SumLevel | None = None,
        variables: list[str] | None = None,
        return_long: bool = True,
    ):
        if group is None and variables is None:
            raise ValueError("At least one of 'group' or 'variables' must be provided.")

        from morpc_census.geos import Scope as _Scope, SumLevel as _SumLevel

        self.scope = scope if isinstance(scope, _Scope) else _Scope(scope.lower())
        self.sumlevel = (
            None if sumlevel is None
            else sumlevel if isinstance(sumlevel, _SumLevel)
            else _SumLevel(sumlevel.lower())
        )
        self.variables = (
            [v.upper() for v in variables] if variables is not None else None
        )

        if group is not None:
            self.group = group if isinstance(group, Group) else Group(endpoint, group.upper())
        else:
            self.group = None
        self.endpoint = self.group.endpoint if self.group is not None else endpoint

        if self.variables is not None and self.group is not None:
            invalid = [v for v in self.variables if v not in self.group.variables]
            if invalid:
                raise ValueError(f"Variables not found in {self.group.code}: {invalid}")

        self.logger = (
            logging.getLogger(__name__)
            .getChild(self.__class__.__name__)
            .getChild(self.name)
        )
        self.logger.info(f"Initializing CensusAPI for {self.name}.")

        self.logger.info("Building request URL and parameters.")
        self.request = self._build_request()

        self.logger.info(
            f"Fetching data from {self.request['url']} "
            f"with params {self.request['params']}."
        )
        try:
            self.data = self._fetch()
        except RuntimeError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to retrieve data: {e}")
            raise RuntimeError("Failed to retrieve data from Census API.") from e

        n_dupes = self.data.duplicated().sum()
        if n_dupes:
            self.logger.warning(
                f"Removing {n_dupes} duplicate rows "
                "(can occur when ucgid=pseudo() is used for geographies)."
            )
            self.data = self.data.loc[~self.data.duplicated()].reset_index(drop=True)

        if return_long:
            self.long = self.melt()

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @cached_property
    def universe(self) -> str:
        """Universe description string. Falls back to 2023 vintage when year < 2023."""
        if self.group is None:
            return 'Not defined — no group specified'
        try:
            source = (
                self.group if self.endpoint.year >= 2023
                else Group(Endpoint(self.endpoint.survey, 2023), self.group.code)
            )
            return source.universe
        except Exception as e:
            self.logger.warning(
                f"Universe not defined for {self.endpoint.survey}/{self.group.code}: {e}"
            )
            return 'Not defined in API — see CensusAPI.request for endpoint details'

    @cached_property
    def vars(self) -> dict:
        """Variable metadata dict including label. When group is set, fetches via the group
        endpoint and respects the variables filter. Without a group, infers the group code
        from each variable name (e.g. B01001_001E → B01001) and fetches the group metadata
        to obtain human-readable labels."""
        if self.group is not None:
            all_vars = dict(self.group.variables)
            if self.variables is not None:
                return {k: v for k, v in all_vars.items() if k in self.variables}
            return all_vars

        from morpc.req import get_json_safely
        kw = {'params': {'key': k}} if (k := _get_api_key()) else {}

        # Group variable codes by their table prefix (B01001_001E → B01001)
        group_map: dict[str, list[str]] = {}
        for v in self.variables:
            m = re.match(r'^([A-Z][A-Z0-9]+)_\d+', v)
            if m:
                group_map.setdefault(m.group(1), []).append(v)

        result: dict[str, dict] = {}
        for gc, gc_vars in group_map.items():
            try:
                data = get_json_safely(
                    f"{CENSUS_DATA_BASE_URL}/{self.endpoint.year}"
                    f"/{self.endpoint.survey}/groups/{gc}.json",
                    **kw,
                )
                gvars = data.get('variables', {})
                for v in gc_vars:
                    result[v] = gvars.get(v, {})
            except Exception:
                for v in gc_vars:
                    result[v] = {}

        for v in self.variables:
            result.setdefault(v, {})
        return result

    @cached_property
    def name(self) -> str:
        """Canonical, machine-readable dataset name."""
        return self._build_name()

    @property
    def geoidfqs(self):
        """Return the GEO_ID column parsed as a list of GeoIDFQ objects."""
        from morpc_census.geos import GeoIDFQ
        return [GeoIDFQ.parse(g) for g in self.data['GEO_ID']]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_name(self) -> str:
        return censusapi_name(
            self.endpoint,
            self.scope,
            self.group,
            sumlevel=self.sumlevel,
            variables=self.variables,
        )

    def _build_request(self) -> dict:
        """Build the Census API request dict from already-normalized instance attributes."""
        from morpc_census.geos import geoinfo_from_scope_sumlevel
        get_param = (
            ','.join(self.variables) if self.variables is not None
            else f"group({self.group.code})"
        )
        geo_param = geoinfo_from_scope_sumlevel(self.scope, self.sumlevel, output='params')
        params = {'get': get_param}
        params.update(geo_param)
        return {'url': self.endpoint.url, 'params': params}

    def _fetch(self) -> pd.DataFrame:
        """Dispatch to the group or variable-list fetch path and return raw data."""
        key = _get_api_key()
        params = dict(self.request['params'])
        if key:
            params['key'] = key
        url = self.request['url']
        if self.group is not None:
            return self._fetch_group(url, params)
        return self._fetch_variables(url, params)

    def _fetch_group(self, url: str, params: dict) -> pd.DataFrame:
        """Fetch all variables in a group using the Census API's group() query form.

        The group() form returns all variables at once without a variable-count limit,
        but the response is a flat text stream rather than JSON.
        """
        from morpc.req import get_text_safely

        self.logger.info(f"Fetching group({self.group.code}) — all variables, no limit.")
        params_string = "&".join(f"{k}={v}" for k, v in params.items())
        text = get_text_safely(f"{url}{params_string}")
        try:
            df = pd.read_csv(
                StringIO(text.replace('[', '').replace(']', '').rstrip(',')),
                sep=',', quotechar='"',
            )
            return df.drop(columns=[c for c in df.columns if c.startswith('Unnamed')])
        except Exception as e:
            self.logger.error(f"Failed to parse group response: {e}")
            raise RuntimeError("Failed to parse Census API group response.") from e

    def _fetch_variables(self, url: str, params: dict) -> pd.DataFrame:
        """Fetch a specific variable list, batching into chunks of 49.

        The Census API allows at most 50 fields per request; GEO_ID occupies one slot,
        leaving 49 for data variables. Each batch includes GEO_ID for row alignment,
        and all batch results are joined on GEO_ID into a single DataFrame.
        """
        from morpc.req import get_json_safely

        BATCH_SIZE = 48
        variables = self.variables
        batches = [variables[i:i + BATCH_SIZE] for i in range(0, len(variables), BATCH_SIZE)]
        self.logger.info(
            f"Fetching {len(variables)} variable(s) in {len(batches)} batch(es)."
        )

        frames = []
        for i, batch in enumerate(batches, 1):
            self.logger.info(f"Batch {i}/{len(batches)}: {len(batch)} variable(s).")
            batch_params = {**params, 'get': ','.join(['GEO_ID', 'NAME'] + batch)}
            records = get_json_safely(url, params=batch_params)
            columns = records.pop(0)
            frames.append(
                pd.DataFrame.from_records(records, columns=columns).set_index(['GEO_ID', 'NAME'])
            )

        result = frames[0] if len(frames) == 1 else frames[0].join(frames[1:])
        return result.reset_index()

    # ------------------------------------------------------------------
    # Data transformation
    # ------------------------------------------------------------------

    def melt(self):
        """Transform the wide API response into a long-format DataFrame.

        Returns
        -------
        pandas.DataFrame
            Columns: geoidfq, [name,] reference_period, survey, concept,
            universe, variable_label, variable, and one or more of
            estimate / moe / percent_estimate / percent_moe / total.
        """
        self.logger.info("Melting data to long format.")
        has_name = 'NAME' in self.data.columns
        id_cols = ['geoidfq', 'name'] if has_name else ['geoidfq']
        long = self._melt_wide_to_long(has_name)
        long = self._attach_dataset_metadata(long, id_cols)
        pivot_index = id_cols + [
            'reference_period', 'survey', 'concept', 'universe',
            'variable_label', 'variable',
        ]
        return self._pivot_and_coerce(long, pivot_index)

    def _melt_wide_to_long(self, has_name: bool) -> pd.DataFrame:
        """Steps 1-4: melt wide data, filter, label, and parse variable codes."""
        geo_cols = ['GEO_ID', 'NAME'] if has_name else ['GEO_ID']
        long = (
            self.data
            .melt(id_vars=geo_cols, var_name='variable', value_name='value')
            .rename(columns={'GEO_ID': 'geoidfq', 'NAME': 'name'})
        )
        # Drop non-data columns (state/county/annotation codes)
        long = long.loc[long['variable'].isin(self.vars)]
        # Human-readable label — strip leading "Estimate!!" prefix
        labels = long['variable'].map(lambda v: self.vars.get(v, {}).get('label', v))
        long['variable_label'] = labels.str.split('!!', n=1).str[-1]
        # Parse base code and value type: B01001_001E → base='B01001_001', type='E'
        parsed = long['variable'].str.extract(r'^([A-Z0-9_]+[0-9]+)([A-Z]{1,2})$')
        long['variable'] = parsed[0].fillna(long['variable'])
        long['variable_type'] = parsed[1].map(VARIABLE_TYPES)
        return long.loc[long['variable_type'].notna()]

    def _attach_dataset_metadata(self, long: pd.DataFrame, id_cols: list[str]) -> pd.DataFrame:
        """Step 5: attach reference_period, survey, universe, and concept columns."""
        long['reference_period'] = self.endpoint.year
        long['survey'] = self.endpoint.survey
        if self.group is not None:
            long['universe'] = self.universe
            long['concept'] = self.group.description.capitalize()
        else:
            # Variables-only mode: derive concept and universe per variable from self.vars.
            # self.vars keys carry the type suffix (e.g. B17024_001E); long['variable']
            # is already the base code (e.g. B17024_001) after _melt_wide_to_long.
            _concept_map: dict[str, str] = {}
            for k, meta in self.vars.items():
                m = re.match(r'^([A-Z0-9_]+[0-9]+)[A-Z]{1,2}$', k)
                if m:
                    _concept_map.setdefault(m.group(1), meta.get('concept', ''))
            long['concept'] = long['variable'].map(
                lambda v: _concept_map.get(v, '').capitalize()
            )
            long['_gc'] = long['variable'].str.extract(r'^([A-Z][A-Z0-9]+)_')[0]
            gc_universe = {
                gc: self.endpoint.groups.get(gc, {}).get('universe', '')
                for gc in long['_gc'].dropna().unique()
            }
            long['universe'] = long.pop('_gc').map(gc_universe).fillna('')
        return long

    def _pivot_and_coerce(self, long: pd.DataFrame, pivot_index: list[str]) -> pd.DataFrame:
        """Steps 6-7: pivot variable_type into columns and coerce values to numeric."""
        try:
            long = (
                long.pivot(index=pivot_index, columns='variable_type', values='value')
                .reset_index()
                .rename_axis(None, axis=1)
            )
        except ValueError as e:
            self.logger.error(f"Pivot failed: {e}")
            raise
        long = long.sort_values(by=['geoidfq', 'variable', 'reference_period'])
        for col in [c for c in long.columns if c in VARIABLE_TYPES.values()]:
            long[col] = pd.to_numeric(
                long[col].where(~long[col].isin(MISSING_VALUES), other=np.nan),
                errors='coerce',
            )
        return long

    # ------------------------------------------------------------------
    # Frictionless metadata
    # ------------------------------------------------------------------

    def define_schema(self):
        """Build a frictionless Schema for the long-format data.

        Returns
        -------
        frictionless.Schema
        """
        import frictionless

        if not hasattr(self, 'long'):
            raise RuntimeError(
                "define_schema() requires long data. "
                "Construct with return_long=True or call melt() first."
            )

        self.logger.info(
            f"Defining schema for {self.endpoint.survey} / {self.endpoint.year}"
            + (f" / {self.group.code}" if self.group is not None else '') + "."
        )

        fixed_fields = [
            {'name': 'geoidfq',          'type': 'string',  'description': 'Census geography fully-qualified identifier'},
            {'name': 'reference_period', 'type': 'integer', 'description': 'Reference year'},
            {'name': 'survey',           'type': 'string',  'description': 'Census survey endpoint'},
            {'name': 'concept',          'type': 'string',  'description': 'Table concept description'},
            {'name': 'universe',         'type': 'string',  'description': 'Universe for the table'},
            {'name': 'variable_label',   'type': 'string',  'description': 'Human-readable variable label'},
            {'name': 'variable',         'type': 'string',  'description': 'Base variable code'},
        ]
        if 'name' in self.long.columns:
            fixed_fields.insert(1, {'name': 'name', 'type': 'string', 'description': 'Geography name'})

        fixed_names = {f['name'] for f in fixed_fields}
        value_fields = []
        for col in self.long.columns:
            if col in fixed_names:
                continue
            if col not in _VALUE_FIELD_DEFS:
                raise ValueError(f"Unexpected column {col!r} in long data.")
            value_fields.append(_VALUE_FIELD_DEFS[col])

        descriptor = {
            'fields': fixed_fields + value_fields,
            'missingValues': MISSING_VALUES,
            'primaryKey': ['geoidfq', 'reference_period', 'variable'],
        }
        result = frictionless.Schema.validate_descriptor(descriptor)
        if not result.valid:
            raise ValueError(f"Schema descriptor invalid: {result}")

        return frictionless.Schema.from_descriptor(descriptor)

    def create_resource(self):
        """Build a frictionless Resource for this dataset.

        Requires :meth:`save` to have been called first (or for
        ``self.schema_filename`` and ``self.filename`` to be set).

        Returns
        -------
        frictionless.Resource
        """
        import frictionless

        year, survey, scope = self.endpoint.year, self.endpoint.survey, self.scope.name
        sumlevel_prefix = f'{self.sumlevel.plural} in ' if self.sumlevel is not None else ''

        if self.group is not None:
            subject = f"{self.group.code}: {self.group.description}"
            title = f"{year} {self.group.description} for {sumlevel_prefix}{scope}"
        else:
            vars_summary = ', '.join(self.variables[:3])
            if len(self.variables) > 3:
                vars_summary += f', ... ({len(self.variables)} total)'
            subject = vars_summary
            title = f"{year} selected variables for {sumlevel_prefix}{scope}"

        return frictionless.Resource.from_descriptor({
            'name': self.name,
            'title': title,
            'description': (
                f"Census API data for {subject} from {survey} {year} "
                f"for {sumlevel_prefix}{scope}."
            ),
            'path': self.filename,
            'schema': self.schema_filename,
            'sources': [{'title': 'US Census Bureau API', 'path': self.request['url'], '_params': self.request['params']}],
        })

    def save(self, output_path):
        """Write data, schema, and resource files to *output_path*.

        Produces three files:
        - ``{name}.long.csv``       — long-format data
        - ``{name}.schema.yaml``    — frictionless schema
        - ``{name}.resource.yaml``  — frictionless resource descriptor

        Parameters
        ----------
        output_path : str or path-like
        """
        import contextlib
        import frictionless

        if not hasattr(self, 'long'):
            raise RuntimeError(
                "save() requires long data. "
                "Construct with return_long=True or call melt() first."
            )

        output = Path(output_path)
        output.mkdir(parents=True, exist_ok=True)

        self.datapath        = output
        self.filename        = f"{self.name}.long.csv"
        self.schema_filename = f"{self.name}.schema.yaml"
        resource_filename    = f"{self.name}.resource.yaml"

        self.logger.info(f"Writing data to {output / self.filename}.")
        self.long.to_csv(output / self.filename, index=False)

        self.logger.info(f"Writing schema to {output / self.schema_filename}.")
        self.schema = self.define_schema()
        self.schema.to_yaml(str(output / self.schema_filename))

        # frictionless resolves resource paths relative to CWD
        self.logger.info(f"Writing resource to {output / resource_filename}.")
        resource = self.create_resource()
        with contextlib.chdir(output):
            resource.to_yaml(resource_filename)
            result = frictionless.Resource(resource_filename).validate()

        if not result.valid:
            self.logger.error(f"Resource validation failed: {result.stats}")
            raise RuntimeError("Resource validation failed after save.")

        self.logger.info("Save complete and resource validated.")


# ---------------------------------------------------------------------------
# DimensionTable
# ---------------------------------------------------------------------------

class DimensionTable:
    """Creates wide and percentage tables from CensusAPI long-format data.

    Parameters
    ----------
    long_data : pandas.DataFrame
        The ``long`` DataFrame produced by :class:`CensusAPI`.
    dim_names : list of str, optional
        Names for the dimension columns parsed from ``variable_label``.
        Auto-named ``dim_0``, ``dim_1``, … when omitted.
    group : Group, optional
        When provided and ``dim_names`` is not given, ``group.dim_names`` is
        used to name the dimension columns.
    """

    def __init__(self, long_data, dim_names=None, group=None):
        self.logger = logging.getLogger(__name__).getChild(self.__class__.__name__)
        self.long = long_data.copy()

        self.value_cols = [
            c for c in self.long.columns
            if c not in (
                'concept', 'universe', 'survey', 'geoidfq', 'name',
                'reference_period', 'variable_label', 'variable',
            )
        ]

        if dim_names is None and group is not None:
            dim_names = group.dim_names

        self.dims = self._parse_dims(dim_names)

    def _parse_dims(self, dim_names=None):
        """Parse ``variable_label`` into a structured dimension DataFrame.

        Each ``!!``-delimited label is split into segments (``':'`` subtotal
        markers are stripped), then shifted rightward so that any value that
        appears at multiple depths across the group always lands in the same
        column.  Empty cells are stored as ``''`` so that ``drop()`` can
        identify rows that are already aggregated over a dimension.

        Returns
        -------
        pandas.DataFrame
            Index = ``variable``, columns = dimension names.
        """
        # One label per variable code — different vintages may use the same
        # code with slightly different label text (e.g. trailing ':' present
        # or absent); keep only the first occurrence.  Sort by variable code
        # first so category order matches Census-defined variable order
        # regardless of how rows arrive (e.g. from a DB cache).
        unique = (self.long[['variable', 'variable_label']]
                  .sort_values('variable')
                  .drop_duplicates(subset='variable')
                  .set_index('variable')
                  .copy())

        def segments(label):
            return [s.rstrip(':').strip() for s in label.split('!!') if s.rstrip(':').strip()]

        seg_series = unique['variable_label'].map(segments)
        width = seg_series.map(len).max() if len(seg_series) else 0
        ncols = width + 2  # buffer so the shift loop has room to expand

        rows = [segs + [None] * (ncols - len(segs)) for segs in seg_series]

        # Shift each value rightward until it no longer appears in any column
        # to its right, aligning values that vary in depth across the tree.
        if rows:
            max_cols = ncols * 2
            changed = True
            while changed:
                changed = False
                right_of = [set() for _ in range(ncols)]
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

        dims = pd.DataFrame(rows, index=unique.index)
        dims = dims.loc[:, dims.notna().any(axis=0)]
        dims = dims.where(dims.notna(), '')

        if dim_names is None:
            group_code = ''
            if 'variable' in self.long.columns and len(self.long):
                m = re.match(r'^([A-Z][A-Z0-9]+)_', str(self.long['variable'].iloc[0]))
                if m:
                    group_code = m.group(1)
            if group_code:
                dim_names = _match_col_names(dims, group_code)

        named = list(dim_names or [])
        n = len(dims.columns)
        raw_names = [
            named[i] if i < len(named) and named[i] is not None else f'dim_{i}'
            for i in range(n)
        ]
        # Deduplicate: if two columns would share a name, append a counter suffix
        # to the second and subsequent occurrences so pandas never gets duplicate
        # column names (which break Series access and corrupt Categorical conversion).
        seen_names: dict = {}
        deduped: list = []
        for name in raw_names:
            if name in seen_names:
                seen_names[name] += 1
                deduped.append(f"{name} {seen_names[name]}")
            else:
                seen_names[name] = 1
                deduped.append(name)
        dims.columns = deduped

        # Convert each column to an ordered categorical using first-appearance
        # order (Census variables are returned in their defined order).
        for col in dims.columns:
            seen = list(dict.fromkeys(dims[col]))
            dims[col] = pd.Categorical(dims[col], categories=seen, ordered=True)

        return dims

    def remap(self, variable_map):
        """Apply variable label substitutions and aggregate collapsed rows.

        Parameters
        ----------
        variable_map : dict
            Label substrings to replace; see :func:`find_replace_variable_map`.

        Returns
        -------
        self
        """
        self.logger.info(f"Remapping variables: {list(variable_map)}.")
        self.long['variable_label'], self.long['variable'] = find_replace_variable_map(
            self.long['variable_label'], self.long['variable'], label_map=variable_map
        )

        group_cols = [c for c in self.long.columns if c not in self.value_cols]
        long_work = self.long.copy()
        agg_dict = {}

        if 'moe' in self.value_cols:
            long_work['_moe_sq'] = pd.to_numeric(long_work['moe'], errors='coerce') ** 2
            agg_dict['_moe_sq'] = 'sum'

        for col in self.value_cols:
            if col != 'moe':
                long_work[col] = pd.to_numeric(long_work[col], errors='coerce')
                agg_dict[col] = 'sum'

        self.long = long_work.groupby(group_cols, observed=True).agg(agg_dict).reset_index()

        if 'moe' in self.value_cols:
            self.long['moe'] = np.sqrt(self.long['_moe_sq'])
            self.long = self.long.drop(columns=['_moe_sq'])

        self.dims = self._parse_dims(dim_names=list(self.dims.columns))
        return self

    def _has_partial_subtotals(self, dim: str) -> bool:
        """Return True if pre-computed partial subtotal rows exist for *dim*.

        A partial subtotal row is one where ``dims[dim] == ''`` (already
        aggregated across *dim*) but at least one sibling dimension carries a
        specific non-empty value — meaning the Census data contains results
        pre-summed over *dim* for particular slices of the other dimensions.

        Rows where *every* dimension is ``''`` are the grand total and do not
        qualify as partial subtotals.  A sibling dimension must have 2+ globally
        distinct non-empty values to be counted; this rules out trivial "Total:"
        root columns that appear identically in every row.
        """
        if dim not in self.dims.columns:
            return False
        other_dims = [d for d in self.dims.columns if d != dim]
        if not other_dims:
            return False
        subtotal_rows = self.dims.loc[self.dims[dim] == '']
        if subtotal_rows.empty:
            return False
        for other in other_dims:
            non_empty_here = subtotal_rows[other][subtotal_rows[other] != '']
            globally_non_empty = self.dims[other][self.dims[other] != '']
            if len(non_empty_here) > 0 and globally_non_empty.nunique() >= 2:
                return True
        return False

    def drop(self, dim):
        """Drop one or more dimension levels, returning a new DimensionTable.

        The transformation mode is auto-detected from the data:

        * **Filter** — when the data already contains pre-computed partial
          subtotal rows for *dim* (rows where ``dims[dim] == ''`` but other
          dimensions carry specific values).  Only those rows are kept; leaf
          rows specific to *dim* are discarded.
        * **Aggregate** — when no such partial subtotals exist.  Leaf rows are
          grouped by the remaining dimensions and geography; estimates are
          summed and MOE is propagated as ``sqrt(sum(moe_i²))``.

        Parameters
        ----------
        dim : str, int, or list of str/int
            Dimension(s) to drop. A string names a dimension column from
            ``self.dims``; an integer selects by 0-based position. A list
            drops each element in order, with each drop applied to the result
            of the previous one.

        Returns
        -------
        DimensionTable
        """
        if isinstance(dim, list):
            # Resolve all items to column names relative to self before dropping so
            # that integer indices refer to the original column positions, not the
            # shrinking positions after each successive drop.
            cols = list(self.dims.columns)
            resolved = []
            for d in dim:
                if isinstance(d, int):
                    if not (-len(cols) <= d < len(cols)):
                        raise IndexError(
                            f"Dimension index {d} out of range for {len(cols)} dimension(s)."
                        )
                    resolved.append(cols[d])
                else:
                    if d not in self.dims.columns:
                        raise ValueError(f"Dimension '{d}' not in {cols}.")
                    resolved.append(d)
            result = self
            for d in resolved:
                result = result.drop(d)
            return result

        if isinstance(dim, int):
            cols = list(self.dims.columns)
            if not (-len(cols) <= dim < len(cols)):
                raise IndexError(
                    f"Dimension index {dim} out of range for {len(cols)} dimension(s)."
                )
            dim = cols[dim]

        if dim not in self.dims.columns:
            raise ValueError(f"Dimension '{dim}' not in {list(self.dims.columns)}.")

        other_dims = [d for d in self.dims.columns if d != dim]

        if not other_dims or self._has_partial_subtotals(dim):
            # Filter path: keep only rows where this dimension is already absent
            # (dim == ''), then drop the column.  Also used when dim is the last
            # remaining column, since _aggregate_dim requires at least one other
            # dimension to group by.
            mask = self.dims[dim] == ''
            keep_vars = set(self.dims.index[mask])
            new_long = self.long.loc[self.long['variable'].isin(keep_vars)].copy()
            new_dims = (self.dims.loc[self.dims.index.isin(keep_vars)]
                        .drop(columns=[dim]))
        else:
            # No pre-aggregation — sum leaf rows and propagate MOE.
            new_long, new_dims = self._aggregate_dim(dim, other_dims)

        # Remove grand-total rows — after the drop, any row where every remaining
        # dim column is '' carries no categorical label and is not meaningful.
        if len(new_dims.columns) > 0 and not new_dims.empty:
            all_empty = (new_dims == '').all(axis=1)
            if all_empty.any():
                keep = set(new_dims.index[~all_empty])
                new_dims = new_dims.loc[~all_empty]
                new_long = new_long.loc[new_long['variable'].isin(keep)]

        result = DimensionTable.__new__(DimensionTable)
        result.logger = self.logger
        result.long = new_long.reset_index(drop=True)
        result.dims = new_dims
        result.value_cols = self.value_cols
        return result

    def _aggregate_dim(self, drop_dim, other_dims):
        """Sum over drop_dim; propagate MOE as sqrt(sum(moe_i²)).

        Only leaf rows (where drop_dim is non-empty) are summed.  Subtotal
        rows (drop_dim == '') are pre-computed totals and would double-count
        if included in the aggregation.
        """
        geo_meta = [c for c in self.long.columns
                    if c not in ('variable', 'variable_label') + tuple(self.value_cols)]

        long_d = self.long.copy()
        for d in self.dims.columns:
            long_d[d] = long_d['variable'].map(self.dims[d])

        # Exclude subtotal rows for this dimension — they are pre-computed totals
        long_d = long_d.loc[long_d[drop_dim] != ''].copy()

        for col in self.value_cols:
            long_d[col] = pd.to_numeric(long_d[col], errors='coerce')

        agg_dict = {col: 'sum' for col in self.value_cols if col != 'moe'}
        if 'moe' in self.value_cols:
            long_d['_moe_sq'] = long_d['moe'] ** 2
            agg_dict['_moe_sq'] = 'sum'

        group_cols = geo_meta + other_dims
        grouped = long_d.groupby(group_cols, observed=True).agg(agg_dict).reset_index()

        if 'moe' in self.value_cols:
            grouped['moe'] = np.sqrt(grouped['_moe_sq'])
            grouped = grouped.drop(columns=['_moe_sq'])

        prefix = self.long['variable'].iloc[0].split('_')[0]
        unique_dim_combos = (grouped[other_dims].drop_duplicates()
                             .reset_index(drop=True))
        unique_dim_combos['variable'] = [
            f"{prefix}_A{i:03d}" for i in range(len(unique_dim_combos))
        ]
        unique_dim_combos['variable_label'] = unique_dim_combos[other_dims].apply(
            lambda row: '!!'.join(v for v in row if v), axis=1
        )

        grouped = grouped.merge(
            unique_dim_combos[['variable', 'variable_label'] + other_dims],
            on=other_dims,
        ).drop(columns=other_dims)

        new_long = grouped[list(self.long.columns)]
        new_dims = unique_dim_combos.set_index('variable')[other_dims]
        return new_long, new_dims

    def wide(self):
        """Pivot long data to wide format.

        Returns
        -------
        pandas.DataFrame
            Rows indexed by dimension labels; columns are a MultiIndex with
            canonical level order ``concept > universe > survey > geoidfq >
            name > [race] > reference_period > value_type``.

        Examples
        --------
        >>> table = DimensionTable(api.long)    # doctest: +SKIP
        >>> wide = table.wide()                 # doctest: +SKIP
        >>> wide.columns.names                  # doctest: +SKIP
        ['concept', 'universe', 'survey', 'geoidfq', 'name', 'reference_period', 'value_type']
        >>> pct = table.percent(_wide=wide)     # doctest: +SKIP
        """
        long = self.long.replace(
            dict.fromkeys(MISSING_VALUES + _MISSING_VALUES_NUMERIC, np.nan)
        )

        col_dims = [c for c in long.columns
                    if c not in ('variable', 'variable_label') + tuple(self.value_cols)]

        wide = long.pivot(
            index='variable',
            columns=col_dims,
            values=self.value_cols,
        )

        # Strip ':' from dim values for display
        display_dims = self.dims.apply(lambda col: col.str.rstrip(':').str.strip())

        col_level_names = [n if n is not None else 'value_type' for n in wide.columns.names]
        wide.columns = wide.columns.to_list()
        wide = wide.join(display_dims)
        wide = wide.set_index(list(display_dims.columns))
        wide.columns = pd.MultiIndex.from_tuples(wide.columns)
        wide.columns.names = col_level_names
        # Apply canonical level order; value-type level ('value_type') goes last.
        current = list(wide.columns.names)
        ordered = [n for n in _WIDE_COL_LEVEL_ORDER if n in current]
        remainder = [n for n in current if n not in _WIDE_COL_LEVEL_ORDER and n != 'value_type']
        wide.columns = wide.columns.reorder_levels(ordered + remainder + ['value_type'])

        # Restore categorical ordering for any level whose source column in self.long
        # is categorical (covers both dim columns from _parse_dims and race).
        # from_tuples() above strips categorical dtype, so we re-apply it here.
        #
        # set_levels() replaces level VALUES without touching the integer codes, so we
        # must pass the CURRENT level values (preserving code→value mapping) and only
        # use the desired category order as the CategoricalIndex's *categories* argument.
        # Passing the desired order as values would corrupt the code→value mapping.
        for i, name in enumerate(wide.columns.names):
            if name == 'value_type':
                continue
            if name in self.long.columns and hasattr(self.long[name], 'cat'):
                cats = list(self.long[name].cat.categories)
                current_vals = list(wide.columns.levels[i])
                present_cats = [c for c in cats if c in set(current_vals)]
                wide.columns = wide.columns.set_levels(
                    pd.CategoricalIndex(current_vals, categories=present_cats, ordered=True),
                    level=i,
                )

        wide = wide.sort_index(level='geoidfq', axis=1)
        # Include dim index values in the duplicate check so that rows sharing
        # identical all-NaN data (e.g. a geography with no coverage) are not
        # collapsed into one row.  Filter via a boolean mask on the original
        # DataFrame to preserve categorical dtypes on column levels.
        return wide[~wide.reset_index().duplicated().values]

    def percent(self, decimals=2, _wide=None):
        """Compute column percentages relative to the grand total row.

        The grand total is the row where all dimension columns after the first
        are ``''`` (empty).  Returns the same structure as :meth:`wide` with
        the total row removed and values expressed as percentages.

        Estimate columns use simple proportion (``x / T * 100``).  MOE columns
        use the Census Bureau derived proportion formula::

            MOE(p) = (1/T) * sqrt(MOE_x² − p² * MOE_T²)

        where ``p = x/T``.  When the radicand is negative (possible due to
        sampling variability), the alternative addition form is used instead::

            MOE(p) = (1/T) * sqrt(MOE_x² + p² * MOE_T²)

        Parameters
        ----------
        decimals : int
            Rounding precision for percentage values.
        _wide : DataFrame, optional
            Pre-computed result of :meth:`wide`. Pass it to avoid computing
            the pivot twice when you need both ``wide()`` and ``percent()``.

        Returns
        -------
        pandas.DataFrame
        """
        wide = _wide if _wide is not None else self.wide()

        idx = wide.index
        if isinstance(idx, pd.MultiIndex):
            total_mask = [all(v == '' for v in vals[1:]) for vals in idx]
        else:
            total_mask = [v == '' for v in idx]

        if not any(total_mask):
            raise ValueError(
                "No grand total row found. Expected a row where all "
                "dimension columns after the first are ''."
            )

        total_pos = total_mask.index(True)
        total_row = wide.iloc[[total_pos]].astype(float)
        non_total = wide.drop(wide.index[total_pos]).astype(float)

        # The variable_type level is the last level (name=None) after wide()'s reorder_levels
        val_type_level = wide.columns.names.index('value_type')
        cols = wide.columns.tolist()
        moe_cols = {c for c in cols if c[val_type_level] == 'moe'}

        pct = non_total.copy()
        for col in cols:
            val_type = col[val_type_level]
            T_est_col = tuple(
                'estimate' if i == val_type_level else v
                for i, v in enumerate(col)
            )
            T = float(total_row[T_est_col].iloc[0])

            if pd.isna(T) or T == 0:
                pct[col] = pd.NA
                continue

            if val_type == 'estimate':
                pct[col] = (non_total[col] / T * 100).round(decimals)

            elif val_type == 'moe':
                # Census Bureau derived proportion formula:
                # MOE(p) = (1/T) * sqrt(MOE_x² ± p² * MOE_T²), * 100
                moe_T = float(total_row[col].iloc[0]) if not pd.isna(float(total_row[col].iloc[0])) else 0.0
                p = non_total[T_est_col] / T
                m_x = non_total[col]
                radicand = m_x**2 - p**2 * moe_T**2
                pos_root = np.sqrt(radicand.clip(lower=0))
                neg_root = np.sqrt(m_x**2 + p**2 * moe_T**2)
                moe_pct = pd.Series(
                    np.where(radicand >= 0, pos_root, neg_root),
                    index=non_total.index,
                ) / T * 100
                moe_pct[m_x.isna() | p.isna()] = np.nan
                pct[col] = moe_pct.round(decimals)

            else:
                # percent_estimate, percent_moe, total, etc.: simple division
                pct[col] = (non_total[col] / T * 100).round(decimals)

        return pct

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    _VTYPE_LABELS = {
        "estimate":         "Estimate",
        "moe":              "MOE",
        "percent_estimate": "Percent Estimate",
        "percent_moe":      "Percent MOE",
        "total":            "Total",
    }

    def _to_wide_flat(self, value_mode: str = "estimate") -> "pd.DataFrame":
        """Return a simplified flat DataFrame suitable for CSV export.

        Dim columns from ``self.dims`` become plain string columns.  Data
        columns are renamed from the 7-level MultiIndex to
        ``"{geography name} - {year} ({value type})"`` so that
        ``concept``, ``universe``, and ``survey`` — which are identical
        for every column — are dropped from the header and captured
        instead in the resource metadata.
        """
        wide = self.percent() if value_mode == "percent" else self.wide()

        # Extract row index (dim values) into a plain DataFrame before
        # touching the column MultiIndex, to avoid tuple-column artefacts
        # that arise when calling reset_index() on a MultiIndex-column frame.
        dims_flat = wide.index.to_frame(index=False)

        # Build simplified column names from the MultiIndex tuples
        new_col_names = []
        for col in wide.columns:
            col_map = dict(zip(wide.columns.names, col))
            geo_name = col_map.get("name", "")
            year = col_map.get("reference_period", "")
            vtype = col_map.get("value_type", "")
            label = self._VTYPE_LABELS.get(vtype, vtype.replace("_", " ").title())
            new_col_names.append(f"{geo_name} - {year} ({label})")

        data_flat = pd.DataFrame(
            wide.values,
            columns=new_col_names,
            index=range(len(wide)),
        )
        return pd.concat([dims_flat, data_flat], axis=1)

    def create_schema(self, value_mode: str = "estimate") -> "frictionless.Schema":
        """Build a frictionless Schema for the flat wide export.

        Dimension columns are typed as ``string``; data columns
        (estimates, MOE, etc.) are typed as ``number``.  The primary key
        is the list of dimension column names.

        Parameters
        ----------
        value_mode:
            ``'estimate'`` (default) or ``'percent'``.  Determines which
            value columns appear in the flat output.
        """
        import frictionless

        flat = self._to_wide_flat(value_mode)
        dim_names = list(self.dims.columns)
        data_cols = [c for c in flat.columns if c not in dim_names]

        dim_fields = [
            {"name": d, "type": "string", "description": f"Dimension: {d}"}
            for d in dim_names
        ]
        data_fields = [
            {"name": c, "type": "number", "missingValues": MISSING_VALUES}
            for c in data_cols
        ]

        descriptor = {
            "fields": dim_fields + data_fields,
            "missingValues": MISSING_VALUES,
            "primaryKey": dim_names,
        }
        result = frictionless.Schema.validate_descriptor(descriptor)
        if not result.valid:
            raise ValueError(f"Schema descriptor invalid: {result}")
        return frictionless.Schema.from_descriptor(descriptor)

    def create_resource(
        self,
        name: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> "frictionless.Resource":
        """Build a frictionless Resource descriptor for the flat export.

        ``concept``, ``universe``, and ``survey`` are stored as private
        custom fields (``_concept``, ``_universe``, ``_survey``) rather
        than as column-level headers.

        Parameters
        ----------
        name:
            Slug used as the resource name and base filename.
        title:
            Human-readable title.  Defaults to the table concept string.
        description:
            Narrative description.  Defaults to a sentence combining
            concept, survey, and universe.

        Notes
        -----
        ``self._export_filename`` and ``self._export_schema_filename``
        must be set before calling this method (done automatically by
        :meth:`save`).
        """
        import frictionless

        concept = str(self.long["concept"].dropna().iloc[0])
        universe = str(self.long["universe"].dropna().iloc[0])
        survey = str(self.long["survey"].dropna().iloc[0])

        descriptor = {
            "name": name,
            "title": title or concept,
            "description": (
                description
                or f"{concept} from {survey}. Universe: {universe}."
            ),
            "path": self._export_filename,
            "schema": self._export_schema_filename,
            "_concept": concept,
            "_universe": universe,
            "_survey": survey,
            "sources": [{"title": "US Census Bureau", "path": "https://api.census.gov"}],
        }
        return frictionless.Resource.from_descriptor(descriptor)

    def save(
        self,
        output_path,
        name: str,
        *,
        value_mode: str = "estimate",
        title: str | None = None,
    ) -> None:
        """Export the DimensionTable to a flat CSV with frictionless artifacts.

        Writes three files to *output_path*:

        * ``{name}.csv``           — flat wide DataFrame (human-readable columns)
        * ``{name}.schema.yaml``   — frictionless Schema
        * ``{name}.resource.yaml`` — frictionless Resource (validated)

        Parameters
        ----------
        output_path:
            Directory to write files into (created if it does not exist).
        name:
            Base filename slug (e.g. ``"b01001-franklin"``).
        value_mode:
            ``'estimate'`` (default) or ``'percent'``.
        title:
            Optional human-readable title for the resource descriptor.
        """
        import contextlib
        import frictionless

        output = Path(output_path)
        output.mkdir(parents=True, exist_ok=True)

        self._export_filename = f"{name}.csv"
        self._export_schema_filename = f"{name}.schema.yaml"
        resource_filename = f"{name}.resource.yaml"

        self._to_wide_flat(value_mode).to_csv(output / self._export_filename, index=False)

        schema = self.create_schema(value_mode)
        schema.to_yaml(str(output / self._export_schema_filename))

        with contextlib.chdir(output):
            resource = self.create_resource(name, title=title)
            resource.to_yaml(resource_filename)
            result = frictionless.Resource(resource_filename).validate()

        if not result.valid:
            self.logger.error("Resource validation failed: %s", result.stats)
            raise RuntimeError("Resource validation failed after save.")


# ---------------------------------------------------------------------------
# RaceDimensionTable
# ---------------------------------------------------------------------------

class RaceDimensionTable(DimensionTable):
    """DimensionTable for concatenated racial iteration group data.

    Accepts a concatenated ``CensusAPI.long`` DataFrame from multiple racial
    iteration group fetches (e.g. B17020A, B17020B, …) and preprocesses it
    before delegating to :class:`DimensionTable`:

    - Extracts the race letter from each variable code and maps it to a
      human-readable label via *race_map*, adding a ``race`` column.
    - Normalizes variable codes so all groups share the same variable
      namespace (``B17020A_001`` → ``B17020_001``).
    - Normalizes ``concept`` (strips the trailing parenthetical race
      qualifier) and ``universe`` (replaces the leading ``"<Race> alone"``
      prefix with ``"Population"``) so both fields are identical across
      races and do not inflate the column MultiIndex.
    - Rows whose race code is not present in *race_map* are silently dropped.

    The ``race`` column is excluded from ``variable_type`` and therefore
    becomes a column-level dimension in :meth:`wide` and :meth:`percent`.
    Because each race occupies its own column group, the inherited
    :meth:`percent` naturally computes within-race percentages (each race's
    Total row is its own denominator).

    Parameters
    ----------
    long_data : pandas.DataFrame
        Concatenation of ``CensusAPI.long`` outputs from racial iteration
        group fetches (e.g. B17020A through B17020I).
    race_map : dict, optional
        Mapping from single uppercase race code letter to human-readable
        label.  Defaults to :data:`RACE_TABLE_MAP`.  Rows whose extracted
        code is absent from the map are dropped before further processing.
    dim_names : list of str, optional
        Forwarded to :class:`DimensionTable`.
    """

    def __init__(self, long_data, race_map=None, dim_names=None):
        effective_map = race_map if race_map is not None else RACE_TABLE_MAP
        processed = self._preprocess(long_data.copy(), effective_map)
        super().__init__(processed, dim_names)
        self.value_cols = [c for c in self.value_cols if c != 'race']
        # Make race an ordered categorical in race_map insertion order so
        # wide() produces race columns in that order.
        race_categories = list(dict.fromkeys(effective_map.values()))
        self.long['race'] = pd.Categorical(
            self.long['race'], categories=race_categories, ordered=True
        )

    def _preprocess(self, long, race_map):
        parsed = long['variable'].str.extract(r'^([A-Z]\d+)([A-Z])_(\d+)')
        # parsed columns: 0 = group prefix (e.g. 'B17020'),
        #                 1 = race letter   (e.g. 'A'),
        #                 2 = variable number (e.g. '001')

        long['race'] = parsed[1].map(race_map)
        mask = long['race'].notna()
        long = long[mask].copy()
        parsed = parsed[mask]

        long['variable'] = (parsed[0] + '_' + parsed[2]).to_numpy()

        if 'concept' in long.columns:
            long['concept'] = long['concept'].str.replace(
                r'\s*\([^)]+\)\s*$', '', regex=True
            ).str.strip()

        if 'universe' in long.columns:
            long['universe'] = long['universe'].str.replace(
                r'^.+?\s+population\s+', 'Population of race ', regex=True,
                flags=re.IGNORECASE,
            )

        return long


# ---------------------------------------------------------------------------
# Private schema helper
# ---------------------------------------------------------------------------

def _build_long_schema(long_df: pd.DataFrame):
    """Build a frictionless Schema from a long-format DataFrame."""
    import frictionless

    fixed_fields = [
        {'name': 'geoidfq',          'type': 'string',  'description': 'Census geography fully-qualified identifier'},
        {'name': 'reference_period', 'type': 'integer', 'description': 'Reference year'},
        {'name': 'survey',           'type': 'string',  'description': 'Census survey endpoint'},
        {'name': 'concept',          'type': 'string',  'description': 'Table concept description'},
        {'name': 'universe',         'type': 'string',  'description': 'Universe for the table'},
        {'name': 'variable_label',   'type': 'string',  'description': 'Human-readable variable label'},
        {'name': 'variable',         'type': 'string',  'description': 'Base variable code'},
    ]
    if 'name' in long_df.columns:
        fixed_fields.insert(1, {'name': 'name', 'type': 'string', 'description': 'Geography name'})

    fixed_names = {f['name'] for f in fixed_fields}
    value_fields = []
    for col in long_df.columns:
        if col in fixed_names:
            continue
        if col not in _VALUE_FIELD_DEFS:
            raise ValueError(f"Unexpected column {col!r} in long data.")
        value_fields.append(_VALUE_FIELD_DEFS[col])

    descriptor = {
        'fields': fixed_fields + value_fields,
        'missingValues': MISSING_VALUES,
        'primaryKey': ['geoidfq', 'reference_period', 'variable'],
    }
    result = frictionless.Schema.validate_descriptor(descriptor)
    if not result.valid:
        raise ValueError(f"Schema descriptor invalid: {result}")
    return frictionless.Schema.from_descriptor(descriptor)


# ---------------------------------------------------------------------------
# TimeSeries
# ---------------------------------------------------------------------------

class TimeSeries:
    """Fetches the same Census group across multiple vintage years and concatenates into one long DataFrame.

    Parameters
    ----------
    survey : str
        Survey name, e.g. ``'acs/acs5'``.
    years : list of int
        Vintage years to fetch.
    scope : str or Scope
        Geographic scope key or instance.
    group : str, Group, or None
        Group code, e.g. ``'B01001'``. Required unless *variables* is given.
    sumlevel : str or SumLevel, optional
    variables : list of str, optional
    """

    def __init__(self, survey, years, scope, group=None, sumlevel=None, variables=None):
        self.survey = survey
        self.years = sorted(int(y) for y in years)
        self.logger = logging.getLogger(__name__).getChild(self.__class__.__name__)

        self.calls: dict[int, CensusAPI] = {}
        for year in self.years:
            endpoint = Endpoint(survey, year)
            self.calls[year] = CensusAPI(endpoint, scope, group=group, sumlevel=sumlevel, variables=variables)

        first = next(iter(self.calls.values()))
        self.scope = first.scope
        self.sumlevel = first.sumlevel
        self.group = first.group
        self.variables = first.variables

        self.long = pd.concat(
            [call.long for call in self.calls.values()],
            ignore_index=True,
        )

    @cached_property
    def name(self) -> str:
        """Canonical name with year range, e.g. ``census-acs-acs5-2019-2023-county-ohio-b01001``."""
        base = censusapi_name(
            Endpoint(self.survey, self.years[0]),
            self.scope,
            self.group,
            sumlevel=self.sumlevel,
            variables=self.variables,
        )
        if len(self.years) == 1:
            return base
        survey_part = self.survey.replace('/', '-')
        old_prefix = f"census-{survey_part}-{self.years[0]}-"
        new_prefix = f"census-{survey_part}-{self.years[0]}-{self.years[-1]}-"
        return base.replace(old_prefix, new_prefix, 1)

    def define_schema(self):
        """Build a frictionless Schema for the concatenated long-format data."""
        self.logger.info(f"Defining schema for {self.survey} {self.years[0]}-{self.years[-1]}.")
        return _build_long_schema(self.long)

    def create_resource(self):
        """Build a frictionless Resource descriptor for the time-series dataset."""
        import frictionless

        year_range = f"{self.years[0]}–{self.years[-1]}" if len(self.years) > 1 else str(self.years[0])
        scope_name = self.scope.name
        sumlevel_prefix = f'{self.sumlevel.plural} in ' if self.sumlevel is not None else ''

        if self.group is not None:
            subject = f"{self.group.code}: {self.group.description}"
            title = f"{year_range} {self.group.description} for {sumlevel_prefix}{scope_name}"
        else:
            vars_summary = ', '.join(self.variables[:3])
            if len(self.variables) > 3:
                vars_summary += f', ... ({len(self.variables)} total)'
            subject = vars_summary
            title = f"{year_range} selected variables for {sumlevel_prefix}{scope_name}"

        sources = [
            {
                'title': f'US Census Bureau API ({year})',
                'path': call.request['url'],
                '_params': call.request['params'],
            }
            for year, call in self.calls.items()
        ]

        return frictionless.Resource.from_descriptor({
            'name': self.name,
            'title': title,
            'description': (
                f"Census API time-series data for {subject} from {self.survey} "
                f"{year_range} for {sumlevel_prefix}{scope_name}."
            ),
            'path': self.filename,
            'schema': self.schema_filename,
            'sources': sources,
        })

    def save(self, output_path):
        """Write concatenated long data, schema, and resource files to *output_path*.

        Produces three files:
        - ``{name}.long.csv``
        - ``{name}.schema.yaml``
        - ``{name}.resource.yaml``
        """
        import contextlib
        import frictionless

        output = Path(output_path)
        output.mkdir(parents=True, exist_ok=True)

        self.datapath        = output
        self.filename        = f"{self.name}.long.csv"
        self.schema_filename = f"{self.name}.schema.yaml"
        resource_filename    = f"{self.name}.resource.yaml"

        self.logger.info(f"Writing data to {output / self.filename}.")
        self.long.to_csv(output / self.filename, index=False)

        self.logger.info(f"Writing schema to {output / self.schema_filename}.")
        self.schema = self.define_schema()
        self.schema.to_yaml(str(output / self.schema_filename))

        self.logger.info(f"Writing resource to {output / resource_filename}.")
        resource = self.create_resource()
        with contextlib.chdir(output):
            resource.to_yaml(resource_filename)
            result = frictionless.Resource(resource_filename).validate()

        if not result.valid:
            self.logger.error(f"Resource validation failed: {result.stats}")
            raise RuntimeError("Resource validation failed after save.")

        self.logger.info("Save complete and resource validated.")

    def dimension_table(self, **kwargs):
        """Return a :class:`DimensionTable` built from the concatenated long data."""
        return DimensionTable(self.long, **kwargs)


# ---------------------------------------------------------------------------
# RaceTable
# ---------------------------------------------------------------------------

class RaceTable:
    """Fetches racial iteration groups for a base group code and concatenates into one long DataFrame.

    Discovers which race letter suffixes (A–I) exist for the given endpoint and group,
    fetches each as a separate :class:`CensusAPI` call, and concatenates the results.
    The concatenated ``long`` DataFrame can be passed directly to
    :class:`RaceDimensionTable`.

    Parameters
    ----------
    endpoint : Endpoint
        Survey and vintage year.
    scope : str or Scope
        Geographic scope key or instance.
    group : str
        Base group code **without** the race letter suffix, e.g. ``'B17020'``.
    sumlevel : str or SumLevel, optional
    race_codes : list of str, optional
        Subset of race letter codes to fetch, e.g. ``['A', 'B', 'C']``.
        Defaults to all codes in :data:`RACE_TABLE_MAP` that exist for the endpoint.
    """

    def __init__(self, endpoint, scope, group, sumlevel=None, race_codes=None):
        self.endpoint = endpoint if isinstance(endpoint, Endpoint) else Endpoint(*endpoint)
        self.logger = logging.getLogger(__name__).getChild(self.__class__.__name__)

        self.base_code = (group.upper() if isinstance(group, str) else group.code)

        candidate_codes = race_codes if race_codes is not None else list(RACE_TABLE_MAP.keys())
        existing_groups = endpoint.groups
        valid_codes = [c for c in candidate_codes if f"{self.base_code}{c}" in existing_groups]

        if not valid_codes:
            raise ValueError(
                f"No racial iteration groups found for {self.base_code!r} in "
                f"{endpoint.survey!r} {endpoint.year}."
            )

        skipped = [c for c in candidate_codes if c not in valid_codes]
        if skipped:
            self.logger.warning(
                f"Race codes {skipped} not found for {self.base_code} in "
                f"{endpoint.survey} {endpoint.year} — skipping."
            )

        self.calls: dict[str, CensusAPI] = {}
        for code in valid_codes:
            self.calls[code] = CensusAPI(
                endpoint, scope, group=f"{self.base_code}{code}", sumlevel=sumlevel
            )

        first = next(iter(self.calls.values()))
        self.scope = first.scope
        self.sumlevel = first.sumlevel

        self.long = pd.concat(
            [call.long for call in self.calls.values()],
            ignore_index=True,
        )

    @cached_property
    def name(self) -> str:
        """Canonical name with ``-race`` suffix, e.g. ``census-acs-acs5-2023-county-ohio-b17020-race``."""
        base = censusapi_name(self.endpoint, self.scope, self.base_code, sumlevel=self.sumlevel)
        return f"{base}-race"

    def define_schema(self):
        """Build a frictionless Schema for the concatenated long-format data."""
        self.logger.info(
            f"Defining schema for {self.endpoint.survey} {self.endpoint.year} "
            f"{self.base_code} (race)."
        )
        return _build_long_schema(self.long)

    def create_resource(self):
        """Build a frictionless Resource descriptor for the racial iteration dataset."""
        import frictionless
        import re as _re

        scope_name = self.scope.name
        sumlevel_prefix = f'{self.sumlevel.plural} in ' if self.sumlevel is not None else ''
        year = self.endpoint.year

        first_group = next(iter(self.calls.values())).group
        base_description = _re.sub(r'\s*\([^)]+\)\s*$', '', first_group.description).strip()

        race_labels = [RACE_TABLE_MAP.get(c, c) for c in self.calls]
        race_codes_str = ', '.join(self.calls.keys())
        title = (
            f"{year} {base_description} by race "
            f"for {sumlevel_prefix}{scope_name}"
        )

        sources = [
            {
                'title': f'US Census Bureau API ({self.base_code}{code}: {RACE_TABLE_MAP.get(code, code)})',
                'path': call.request['url'],
                '_params': call.request['params'],
            }
            for code, call in self.calls.items()
        ]

        return frictionless.Resource.from_descriptor({
            'name': self.name,
            'title': title,
            'description': (
                f"Census API racial iteration data for {self.base_code} ({base_description}) "
                f"from {self.endpoint.survey} {year} "
                f"for {sumlevel_prefix}{scope_name}. "
                f"Race codes fetched: {race_codes_str}."
            ),
            'path': self.filename,
            'schema': self.schema_filename,
            'sources': sources,
        })

    def save(self, output_path):
        """Write concatenated long data, schema, and resource files to *output_path*.

        Produces three files:
        - ``{name}.long.csv``
        - ``{name}.schema.yaml``
        - ``{name}.resource.yaml``
        """
        import contextlib
        import frictionless

        output = Path(output_path)
        output.mkdir(parents=True, exist_ok=True)

        self.datapath        = output
        self.filename        = f"{self.name}.long.csv"
        self.schema_filename = f"{self.name}.schema.yaml"
        resource_filename    = f"{self.name}.resource.yaml"

        self.logger.info(f"Writing data to {output / self.filename}.")
        self.long.to_csv(output / self.filename, index=False)

        self.logger.info(f"Writing schema to {output / self.schema_filename}.")
        self.schema = self.define_schema()
        self.schema.to_yaml(str(output / self.schema_filename))

        self.logger.info(f"Writing resource to {output / resource_filename}.")
        resource = self.create_resource()
        with contextlib.chdir(output):
            resource.to_yaml(resource_filename)
            result = frictionless.Resource(resource_filename).validate()

        if not result.valid:
            self.logger.error(f"Resource validation failed: {result.stats}")
            raise RuntimeError("Resource validation failed after save.")

        self.logger.info("Save complete and resource validated.")

    def dimension_table(self, **kwargs):
        """Return a :class:`RaceDimensionTable` built from the concatenated long data."""
        return RaceDimensionTable(self.long, **kwargs)
