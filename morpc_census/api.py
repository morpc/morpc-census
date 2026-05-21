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
    return DimensionTable(long_df).concept_dims


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

    dt = DimensionTable(label_df, group=group)

    result: dict[str, list[str]] = {}
    for col in dt.dims.columns:
        cats = [
            str(v).rstrip(":").strip()
            for v in dt.dims[col].cat.categories
            if str(v).strip(":").strip()
        ]
        result[col] = cats
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

        self.concept_dims = self._resolve_dim_names(dim_names, group)
        self.dims = self._parse_dims(self.concept_dims)

    def _resolve_dim_names(self, dim_names_arg, group_arg) -> dict:
        """Centralise dim-name resolution into a single ``dict[str, str]``.

        Priority order
        --------------
        1. ``dim_names_arg`` is a ``dict``  → return as-is.
        2. ``dim_names_arg`` is a ``list``  → build ``{"dim_i": name, …}``.
        3. ``group_arg is not None``         → return ``group_arg.concept_dims``.
        4. Auto-infer from ``self.long``     → extract group code / concept and
           run the same inference pipeline used by :attr:`Group.concept_dims`.
           Only used when the concept string contains ``" by "`` (or a curated
           override exists in ``dim_names.json``).
        5. ``{}`` fallback.
        """
        # Priority 1: dict passed directly
        if isinstance(dim_names_arg, dict):
            return dim_names_arg

        # Priority 2: list passed
        if isinstance(dim_names_arg, list):
            return {f"dim_{i}": name for i, name in enumerate(dim_names_arg)}

        # Priority 3: group object
        if group_arg is not None:
            return group_arg.concept_dims

        # Priority 4: auto-infer from self.long
        try:
            group_code = ""
            if "variable" in self.long.columns and not self.long.empty:
                m = re.match(r"^([A-Z][A-Z0-9]+)_", str(self.long["variable"].iloc[0]))
                if m:
                    group_code = m.group(1)

            # Check curated override
            overrides = _load_dim_names_json()
            if group_code and group_code in overrides:
                return overrides[group_code]

            # Auto-infer only when concept contains " by "
            concept = ""
            if "concept" in self.long.columns and not self.long.empty:
                concept = str(self.long["concept"].iloc[0])

            if " by " not in concept.lower():
                return {}

            temp_dims = self._parse_dims({})
            return _infer_dim_names_from_dims(temp_dims, concept)
        except Exception:
            return {}

    def _parse_dims(self, concept_dims=None):
        """Parse ``variable_label`` into a structured dimension DataFrame.

        Each ``!!``-delimited label is split into subtotal segments (ending
        with ``:``) and leaf segments (no trailing ``:``).  Subtotals are
        left-aligned into the first *S* columns; leaves are left-aligned into
        the next *L* columns, where *S* and *L* are the maximum depths across
        all variables.  This keeps the same concept in the same column even
        when paths have different depths (e.g. B05004 Sex by Nativity).

        The ``':'`` suffix is preserved in the stored values so ``drop()`` can
        distinguish aggregate rows from leaf rows.  It is stripped for display
        in ``wide()``.

        Returns
        -------
        pandas.DataFrame
            Index = ``variable``, columns = dimension names.
        """
        # One label per variable code — different vintages may use the same code
        # with slightly different label text (e.g. trailing ':' present or absent).
        unique = (self.long[['variable', 'variable_label']]
                  .drop_duplicates(subset='variable')
                  .set_index('variable')
                  .copy())

        # Normalize: older Census vintages omit the trailing ':' from subtotal
        # segments.  Rebuild each label so that any segment which is a strict
        # prefix of another label (i.e. has children in the label tree) gets a
        # ':' suffix.  Existing ':' suffixes are also preserved, so labels that
        # are already in the standard convention pass through unchanged.
        clean_labels = set(
            '!!'.join(p.rstrip(':') for p in lbl.split('!!'))
            for lbl in unique['variable_label']
        )

        def normalize_label(label):
            parts = label.split('!!')
            stripped = [p.rstrip(':') for p in parts]
            result = []
            for i, (orig, part) in enumerate(zip(parts, stripped)):
                prefix = '!!'.join(stripped[:i + 1])
                has_children = any(
                    other.startswith(prefix + '!!') for other in clean_labels
                )
                result.append(part + ':' if (orig.endswith(':') or has_children) else part)
            return '!!'.join(result)

        unique['variable_label'] = unique['variable_label'].map(normalize_label)

        def split_path(label):
            parts = label.split('!!')
            return (
                [p for p in parts if p.endswith(':')],
                [p for p in parts if not p.endswith(':')],
            )

        paths = unique['variable_label'].map(split_path)
        S = paths.map(lambda x: len(x[0])).max()
        L = paths.map(lambda x: len(x[1])).max()

        def align(subtotals, leaves):
            return (subtotals + [''] * (S - len(subtotals)) +
                    leaves    + [''] * (L - len(leaves)))

        rows = paths.map(lambda x: align(*x))
        n = S + L
        dims = pd.DataFrame(rows.tolist(), index=unique.index)
        cd = concept_dims or {}
        dims.columns = [cd.get(f"dim_{i}", f"dim_{i}") for i in range(n)]

        # Convert each column to an ordered categorical using first-appearance order
        # (Census variables are returned in their defined hierarchical order).
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

        self.dims = self._parse_dims(self.concept_dims)
        return self

    def drop(self, dim, method='summarize'):
        """Drop one or more dimension levels, returning a new DimensionTable.

        Parameters
        ----------
        dim : str, int, or list of str/int
            Dimension(s) to drop. A string names a dimension column from
            ``self.dims``; an integer selects by 0-based position. A list
            drops each element in order, with each drop applied to the result
            of the previous one.
        method : {'summarize', 'aggregate'}
            ``'summarize'``: keep only rows where *dim* is absent (``''``),
            i.e. rows that are already aggregated across this dimension.
            Rows that carry a specific value for *dim* are discarded.

            ``'aggregate'``: group all remaining rows by the other dimensions
            and geography, sum estimates, and propagate MOE via
            ``sqrt(sum(moe_i²))``.

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
                result = result.drop(d, method=method)
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

        if method == 'summarize':
            # Keep only rows where this dimension is absent — i.e. already aggregated
            # across it.  Rows where dim is 'Male:', 'Spanish:', etc. represent a
            # specific value of that dimension and are discarded.
            mask = self.dims[dim] == ''
            keep_vars = set(self.dims.index[mask])
            new_long = self.long.loc[self.long['variable'].isin(keep_vars)].copy()
            new_dims = (self.dims.loc[self.dims.index.isin(keep_vars)]
                        .drop(columns=[dim]))

        elif method == 'aggregate':
            new_long, new_dims = self._aggregate_dim(dim, other_dims)

        else:
            raise ValueError(f"method must be 'summarize' or 'aggregate', got '{method}'.")

        result = DimensionTable.__new__(DimensionTable)
        result.logger = self.logger
        result.long = new_long.reset_index(drop=True)
        result.dims = new_dims
        result.value_cols = self.value_cols
        result.concept_dims = self.concept_dims
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
        # DataFrame.apply with str accessors returns object dtype, losing the ordered
        # Categorical dtype that _parse_dims built.  Rebuild each column explicitly so
        # that the row-level sort later uses category order (Census order) rather than
        # falling back to lexicographic ordering.
        for col in display_dims.columns:
            orig_cats = [c.rstrip(':').strip() for c in self.dims[col].cat.categories]
            display_dims[col] = pd.Categorical(
                display_dims[col], categories=orig_cats, ordered=True
            )

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
        # Deduplicate by index only (not by data values), so rows whose estimates
        # happen to be equal are not silently dropped.
        wide = wide[~wide.index.duplicated(keep='first')]
        # Sort rows by the categorical MultiIndex (Census-defined order, not alphabetical).
        wide = wide.sort_index(axis=0, sort_remaining=True)
        return wide

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
