"""Geography query construction, GEOID parsing, and geometry fetching for Census data.

Provides:
- Scope / SumLevel / GeoIDFQ dataclasses for building Census API queries
- SCOPES registry (lazy-loaded) of named geographic extents
- Functions for fetching GEOID lists and geometries from Census and TIGERweb APIs
- MORPC-specific GEOID translation utilities
"""
import functools
import logging
import os
import re
from dataclasses import dataclass
from geopandas import GeoDataFrame
from pandas import DataFrame, Series
from typing import Literal

logger = logging.getLogger(__name__)



@dataclass
class Scope:
    """A named Census API geography scope with its query parameters.

    Can be constructed fully (``Scope(name, for_param, in_param)``) or by name alone
    (``Scope("franklin")``), in which case ``for_param`` and ``in_param`` are looked up
    from the built-in ``SCOPES`` registry.
    """
    name: str
    for_param: str | None = None
    in_param: str | None = None

    def __post_init__(self) -> None:
        if self.for_param is None:
            if self.name not in SCOPES:
                raise ValueError(
                    f"Scope {self.name!r} not recognized. Available: {list(SCOPES.keys())}"
                )
            s = SCOPES[self.name]
            self.for_param = s.for_param
            self.in_param = s.in_param

    @property
    def params(self) -> dict:
        d = {"for": self.for_param}
        if self.in_param is not None:
            d["in"] = self.in_param
        return d
    
    @property
    def sql(self) -> str:
        if self.name == 'us':
            where = '1=1'
        else:
            scope_params = self.params
            wheres = []
            for param in scope_params:
                geo, ids = scope_params[param].split(':')
                ids_str = ", ".join(f"'{x}'" for x in ids.split(','))
                wheres.append(f"{geo.upper()} in ({ids_str})")
            where = " and ".join(wheres)
        return where


@dataclass(frozen=True)
class SumLevel:
    """A Census geography summary level, pairing a query name with its summary level code.

    Can be constructed fully (``SumLevel(name, sumlevel)``) or by a single argument:
    - ``SumLevel("county")`` — looks up the three-digit code from ``SUMLEVEL_DESCRIPTIONS``
    - ``SumLevel("050")``   — looks up the query name from ``SUMLEVEL_DESCRIPTIONS``
    """
    name: str
    sumlevel: str = ""                  # three-digit code, e.g. "050"; auto-filled when omitted
    singular: str | None = None         # singular display name, e.g. "county"
    plural: str | None = None           # plural display name, e.g. "counties"
    hierarchy_string: str | None = None # hierarchical label, e.g. "COUNTY"
    tigerweb_name: str | None = None    # TIGERweb REST API layer name, e.g. "counties"
    current_variant: str | None = None  # The variant current used by the Census. e.g. "00", "M7"

    def __post_init__(self) -> None:
        if self.sumlevel:
            return  # fully specified — nothing to do
        from morpc import SUMLEVEL_DESCRIPTIONS
        key = self.name
        if re.match(r"^\d{3}$", key):
            # Three-digit code passed as the only argument — look up the query name
            if key not in SUMLEVEL_DESCRIPTIONS:
                raise ValueError(f"Sumlevel code {key!r} not found in SUMLEVEL_DESCRIPTIONS")
            desc = SUMLEVEL_DESCRIPTIONS[key]
            object.__setattr__(self, "name", desc["censusQueryName"])
            object.__setattr__(self, "sumlevel", str(key))
        else:
            # Query name — look up the three-digit code
            for code, desc in SUMLEVEL_DESCRIPTIONS.items():
                if desc["censusQueryName"] == key:
                    object.__setattr__(self, "sumlevel", str(code))
                    break
            else:
                available = [
                    d["censusQueryName"]
                    for d in SUMLEVEL_DESCRIPTIONS.values()
                    if d["censusQueryName"] is not None
                ]
                raise ValueError(f"Sumlevel {key!r} not recognized. Available: {available}")
            
        object.__setattr__(self, "singular", desc["singular"])
        object.__setattr__(self, "plural", desc["plural"])
        object.__setattr__(self, "hierarchy_string", desc["hierarchy_string"])
        object.__setattr__(self, "tigerweb_name", desc["censusRestAPI_layername"])
        object.__setattr__(self, "current_variant", desc.get("current_variant"))
    
    def __repr__(self):
        return f"{self.sumlevel!r}"

    def get_query_req(self, year: str = '2023') -> dict:
        """Fetch Census API requirements for this sumlevel (required 'in' parameters and wildcards)."""
        from morpc import SUMLEVEL_FROM_CENSUSQUERY
        from morpc.req import get_json_safely

        sumlevel_code = SUMLEVEL_FROM_CENSUSQUERY[self.name]
        url = f"https://api.census.gov/data/{year}/geoinfo/geography.json"
        kw = {'params': {'key': k}} if (k := _get_api_key()) else {}
        json = get_json_safely(url, **kw)

        query_requirements: dict = {}
        for item in json['fips']:
            if item['geoLevelDisplay'] in sumlevel_code:
                query_requirements['requires'] = item.get('requires')
                query_requirements['wildcard'] = item.get('wildcard')

        logger.debug(f"{self.name} requires {query_requirements}")
        return query_requirements

    @property
    def parts(self) -> list[str]:
        """Geo component field names for GEOIDFQs of this sumlevel (e.g. ``['state', 'county']``)."""
        return [name for name, _ in _geoidfq_geo_fields(self.sumlevel)]


def _geoidfq_geo_fields(sumlevel: str) -> list[tuple[str, int]]:
    """Return the geo-specific (name, width) pairs for a sumlevel's geoidfq_format."""
    from morpc import SUMLEVEL_DESCRIPTIONS
    fmt = SUMLEVEL_DESCRIPTIONS[sumlevel].get("geoidfq_format")
    if fmt is None:
        raise ValueError(f"Sumlevel {sumlevel!r} has no geoidfq_format")
    return [
        (y[0].lower(), int(y[1]))
        for y in [x.split(":") for x in re.findall(r"\{(.+?)\}", fmt)]
        if y[0] not in ("SUMLEVEL", "VARIANT", "GEOCOMP")
    ]


class GeoIDFQ:
    """A parsed Census fully-qualified geographic identifier (GEOIDFQ).

    Structure: {SUMLEVEL:3}{VARIANT:2}{GEOCOMP:2}US{geo-specific fields...}

    Geo components (state, county, tract, etc.) are accessible as direct attributes.

    Variant codes (Census geo-variant system):
      "00"        standard/default — most geography types
      "01"–"59"   Congressional districts (add 100 for Congress number)
      "Ux"        upper-chamber state legislative districts
      "Lx"        lower-chamber state legislative districts
      "Mx"        CBSAs, metro divisions, combined statistical areas
      "Cx"        urban areas
      "Px"        public use microdata areas (PUMAs)
      "Zx"        ZIP Code tabulation areas
    """

    def __init__(self, sumlevel: "str | SumLevel", variant: str, geocomp: str, **kwargs: str) -> None:
        self.sumlevel = sumlevel
        self.variant = variant
        self.geocomp = geocomp
        self._geo_fields: list[str] = list(kwargs)
        for name, value in kwargs.items():
            setattr(self, name, value)

    def __repr__(self) -> str:
        sl_code = self.sumlevel.sumlevel if isinstance(self.sumlevel, SumLevel) else self.sumlevel
        fields = {"sumlevel": sl_code, "variant": self.variant, "geocomp": self.geocomp}
        fields.update({f: getattr(self, f) for f in self._geo_fields})
        return "GeoIDFQ(" + ", ".join(f"{k}={v!r}" for k, v in fields.items()) + ")"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GeoIDFQ) and str(self) == str(other)

    @classmethod
    def parse(cls, geoidfq: str) -> "GeoIDFQ":
        """Parse a GEOIDFQ string into its components."""
        sumlevel = SumLevel(geoidfq[0:3])
        variant = geoidfq[3:5]
        geocomp = geoidfq[5:7]
        parts: dict[str, str] = {}
        pos = 9  # skip SUMLEVEL(3) + VARIANT(2) + GEOCOMP(2) + "US"(2)
        for name, width in _geoidfq_geo_fields(sumlevel.sumlevel):
            parts[name] = geoidfq[pos:pos + width]
            pos += width
        return cls(sumlevel=sumlevel, variant=variant, geocomp=geocomp, **parts)

    @classmethod
    def build(cls, sumlevel: "str | SumLevel", variant: "str | None" = None, geocomp: str = "00", **kwargs: str) -> "GeoIDFQ":
        """Construct a GeoIDFQ from components.

        Raises ValueError if the sumlevel has no geoidfq_format (MORPC sumlevels)
        or if geo field kwargs do not match the expected fields for the sumlevel.
        """
        sl_code = sumlevel.sumlevel if isinstance(sumlevel, SumLevel) else sumlevel
        if variant is None:
            variant = sumlevel.current_variant if isinstance(sumlevel, SumLevel) and sumlevel.current_variant else "00"
        expected = [name for name, _ in _geoidfq_geo_fields(sl_code)]
        if list(kwargs.keys()) != expected:
            raise ValueError(
                f"geo field keys {list(kwargs.keys())} do not match expected {expected} "
                f"for sumlevel {sumlevel!r}"
            )
        return cls(sumlevel=sumlevel, variant=variant, geocomp=geocomp, **kwargs)

    def __str__(self) -> str:
        sl_code = self.sumlevel.sumlevel if isinstance(self.sumlevel, SumLevel) else self.sumlevel
        return sl_code + self.variant + self.geocomp + "US" + "".join(getattr(self, f) for f in self._geo_fields)

    @property
    def geoid(self) -> str:
        """Short-form ID after 'US' — used in REST API and Census API queries."""
        return "".join(getattr(self, f) for f in self._geo_fields)

    @property
    def parts(self) -> "dict[str, str]":
        """Dict of geo components (state, county, tract, etc.)."""
        return {f: getattr(self, f) for f in self._geo_fields}


# TODO (jinskeep_morpc): Develop function for fetching census geographies leveraging scopes
# Issue URL: https://github.com/morpc/morpc-py/issues/102
#   The current geos-lookup workflow is limited by size and scope. This function will be used
#   to fetch geographies at any scale and scope without the need to store it locally. It is 
#   limited to census geographies. 
#   [ ]: Consider storing the data as a remote frictionless resource similar to the acs data class.
#   [ ]: Define scale and scopes that are used. Possibly lists for benchmarking (i.e. Most populous cities)

class _LazyScopes(dict):
    """Dict of Scope objects built from morpc constants on first access.

    Defers ``import morpc`` (which triggers a Census API network call in morpc-py)
    until the dict is actually accessed, so ``import morpc_census`` never blocks.
    """

    def __init__(self) -> None:
        super().__init__()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        import morpc
        self.update({
            "us": Scope(name="us", for_param="us:1"),
            "columbuscbsa": Scope(
                name="columbuscbsa",
                for_param=(
                    "metropolitan statistical area/micropolitan statistical area:"
                    f"{morpc.CONST_COLUMBUS_CBSA_ID}"
                ),
            ),
        })
        for key, value in morpc.CONST_STATE_NAME_TO_ID.items():
            s = Scope(name=key, for_param=f"state:{int(value):02d}")
            self[s.name] = s
        for key, value in morpc.CONST_COUNTY_NAME_TO_ID.items():
            s = Scope(name=key.lower(), for_param=f"county:{int(value[2:6]):03d}", in_param="state:39")
            self[s.name] = s
        for name, region_key, in_param in [
            ("region15",     "15-County Region",  "state:39"),
            ("region10",     "10-County Region",  "state:39"),
            ("region7",      "7-County Region",   "state:39"),
            ("regioncorpo",  "CORPO Region",      "state:39"),
            ("regionceds",   "CEDS Region",       "state:39"),
            ("regioncbsa",   "CBSA",              "state:39"),
            ("regionmobility", "Mobility Region", "state:39"),
            ("regionmpo",    "MPO Region",        "state:39"),
        ]:
            fips = ",".join(morpc.CONST_COUNTY_NAME_TO_ID[x][2:6] for x in morpc.CONST_REGIONS[region_key])
            self[name] = Scope(name=name, for_param=f"county:{fips}", in_param=in_param)
        self._loaded = True

    def __getitem__(self, key):        self._load(); return super().__getitem__(key)
    def __contains__(self, key):      self._load(); return super().__contains__(key)
    def __iter__(self):               self._load(); return super().__iter__()
    def __len__(self):                self._load(); return super().__len__()
    def keys(self):                   self._load(); return super().keys()
    def values(self):                 self._load(); return super().values()
    def items(self):                  self._load(); return super().items()
    def get(self, key, default=None): self._load(); return super().get(key, default)


SCOPES: dict[str, Scope] = _LazyScopes()

## These are the available children sumelevels for the various parent level sumlevels when using the ucgid=psuedo() predicate.
## https://www.census.gov/data/developers/guidance/api-user-guide/ucgid-predicate.html
## See https://www2.census.gov/data/api-documentation/list-of-available-collections-of-geographies.xlsx for list of geographies.
PSEUDOS = {'010': [
    '0300000',
    '0400000',
    '04000S0',
    '0500000',
    '0600000',
    '1400000',
    '1500000',
    '1600000',
    '2500000',
    '2510000',
    '3100000',
    '31000M1',
    '31000M2',
    '3140000',
    '3300000',
    '3500000',
    '4000000',
    '5000000',
    '7950000',
    '8600000',
    '8610000',
    '9500000',
    '9600000',
    '9700000',
    '9800000',
    'E330000',
    'E600000',
    'E800000',
    'E810000'],
 '040': [
    '0500000',
    '0600000',
    '06V0000',
    '1000000',
    '1400000',
    '1500000',
    '1600000',
    '1700000',
    '2300000',
    '2500000',
    '3100000',
    '3500000',
    '4000000',
    '4200000',
    '5000000',
    '6100000',
    '6200000',
    '7000000',
    '7950000',
    '8600000',
    '8610000',
    '8710000',
    '9040000',
    '9500000',
    '9600000',
    '9700000',
    '9800000',
    'E600000'],
 '050': [
    '0600000',
    '06V0000',
    '1000000',
    '1400000',
    '1500000',
    '1600000',
    '7000000',
    '8600000',
    '8710000'],
 '060': ['1000000'],
 '140': ['1000000', '1500000'],
 '160': ['1000000', '1400000', '8600000', '8710000'],
 '250': ['1000000', '2510000', '5000000'],
 '310': [
    '0500000',
    '0600000',
    '1400000',
    '1500000',
    '1600000',
    '5000000',
    '8600000',
    'E600000'],
 '314': ['0500000',
    '0600000',
    '1400000',
    '1500000',
    '1600000',
    '5000000',
    '8600000'],
 '330': ['0500000',
    '0600000',
    '1400000',
    '1500000',
    '1600000',
    '3100000',
    '5000000'],
 '335': ['0600000'],
 '350': ['0500000', '0600000', '3520000'],
 '355': ['0600000'],
 '500': ['0500000', '0600000', '1400000', '1500000', '4000000'],
 '610': ['0600000', '1600000'],
 '620': ['0600000', '1600000'],
 '950': ['1000000'],
 '960': ['1000000'],
 '970': ['1000000']
 }


@functools.cache
def _get_api_key() -> str | None:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
    return os.environ.get('CENSUS_API_KEY')


def geoinfo_from_params(param_dict: dict, year: int = 2024, output: Literal['list','table','json'] = 'table') -> list | DataFrame:
    """Return GEOIDFQs from a Census geoinfo query using ucgid, for/in, or both parameters."""
    import morpc.req
    import pandas as pd
    url = f"https://api.census.gov/data/{year}/geoinfo"
    params = {'get': 'GEO_ID,NAME'}
    logger.debug(f"Updating params from {param_dict}")

    if 'ucgid' in param_dict:
        if 'pseudo' in param_dict['ucgid']:
            params.update({'ucgid': param_dict['ucgid']})
        else:
            logger.error(f"ucgid without pseudo. {params}")
            raise NotImplementedError
    elif 'for' in param_dict:
        params.update({'for': param_dict['for']})
        if 'in' in param_dict:
            params.update({'in': param_dict['in']})

    if k := _get_api_key():
        params['key'] = k
    logger.info(f"Getting GEOIDS from {url} and params: {params}.")
    json = morpc.req.get_json_safely(url, params=params)

    if output == 'list':
        return [GeoIDFQ.parse(row[0]) for row in json[1:]]
    if output == 'table':
        return pd.DataFrame.from_records(json[1:], columns=json[0]).reset_index().drop(columns='index')
    if output == 'json':
        return json


def geoids_from_scope(scope: str | Scope, output: Literal['list','table','json'] = 'list') -> list | DataFrame:
    """Return GEOIDFQs for all geographies in scope from the Census geoinfo API."""
    from morpc.req import get_json_safely
    import pandas as pd

    sc = scope if isinstance(scope, Scope) else SCOPES[scope]
    logger.debug(f"Fetching geoids from scope {sc.name!r}.")
    baseurl = "https://api.census.gov/data/2023/geoinfo?get=GEO_ID"
    params = dict(sc.params)
    if k := _get_api_key():
        params['key'] = k
    json = get_json_safely(baseurl, params=params)
    if output == 'list':
        return [GeoIDFQ.parse(row[0]) for row in json[1:]]
    if output == 'table':
        return pd.DataFrame.from_records(json[1:], columns=json[0]).reset_index().drop(columns='index')
    if output == 'json':
        return json


def get_query_req(sumlevel: str, year: str = '2023') -> dict:
    """Fetch Census API requirements for the given sumlevel. Delegates to SumLevel.get_query_req."""
    return SumLevel(sumlevel).get_query_req(year)


def pseudos_from_scope_sumlevel(
    sumlevel: str | SumLevel,
    scope: str | Scope,
    scope_geoids: "list[GeoIDFQ] | None" = None,
) -> list[str]:
    """Build ucgid pseudo predicates for each parent GEOID in scope at the given child sumlevel."""
    sl = sumlevel if isinstance(sumlevel, SumLevel) else SumLevel(sumlevel)
    sc = scope if isinstance(scope, Scope) else SCOPES[scope]

    logger.debug(f"Getting pseudo combinations for parents in {sc.name!r} at sumlevel {sl.name!r}")
    parents = scope_geoids if scope_geoids is not None else geoids_from_scope(sc)

    parent_sumlevel = parents[0].sumlevel.sumlevel
    child = f"{sl.sumlevel}0000"

    if child in PSEUDOS[parent_sumlevel]:
        logger.info(f"Returning pseudos for {child} in {parents}")
        return [f"{str(parent)}${child}" for parent in parents]

    logger.error(f"{child} is not an allowed child for parent sumlevel {parent_sumlevel!r}")
    raise ValueError(f"{sl.name!r} is not a valid child sumlevel for scope {sc.name!r}")


def geoinfo_for_hierarchical_geos(scope: str | Scope, sumlevel: str | SumLevel) -> DataFrame:
    """Build a geoinfo table for sumlevel/scope combinations that cannot be expressed as ucgid pseudos."""
    import pandas as pd
    sl = sumlevel if isinstance(sumlevel, SumLevel) else SumLevel(sumlevel)
    sc = scope if isinstance(scope, Scope) else SCOPES[scope]

    query_req = sl.get_query_req()
    scope_params = list(sc.params.values())
    in_scope = [x.split(":")[0] for x in scope_params if x.split(":")[0] in query_req['requires']]
    not_in_scope = [x for x in query_req['requires'] if x not in in_scope]

    parent_table = geoids_from_scope(sc, output='table').drop(columns='GEO_ID')

    for geo in not_in_scope:
        logger.info(f"Getting {geo} from {in_scope}")
        parent_table[geo] = None
        parent_table[geo] = parent_table[geo].astype('object')

        for i, row in parent_table.iterrows():
            in_param_str = [
                f"{x}:{','.join(row[x]) if isinstance(row[x], list) else row[x]}"
                for x in in_scope
            ]
            geoids = geoinfo_from_params({"in": in_param_str, "for": f"{geo}:*"}, output='table')[geo].to_list()
            logger.debug(f"at row:{i}, column:{geo} adding geoids {geoids}")
            parent_table.at[i, geo] = geoids

        in_scope += [geo]
        if len(in_scope) < len(query_req['requires']):
            parent_table = parent_table.explode(geo).reset_index().drop(columns='index')

    geoinfos = []
    for i, row in parent_table.iterrows():
        in_param_str = [
            f"{x}:{','.join(row[x]) if isinstance(row[x], list) else row[x]}"
            for x in in_scope
        ]
        geoinfo = geoinfo_from_params({"in": in_param_str, "for": f"{sl.name}:*"}, output='table')
        geoinfos.append(geoinfo)

    return pd.concat(geoinfos)


def geoinfo_from_scope_sumlevel(
    scope: str | Scope,
    sumlevel: str | SumLevel | None = None,
    output: Literal['list', 'table', 'json', 'params'] = 'list',
) -> list | DataFrame | dict:
    """Return GEOIDFQs for all geographies at sumlevel within scope.

    Parameters
    ----------
    scope : str | Scope
        A scope name or Scope instance (e.g. ``"franklin"``, ``"region15"``).
    sumlevel : str | SumLevel | None, optional
        Summary level name or SumLevel instance (e.g. ``"tract"``). Defaults to the scope's native level.
    output : {'list', 'table', 'json', 'params'}
        Return format: list of GEO_ID strings, DataFrame, JSON dict, or raw params dict.

    Raises
    ------
    ValueError
        When sumlevel and scope are an invalid combination.

    Examples
    --------
    Get a list of GEOIDFQs for all counties in the 15-county MORPC region:

    >>> from morpc_census import geoinfo_from_scope_sumlevel  # doctest: +SKIP
    >>> geoinfo_from_scope_sumlevel('region15')               # doctest: +SKIP
    ['0500000US39041', '0500000US39045', ...]

    Get a DataFrame of tracts within Franklin County:

    >>> geoinfo_from_scope_sumlevel('franklin', 'tract', output='table')  # doctest: +SKIP
    """
    sc = scope if isinstance(scope, Scope) else SCOPES[scope]
    scope_geoids = geoids_from_scope(sc)  # list[GeoIDFQ]
    scope_sl = scope_geoids[0].sumlevel   # SumLevel of the scope's native geography

    params: dict = {}
    geoinfo: DataFrame | None = None

    logger.debug(f"Building parameters for scope {sc.name!r} at sumlevel {sumlevel!r}.")

    if sumlevel is None:
        sl = SumLevel('county') if sc.name.startswith('region') else scope_sl
        logger.info(f"No sumlevel specified; using scope {sc.name!r} parameters.")
        params.update(sc.params)
        if output == 'params':
            return params
        geoinfo = geoinfo_from_params(params, output='table')

    else:
        sl = sumlevel if isinstance(sumlevel, SumLevel) else SumLevel(sumlevel)
        logger.info(f"SumLevel {sl.name!r} specified for scope {sc.name!r}.")

        if scope_sl == sl:
            logger.warning(f"Scope {sc.name!r} is already at {sl.name!r}; using scope parameters directly.")
            params.update(sc.params)
            if output == 'params':
                return params
            geoinfo = geoinfo_from_params(params, output='table')

        else:
            try:
                pseudos = pseudos_from_scope_sumlevel(sl, sc, scope_geoids=scope_geoids)
                params['ucgid'] = f"pseudo({','.join(pseudos)})"
                if output == 'params':
                    return params
                geoinfo = geoinfo_from_params(params, output='table')

            except ValueError:
                logger.info("Pseudo predicates unavailable; using hierarchical for/in approach.")
                # (https://www.census.gov/data/developers/guidance/api-user-guide/ucgid-predicate.html)
                if output == 'params':
                    return {'ucgid': ','.join(geoinfo_for_hierarchical_geos(sc, sl)['GEO_ID'])}
                geoinfo = geoinfo_for_hierarchical_geos(sc, sl)

    if output == 'table':
        return geoinfo
    if output == 'json':
        return geoinfo.set_index('GEO_ID').to_dict()['NAME']
    if output == 'list':
        return [GeoIDFQ.parse(fq) for fq in geoinfo['GEO_ID']]

def _fetch_layer(sumlevel: SumLevel, geoids: list[str], year: int | None, survey: str, chunk_size: int) -> "GeoDataFrame":
    """Fetch geometries for a single sumlevel from TIGERweb, chunking the geoid list as needed."""
    import morpc
    from morpc_census.tigerweb import get_layer_url
    import pandas as pd

    if sumlevel.tigerweb_name is None:
        logger.error(f"Sumlevel {sumlevel!r} has no TIGERweb layer.")
        raise NotImplementedError(f"Sumlevel {sumlevel!r} has no TIGERweb layer")

    url = get_layer_url(layer_name=sumlevel.tigerweb_name, year=year, survey=survey)
    logger.info(f"Fetching {sumlevel.name} geometries ({len(geoids)} records) from {url}")

    chunks = [geoids[i:i + chunk_size] for i in range(0, len(geoids), chunk_size)]
    results = []
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            logger.info(f"  chunk {i + 1}/{len(chunks)} ({len(chunk)} records)")
        where = "GEOID in ({})".format(",".join(f"'{g}'" for g in chunk))
        resource = morpc.rest_api.resource(name='temp', url=url, where=where, outfields='GEOID', max_record_count=chunk_size)
        results.append(morpc.rest_api.gdf_from_resource(resource))

    return pd.concat(results)


def fetch_geos_from_geoids(geoidfqs: list[GeoIDFQ], year: int | None = None, survey: Literal['current', 'ACS', 'DEC'] = 'current', chunk_size: int = 500) -> GeoDataFrame:
    """Fetch geometries from the TIGERweb REST API for a list of GeoIDFQ objects."""
    import pandas as pd
    import geopandas as gpd

    by_sumlevel: dict[SumLevel, list[str]] = {}
    for g in geoidfqs:
        by_sumlevel.setdefault(g.sumlevel, []).append(g.geoid)

    logger.info(f"Fetching geometries for sumlevels: {', '.join(sl.name for sl in by_sumlevel)}")

    results = [
        _fetch_layer(sl, geoids, year, survey, chunk_size)
        for sl, geoids in by_sumlevel.items()
    ]

    geometries = pd.concat(results).rename(columns={'GEOID': 'GEO_ID'})
    return gpd.GeoDataFrame(geometries, geometry='geometry')

def fetch_geos_from_scope_sumlevel(scope: str | Scope, sumlevel: str | SumLevel | None = None, year: int | None = None, survey: Literal['current', 'ACS', 'DEC'] = 'current', chunk_size: int = 500) -> GeoDataFrame:
    """Fetch a GeoDataFrame of geometries for all geographies at sumlevel within scope.

    Parameters
    ----------
    scope : str | Scope
        A scope name (e.g. ``'region15'``, ``'franklin'``) or ``Scope`` instance.
    sumlevel : str | SumLevel | None, optional
        Summary level (e.g. ``'tract'``, ``'county'``). Defaults to the scope's native level.
    year : int, optional
        Vintage year for ACS or Decennial TIGERweb services. Not used for ``survey='current'``.
    survey : {'current', 'ACS', 'DEC'}
        TIGERweb service to query. Defaults to ``'current'`` (most recent geometries).
    chunk_size : int
        Max features per TIGERweb request. Defaults to 500.

    Returns
    -------
    geopandas.GeoDataFrame
        Boundary geometries with ``GEOID``, ``NAME``, and ``geometry`` columns.

    Examples
    --------
    Get county boundaries for the 15-county MORPC region:

    >>> from morpc_census import fetch_geos_from_scope_sumlevel  # doctest: +SKIP
    >>> geos = fetch_geos_from_scope_sumlevel('region15')        # doctest: +SKIP
    >>> geos.plot()                                              # doctest: +SKIP

    Get tract boundaries within Franklin County:

    >>> tracts = fetch_geos_from_scope_sumlevel('franklin', 'tract')  # doctest: +SKIP
    """
    sc = scope if isinstance(scope, Scope) else SCOPES[scope]
    sl = sumlevel if isinstance(sumlevel, SumLevel) or sumlevel is None else SumLevel(sumlevel)

    geoinfo = geoinfo_from_scope_sumlevel(sc, sl, output='table')
    parsed = [GeoIDFQ.parse(fq) for fq in geoinfo['GEO_ID']]
    geoinfo['GEOIDFQ'] = geoinfo['GEO_ID']
    geoinfo['GEO_ID'] = [g.geoid for g in parsed]
    geos = fetch_geos_from_geoids(parsed, year, survey, chunk_size=chunk_size)

    try:
        geos = geos.set_index('GEO_ID').join(geoinfo.set_index('GEO_ID')).reset_index()
        geos = geos.loc[~geos['geometry'].isna()]
    except Exception as e:
        logger.error(f"Unable to join geoinfo to geos, returning only geos.")

    return geos

def morpc_juris_part_to_full(geoidSeries: Series, validateTranslation: bool = True, gitRootPath: str = "../") -> DataFrame:
    """Given a series of fully-qualified MORPC GEOIDs representing county parts of MORPC jurisdictions (i.e. SUMLEVEL
    M11 or M25), this function provides a dataframe which maps each part to its parent jurisdiction (M10 or M24, respectively).

    Parameters
    ----------
    geoidSeries : pandas.core.series.Series
        A Pandas Series object which contains a list of MORPC GEOIDs. GEOIDs must belong to a single SUMLEVEL. The following 
        SUMLEVELs are supported:
            M11 - County parts for active and prospective MORPC member jurisdictions, including non-incorporated townships and
                  parts of cities and villages.  These geographies are defined using Census-maintained (i.e. TIGER) boundaries.
                  Also known as "JURIS-COUNTY" geos.
            M25 - Like M11, these are county parts of cities, villages, and non-incorporated townships, however these are defined 
                  using MORPC-maintained boundaries rather than Census boundaries.  Also known as "JURIS-COUNTY-MORPC" geos.
    validateTranslation : bool
        When validateTranslation is True (default), the function will attempt to validate the provided list of GEOIDs using the 
        lookup table output from the morpc-geos-collect workflow prior to attempting to map them to their parents.  If validateTranslation 
        is False or if the lookup table is not available, the function will attempt a naive mapping of the GEOID, however the 
        resulting parent GEOID may not be valid.  The lookup table is located via gitRootPath (see below).
    gitRootPath : str
        The path to a folder containing the Git repository for the morpc-geos-collect workflow. Defaults to the parent 
        directory ("../").  .

    Returns
    -------
    mappingDataFrame : pandas.core.frame.DataFrame
        A Pandas DataFrame object which maps each of the input MORPC GEOIDs to its parent MORPC GEOID.

    """
    import pandas as pd
    import logging
    import os
    import morpc

    logger  = logging.getLogger(__name__)    

    supportedSumlevels = ["M11","M25"]

    # Create a copy of the user-provided series that we can manipulate. Preserve the name of the series so we can name
    # the returned series accordingly
    myGeoidSeries = geoidSeries.copy()
    myGeoidSeries.name = "GEOIDFQ"

    # Convert the series to a dataframe and extract the original sumlevels. Create a field to capture the
    # translated GEOID
    df = pd.DataFrame(myGeoidSeries)
    df["SUMLEVEL_ORIG"] = df["GEOIDFQ"].apply(lambda x:x[0:3])
    df["GEOIDFQ_PARENT"] = None
    df["GEOIDFQ_PARENT"] = df["GEOIDFQ_PARENT"].astype("string")
    df = df.set_index("GEOIDFQ")

    # Verify that only a single SUMLEVEL is represented among the provided GEOIDs
    includedSumlevels = df["SUMLEVEL_ORIG"].unique()
    if(len(includedSumlevels) > 1):
        logger.error(f"Detected multiple SUMLEVELs among input GEOIDs. GEOIDs must belong to a single SUMLEVEL (i.e. one of the following: {','.join(supportedSumlevels)})")
        raise RuntimeError

    # Assuming we made it this far, we can extract the single input SUMLEVEL
    includedSumlevel = includedSumlevels[0]
    
    # Verify that the SUMLEVELs for the user-provided GEOIDs are all supported
    if(not includedSumlevel in supportedSumlevels):
        logger.error(f"SUMLEVEL {includedSumlevel} is not supported. Supported sumlevels are {','.join(supportedSumlevels)}")
        raise RuntimeError

    # Try to load the geography lookup table output from the morpc-geos-collect workflow from a local copy of the repository
    # to be found in a subdirectory of gitRootPath. This is the authoritative list of geographies known to MORPC. If a GEOID
    # is not listed here it may be invalid. If the attempt to load the lookup table fails, flag the translation as naive
    # and warn the user.
    naiveTranslation = True
    if(validateTranslation == True):
        try:
            morpcGeosLookupPath = os.path.join(os.path.normpath(gitRootPath), "morpc-geos-collect", "output_data", "morpc-geos-lookup.resource.yaml")
            logger.info(f"Attempting to load MORPC geography lookup table from path: {morpcGeosLookupPath}")
            (morpcGeosLookup, morpcGeosLookupResource, morpcGeosLookupSchema) = morpc.frictionless.load_data(morpcGeosLookupPath)
            morpcGeosLookup = morpcGeosLookup.set_index("GEOIDFQ")
            # Add a "VALID" flag to the records in the table. When joined to the user-provided GEOIDs, a missing flag will
            # indicate that the GEOID was not found in the lookup table.
            morpcGeosLookup["VALID"] = True
            morpcGeosLookup["VALID"] = morpcGeosLookup["VALID"].astype("bool")
            naiveTranslation = False
        except Exception as e:
            logger.warning("Failed to MORPC geography lookup table. GEOID mappings will be naive (not validated). {e}")
    else:
        logger.warning("Not attempting to validate translations per user instruction. GEOID mappings will be naive.")

    # If the user requested to validate the GEOIDs and we were able to load the lookup table, join the VALID
    # flag from the lookup table for each user-provided GEOID that appears in the lookup table. If some GEOIDs
    # did not appear in the lookup table, warn the user and continue.
    if(not naiveTranslation):            
        df = df.join(morpcGeosLookup["VALID"])
        missingIndex = df.loc[df["VALID"] != True].index
        if(not len(missingIndex) == 0):
            logger.warn("The following GEOIDs were not found in the MORPC geography lookup table and may be invalid.  If so, their mappings will also be invalid.")
            logger.warn(f"Missing GEOIDs: {",".join(list(missingIndex))}")
        df = df.drop(columns="VALID")

    if(includedSumlevel == "M11"):
        parentSumlevel = "M10"
    elif(includedSumlevel == "M25"):
        parentSumlevel = "M24"
    else:
        logger.error(f"SUMLEVEL {includedSumlevel} is not supported. Supported sumlevels are {','.join(supportedSumlevels)}")
        raise RuntimeError

    logger.info(f"Mapping provided geographies in SUMLEVEL {includedSumlevel} ({morpc.HIERARCHY_STRING_LOOKUP[includedSumlevel]}) to parent geographies in SUMLEVEL {parentSumlevel} ({morpc.HIERARCHY_STRING_LOOKUP[parentSumlevel]})")
        
    df = df.reset_index()
    # Identify townships. Township GEOIDs end in 99999
    df["TOWNSHIP"] = df["GEOIDFQ"].str.endswith("99999")
    # Modify the GEOIDs for ALL geographies by changing the SUMLEVEL
    df["GEOIDFQ_PARENT"] = df["GEOIDFQ"].str.slice_replace(start=0, stop=3, repl=parentSumlevel)
    # Modify the GEOIDs for the NON-township geographies only (i.e. cities and villages). In this case we need to remove the 
    # three-digit suffix that specifies the county that the part belongs to.
    df.loc[df["TOWNSHIP"] != True, "GEOIDFQ_PARENT"] = df["GEOIDFQ_PARENT"].str.slice_replace(start=-3)
    
    # Check for null values in the translated GEOIDs. Throw an error if any appear.
    failedTranslationIndex = df.loc[df["GEOIDFQ_PARENT"].isna()].index
    if(len(failedTranslationIndex) > 0):
        logger.error(f"Translation failed for the following geographies: {list(failedTranslationIndex)}")
        raise RuntimeError

    df = df.filter(items=["GEOIDFQ","GEOIDFQ_PARENT"], axis="columns")

    # If the user requested to validate the GEOIDs and we were able to load the lookup table, join the VALID
    # flag from the lookup table for each parent GEOID that appears in the lookup table. If some GEOIDs
    # did not appear in the lookup table, warn the user and continue.
    if(not naiveTranslation):            
        df = df.merge(morpcGeosLookup["VALID"], left_on="GEOIDFQ_PARENT", right_on="GEOIDFQ")
        missingIndex = df.loc[df["VALID"] != True].index
        if(not len(missingIndex) == 0):
            logger.warn("The following parent GEOIDs were not found in the MORPC geography lookup table and may be invalid.")
            logger.warn(f"Missing parent GEOIDs: {",".join(list(missingIndex))}")
        df = df.drop(columns="VALID")
    
    mappingDataFrame = df.copy()
    
    return mappingDataFrame
    
def census_geoid_to_morpc(geoidSeries: Series, targetSumlevel: str, validateTranslation: bool = True, gitRootPath: str = "../", verbose: bool = False) -> DataFrame:
    """Given a series of fully-qualified Census GEOIDs and a target MORPC SUMLEVEL, this function translates each GEOID in 
    the series to its equivalent MORPC GEOID.  MORPC maintains a set of fully-qualified geographic identifiers (GEOIDFQs) 
    that mimic the fully qualified GEOIDs used by the Census Bureau. Similar to Census GEOIDFQs, MORPC GEOIDFQs have the form 
    XXX0000USYYYYYYYY, where XXX reflects the three-digit geographic summary level (SUMLEVEL) of the geography, "0000US" is a 
    string literal which means nothing but is included to mimic Census GEOIDFQs, and YYYYYYYY short-form GEOID for each geography
    that is unique within the SUMLEVEL and whose length and composition depends on the SUMLEVEL. For cases where a MORPC geography
    corresponds to a Census geography, the short-form GEOID must match the Census geography, however a MORPC SUMLEVEL may be
    comprised of geographies from multiple Census SUMLEVELs. Nonetheless, the short-form GEOID must still be unique within
    the MORPC SUMLEVEL.  There is a one-to-many mapping between a Census GEOID and MORPC GEOIDs, therefore it is necessary to
    specify the target SUMLEVEL for the desired MORPC GEOIDs.

    See also morpc.census.geos.morpc_geoid_to_census()
    
    Parameters
    ----------
    geoidSeries : pandas.core.series.Series
        A Pandas Series object which contains a list of Census GEOIDs. GEOIDs from multiple SUMLEVELs may be 
        included. The included GEOIDs must translate to a single MORPC SUMLEVEL, as specified by targetSumlevel (see below). 
        The Census SUMLEVELs which may be included vary depending on the MORPC SUMLEVEL.
    targetSumlevel : str
        A three-digit string designating the MORPC SUMLEVEL to use when translating the Census GEOIDs.  The following SUMLEVELs
        are supported:
            M10 - Active and prospective MORPC member jurisdictions, including whole cities and villages (Census SUMLEVEL 160),
                  and non-incorporated townships (Census SUMLEVEL 070).  Non-incorporated townships are assumed to be fully 
                  contained in a single county. As of March 2026, there are some cases where townships span county boundaries, 
                  however in each of these cases the out-of-county portion of the township is coterminus with a city or village 
                  and thus the incorporated place takes precedence. These geographies are defined using Census-maintained (i.e. TIGER) 
                  boundaries. Also known as "JURIS" geos.
            M11 - County parts for active and prospective MORPC member jurisdictions, including non-incorporated townships (Census
                  SUMLEVEL 070) and parts of cities and villages (Census SUMLEVEL 155).  These geographies are defined using Census-
                  maintained (i.e. TIGER) boundaries. Also known as "JURIS-COUNTY" geos.
            M23 - These are whole counties (like Census SUMLEVEL 050), however they are defined using MORPC-maintained boundaries
                  rather than Census boundaries. Also known as "COUNTY-MORPC" geos.
            M24 - Like M10, these are whole cities, villages, and non-incorporated townships, however these are defined using
                  MORPC-maintained boundaries rather than Census boundaries.  Also known as "JURIS-MORPC" geos.
            M25 - Like M11, these are county parts of cities, villages, and non-incorporated townships, however these are defined 
                  using MORPC-maintained boundaries rather than Census boundaries.  Also known as "JURIS-COUNTY-MORPC" geos.
    validateTranslation : bool
        When validateTranslation is True (default), the function will attempt to validate the provided list of GEOIDs using the 
        lookup table output from the morpc-geos-collect workflow prior to attempting to translate them.  If validateTranslation 
        is False or if the lookup table is not availble, the function will attempt a naive translation of the GEOID, however the 
        resulting GEOID may not be valid.  The lookup table is located via gitRootPath (see below).
    gitRootPath : str
        The path to a folder containing the Git repository for the morpc-geos-collect workflow. Defaults to the parent 
        directory ("../").  .
    verbose : bool
        Set verbose to True to increase logging output from the function.

    Returns
    -------
    mappingDataFrame : pandas.core.frame.DataFrame
        A Pandas DataFrame object which maps each of the input Census GEOIDs to its MORPC equivalent in the context of the 
        specified target SUMLEVEL.
    """
    import pandas as pd
    import logging
    import os
    import morpc

    logger  = logging.getLogger(__name__)    

    supportedSumlevels = ["M10","M11","M23","M24","M25"]

    sumlevelMap = {
        "M10": [morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"], morpc.SUMLEVEL_LOOKUP["PLACE"]],
        "M11": [morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"], morpc.SUMLEVEL_LOOKUP["PLACE-COUNTY"]],
        "M23": [morpc.SUMLEVEL_LOOKUP["COUNTY"]],
        "M24": [morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"], morpc.SUMLEVEL_LOOKUP["PLACE"]],
        "M25": [morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"], morpc.SUMLEVEL_LOOKUP["PLACE-COUNTY"]]
    }
    
    # Verify that the user-specified target SUMLEVEL is supported
    if(not targetSumlevel in supportedSumlevels):
        logger.error(f"SUMLEVEL {sumlevel} is not supported. Supported sumlevels are {','.join(supportedSumlevels)}")
        raise RuntimeError
    
    # Create a copy of the user-provided series that we can manipulate. Preserve the name of the series so we can name
    # the returned series accordingly
    myGeoidSeries = geoidSeries.copy()
    myGeoidSeries.name = "GEOIDFQ"

    # Convert the series to a dataframe and extract the original sumlevels. Create a field to capture the
    # translated GEOID
    df = pd.DataFrame(myGeoidSeries)
    df["SUMLEVEL_ORIG"] = df["GEOIDFQ"].apply(lambda x:x[0:3])
    df["GEOIDFQ_MORPC"] = None
    df["GEOIDFQ_MORPC"] = df["GEOIDFQ_MORPC"].astype("string")
    df = df.set_index("GEOIDFQ")

    # Check the Census SUMLEVELs present in the series to make sure they have equivalents in the user-specified target
    # SUMLEVEL. If the set of SUMLEVELs included in the series is not a subset of the possible Census SUMLEVELS in the
    # target MORPC SUMLEVEL, then throw an error.
    if(not (set(df["SUMLEVEL_ORIG"].unique()) <= set(sumlevelMap[targetSumlevel]))):
        logger.error(f"The user-provided series includes geographies in the following SUMLEVEL(s) which do not have equivalent geographies in the target SUMLEVEL ({targetSumlevel}): {",".join(list(set(df["SUMLEVEL_ORIG"].unique()) - set(sumlevelMap[targetSumlevel])))}")
        raise RuntimeError
    
    # Try to load the geography lookup table output from the morpc-geos-collect workflow from a local copy of the repository
    # to be found in a subdirectory of gitRootPath. This is the authoritative list of geographies known to MORPC. If a GEOID
    # is not listed here it may be invalid. If the attempt to load the lookup table fails, flag the translation as naive
    # and warn the user.
    naiveTranslation = True
    if(validateTranslation == True):
        try:
            morpcGeosLookupPath = os.path.join(os.path.normpath(gitRootPath), "morpc-geos-collect", "output_data", "morpc-geos-lookup.resource.yaml")
            logger.info(f"Attempting to load MORPC geography lookup table from path: {morpcGeosLookupPath}")
            (morpcGeosLookup, morpcGeosLookupResource, morpcGeosLookupSchema) = morpc.frictionless.load_data(morpcGeosLookupPath)
            morpcGeosLookup = morpcGeosLookup.set_index("GEOIDFQ")
            # Add a "VALID" flag to the records in the table. When joined to the user-provided GEOIDs, a missing flag will
            # indicate that the GEOID was not found in the lookup table.
            morpcGeosLookup["VALID"] = True
            morpcGeosLookup["VALID"] = morpcGeosLookup["VALID"].astype("bool")
            naiveTranslation = False
        except Exception as e:
            logger.warning(f"Failed to MORPC geography lookup table. GEOID translations will be naive (not validated). {e}")
    else:
        logger.warning("Not attempting to validate translations per user instruction. GEOID translations will be naive.")

    # If the user requested to validate the GEOIDs and we were able to load the lookup table, join the VALID
    # flag from the lookup table for each user-provided GEOID that appears in the lookup table. If some GEOIDs
    # did not appear in the lookup table, warn the user and continue.
    if(not naiveTranslation):            
        df = df.join(morpcGeosLookup["VALID"])
        missingIndex = df.loc[df["VALID"] != True].index
        if(not len(missingIndex) == 0):
            logger.warn("The following GEOIDs were not found in the MORPC geography lookup table and may be invalid.  If so, their translations will also be invalid.")
            logger.warn(f"Missing GEOIDs: {",".join(list(missingIndex))}")
        df = df.drop(columns="VALID")

    # Since we've already verified that all of the user-provided geographies have an equivalent in the target SUMLEVEL, we can simply replace
    # the SUMLEVEL portion of their GEOIDs all at once.
    df = df.reset_index()
    df["GEOIDFQ_MORPC"] = df["GEOIDFQ"].str.slice_replace(start=0, stop=3, repl=targetSumlevel)

    # Check for null values in the translated GEOIDs. Throw an error if any appear.
    failedTranslationIndex = df.loc[df["GEOIDFQ_MORPC"].isna()].index
    if(len(failedTranslationIndex) > 0):
        logger.error(f"Translation failed for the following geographies: {list(failedTranslationIndex)}")
        raise RuntimeError
        
    df = df.filter(items=["GEOIDFQ","GEOIDFQ_MORPC"], axis="columns")

    # If the user requested to validate the GEOIDs and we were able to load the lookup table, join the VALID
    # flag from the lookup table for each parent GEOID that appears in the lookup table. If some GEOIDs
    # did not appear in the lookup table, warn the user and continue.
    if(not naiveTranslation):            
        df = df.merge(morpcGeosLookup["VALID"], left_on="GEOIDFQ_MORPC", right_on="GEOIDFQ", how="left")
        missingGeoids = df.loc[df["VALID"] != True, "GEOIDFQ_MORPC"]
        if(not len(missingGeoids) == 0):
            logger.warn(f"The following GEOIDs were not found in the MORPC geography lookup table and may be invalid: {','.join(list(missingGeoids))}")
        df = df.drop(columns="VALID")
    
    mappingDataFrame = df.copy().rename(columns={"GEOIDFQ":geoidSeries.name})
    
    return mappingDataFrame
    
def morpc_geoid_to_census(geoidSeries: Series, validateTranslation: bool = True, gitRootPath: str = "../", verbose: bool = False) -> DataFrame:
    """Given a series of fully-qualified MORPC GEOIDs, this function translates each GEOID in the series to its equivalent
    Census GEOID.  MORPC maintains a set of fully-qualified geographic identifiers (GEOIDFQs) that mimic the fully qualified 
    GEOIDs used by the Census Bureau. Similar to Census GEOIDFQs, MORPC GEOIDFQs have the form XXX0000USYYYYYYYY, where XXX 
    reflects the three-digit geographic summary level (SUMLEVEL) of the geography, "0000US" is a string literal which 
    means nothing but is included to mimic Census GEOIDFQs, and YYYYYYYY short-form GEOID for each geography that is 
    unique within the SUMLEVEL and whose length and composition depends on the SUMLEVEL. For cases where a MORPC geography
    corresponds to a Census geography, the short-form GEOID must match the Census geography, however a MORPC SUMLEVEL may be
    comprised of geographies from multiple Census SUMLEVELs. Nonetheless, the short-form GEOID must still be unique within
    the MORPC SUMLEVEL.

    See also morpc.census.geos.census_geoid_to_morpc()
    
    Parameters
    ----------
    geoidSeries : pandas.core.series.Series
        A Pandas Series object which contains a list of MORPC GEOIDs. GEOIDs from multiple SUMLEVELs may be 
        included, in which case each SUMLEVEL will be handled separately.  The following SUMLEVELs are supported:
            M10 - Active and prospective MORPC member jurisdictions, including whole cities, villages, and non-incorporated
                  townships.  Non-incorporated townships are assumed to be fully contained in a single county. As of March 2026,
                  there are some cases where townships span county boundaries, however in each of these cases the out-of-county
                  portion of the township is coterminus with a city or village and thus the incorporated place takes precedence.
                  These geographies are defined using Census-maintained (i.e. TIGER) boundaries. Also known as "JURIS" geos.
            M11 - County parts for active and prospective MORPC member jurisdictions, including non-incorporated townships and
                  parts of cities and villages.  These geographies are defined using Census-maintained (i.e. TIGER) boundaries.
                  Also known as "JURIS-COUNTY" geos.
            M23 - These are whole counties (like Census SUMLEVEL 050), however they are defined using MORPC-maintained boundaries
                  rather than Census boundaries. Also known as "COUNTY-MORPC" geos.
            M24 - Like M10, these are whole cities, villages, and non-incorporated townships, however these are defined using
                  MORPC-maintained boundaries rather than Census boundaries.  Also known as "JURIS-MORPC" geos.
            M25 - Like M11, these are county parts of cities, villages, and non-incorporated townships, however these are defined 
                  using MORPC-maintained boundaries rather than Census boundaries.  Also known as "JURIS-COUNTY-MORPC" geos.
    validateTranslation : bool
        When validateTranslation is True (default), the function will attempt to validate the provided list of GEOIDs using the 
        lookup table output from the morpc-geos-collect workflow prior to attempting to translate them.  If validateTranslation 
        is False or if the lookup table is not availble, the function will attempt a naive translation of the GEOID, however the 
        resulting GEOID may not be valid.  The lookup table is located via gitRootPath (see below).
    gitRootPath : str
        The path to a folder containing the Git repository for the morpc-geos-collect workflow. Defaults to the parent 
        directory ("../").  .
    verbose : bool
        Set verbose to True to increase logging output from the function.

    Returns
    -------
    mappingDataFrame : pandas.core.frame.DataFrame
        A Pandas DataFrame object which maps each of the input MORPC GEOIDs to its Census equivalent

    """
    import pandas as pd
    import logging
    import morpc
    import os

    logger  = logging.getLogger(__name__)    

    supportedSumlevels = ["M10","M11","M23","M24","M25"]

    # Create a copy of the user-provided series that we can manipulate. Preserve the name of the series so we can name
    # the returned series accordingly
    myGeoidSeries = geoidSeries.copy()
    myGeoidSeries.name = "GEOIDFQ"

    # Convert the series to a dataframe and extract the original sumlevels. Create a field to capture the
    # translated GEOID
    df = pd.DataFrame(myGeoidSeries)
    df["SUMLEVEL_ORIG"] = df["GEOIDFQ"].apply(lambda x:x[0:3])
    df["GEOIDFQ_CENSUS"] = None
    df["GEOIDFQ_CENSUS"] = df["GEOIDFQ_CENSUS"].astype("string")
    df = df.set_index("GEOIDFQ")

    # Verify that the SUMLEVELs for the user-provided GEOIDs are all supported
    for sumlevel in df["SUMLEVEL_ORIG"].unique():
        if(not sumlevel in supportedSumlevels):
            logger.error(f"SUMLEVEL {sumlevel} is not supported. Supported sumlevels are {','.join(supportedSumlevels)}")
            raise RuntimeError

    # Try to load the geography lookup table output from the morpc-geos-collect workflow from a local copy of the repository
    # to be found in a subdirectory of gitRootPath. This is the authoritative list of geographies known to MORPC. If a GEOID
    # is not listed here it may be invalid. If the attempt to load the lookup table fails, flag the translation as naive
    # and warn the user.
    naiveTranslation = True
    if(validateTranslation == True):
        try:
            morpcGeosLookupPath = os.path.join(os.path.normpath(gitRootPath), "morpc-geos-collect", "output_data", "morpc-geos-lookup.resource.yaml")
            logger.info(f"Attempting to load MORPC geography lookup table from path: {morpcGeosLookupPath}")
            (morpcGeosLookup, morpcGeosLookupResource, morpcGeosLookupSchema) = morpc.frictionless.load_data(morpcGeosLookupPath)
            morpcGeosLookup = morpcGeosLookup.set_index("GEOIDFQ")
            # Add a "VALID" flag to the records in the table. When joined to the user-provided GEOIDs, a missing flag will
            # indicate that the GEOID was not found in the lookup table.
            morpcGeosLookup["VALID"] = True
            morpcGeosLookup["VALID"] = morpcGeosLookup["VALID"].astype("bool")
            naiveTranslation = False
        except Exception as e:
            logger.warning("Failed to MORPC geography lookup table. GEOID translations will be naive (not validated). {e}")
    else:
        logger.warning("Not attempting to validate translations per user instruction. GEOID translations will be naive.")

    # If the user requested to validate the GEOIDs and we were able to load the lookup table, join the VALID
    # flag from the lookup table for each user-provided GEOID that appears in the lookup table. If some GEOIDs
    # did not appear in the lookup table, warn the user and continue.
    if(not naiveTranslation):            
        df = df.join(morpcGeosLookup["VALID"])
        missingIndex = df.loc[df["VALID"] != True].index
        if(not len(missingIndex) == 0):
            logger.warn("The following GEOIDs were not found in the MORPC geography lookup table and may be invalid.  If so, their translations will also be invalid.")
            logger.warn(f"Missing GEOIDs: {",".join(list(missingIndex))}")
        df = df.drop(columns="VALID")

    # We'll handle the collection of geographies in each SUMLEVEL separately. Iterate through the SUMLEVELs
    for sumlevel in df["SUMLEVEL_ORIG"].unique():
        if(verbose):
            logger.info(f"Processing geographies in SUMLEVEL {sumlevel} ({morpc.HIERARCHY_STRING_LOOKUP[sumlevel]})")
        # Extract only the records in this SUMLEVEL
        thisSumlevel = df.loc[df["SUMLEVEL_ORIG"] == sumlevel].copy().reset_index()
        if(sumlevel == "M10" or sumlevel == "M24"):
            if(verbose):
                logger.info(f"MORPC SUMLEVEL {sumlevel} is comprised of complete cities, villages, and non-incorporated townships.")
                logger.info(f"Substituting Census SUMLEVEL {morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"]} for non-incorporated townships.")
                logger.info(f"Substituting Census SUMLEVEL {morpc.SUMLEVEL_LOOKUP["PLACE"]} for places (cities and villages.)")
            # Identify townships. Township GEOIDs end in 99999
            thisSumlevel["TOWNSHIP"] = thisSumlevel["GEOIDFQ"].str.endswith("99999")
            # Modify the GEOIDs for the township geographies
            thisSumlevel.loc[thisSumlevel["TOWNSHIP"] == True, "GEOIDFQ_CENSUS"] = morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"] + thisSumlevel["GEOIDFQ"].str.removeprefix(sumlevel)
            # Modify the GEOIDs for the place (city, village) geographies
            thisSumlevel.loc[thisSumlevel["TOWNSHIP"] != True, "GEOIDFQ_CENSUS"] = morpc.SUMLEVEL_LOOKUP["PLACE"] + thisSumlevel["GEOIDFQ"].str.removeprefix(sumlevel)
            # Drop the township identifier flag
            thisSumlevel = thisSumlevel.drop(columns=["TOWNSHIP"])
            # Update the values for this SUMLEVEL only in the working dataframe
            df.update(thisSumlevel.set_index("GEOIDFQ"))
        elif(sumlevel == "M11" or sumlevel == "M25"):
            if(verbose):
                logger.info(f"MORPC SUMLEVEL {sumlevel} is comprised of county parts of cities, villages, and non-incorporated townships.")
                logger.info(f"Substituting Census SUMLEVEL {morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"]} for county parts of non-incorporated townships.")
                logger.info(f"Substituting Census SUMLEVEL {morpc.SUMLEVEL_LOOKUP["PLACE-COUNTY"]} for county parts of places (cities and villages).")
            # Identify townships. Township GEOIDs end in 99999
            thisSumlevel["TOWNSHIP"] = thisSumlevel["GEOIDFQ"].str.endswith("99999")
            # Modify the GEOIDs for the township geographies
            thisSumlevel.loc[thisSumlevel["TOWNSHIP"] == True, "GEOIDFQ_CENSUS"] = morpc.SUMLEVEL_LOOKUP["COUNTY-TOWNSHIP-REMAINDER"] + thisSumlevel["GEOIDFQ"].str.removeprefix(sumlevel)
            # Modify the GEOIDs for the place (city, village) geographies
            thisSumlevel.loc[thisSumlevel["TOWNSHIP"] != True, "GEOIDFQ_CENSUS"] = morpc.SUMLEVEL_LOOKUP["PLACE-COUNTY"] + thisSumlevel["GEOIDFQ"].str.removeprefix(sumlevel)
            # Drop the township identifier flag
            thisSumlevel = thisSumlevel.drop(columns=["TOWNSHIP"])
            # Update the values for this SUMLEVEL only in the working dataframe
            df.update(thisSumlevel.set_index("GEOIDFQ"))
        elif(sumlevel == "M23"):
            if(verbose):
                logger.info(f"MORPC SUMLEVEL {sumlevel} is comprised of counties.")
                logger.info(f"Substituting Census SUMLEVEL {morpc.SUMLEVEL_LOOKUP["COUNTY"]} for counties.")
            # Modify the GEOIDs for all geos in this SUMLEVEL (all counties)
            thisSumlevel["GEOIDFQ_CENSUS"] = morpc.SUMLEVEL_LOOKUP["COUNTY"] + thisSumlevel["GEOIDFQ"].str.removeprefix(sumlevel)
            # Update the values for this SUMLEVEL only in the working dataframe
            df.update(thisSumlevel.set_index("GEOIDFQ"))
        else:
            # Included just for completeness. Unsupported sumlevels should have been caught earlier.
            logger.error(f"SUMLEVEL {sumlevel} is not supported. Supported sumlevels are {','.join(supportedSumlevels)}")
            raise RuntimeError

    # Check for null values in the translated GEOIDs. Throw an error if any appear.
    failedTranslationIndex = df.loc[df["GEOIDFQ_CENSUS"].isna()].index
    if(len(failedTranslationIndex) > 0):
        logger.error(f"Translation failed for the following geographies: {list(failedTranslationIndex)}")
        raise RuntimeError

    df = df.filter(items=["GEOIDFQ","GEOIDFQ_CENSUS"], axis="columns")

    # If the user requested to validate the GEOIDs and we were able to load the lookup table, join the VALID
    # flag from the lookup table for each parent GEOID that appears in the lookup table. If some GEOIDs
    # did not appear in the lookup table, warn the user and continue.
    if(not naiveTranslation):            
        df = df.merge(morpcGeosLookup["VALID"], left_on="GEOIDFQ_CENSUS", right_on="GEOIDFQ", how="left")
        missingGeoids = df.loc[df["VALID"] != True, "GEOIDFQ_CENSUS"]
        if(not len(missingGeoids) == 0):
            logger.warn(f"The following GEOIDs were not found in the MORPC geography lookup table and may be invalid: {','.join(list(missingGeoids))}")
        df = df.drop(columns="VALID")
    
    mappingDataFrame = df.copy().rename(columns={"GEOIDFQ":geoidSeries.name})
    
    return mappingDataFrame

def geoidfq_to_columns(geoidfqs: Series | DataFrame) -> DataFrame | GeoDataFrame:
    """Explode a GEOIDFQ Series or DataFrame into sumlevel, variant, geocomp, and geo-field columns."""
    import pandas as pd
    import geopandas as gpd

    if isinstance(geoidfqs, pd.Series):
        logger.debug("Converting GEOIDFQs from Series")
        df = pd.DataFrame(geoidfqs, columns=['geoidfq']).set_index('geoidfq')
    else:
        df = geoidfqs
        if df.index.name in ['GEOIDFQ', 'geoidfq']:
            df = df.reset_index()
        df.columns = [x.lower() for x in df.columns]
        if 'geoidfq' not in df.columns:
            logger.error("GEOIDFQs not in DataFrame.")
            raise ValueError
        df = df.set_index('geoidfq')

    parsed = {g: GeoIDFQ.parse(g) for g in df.index}
    df['sumlevel'] = [parsed[g].sumlevel for g in df.index]
    df['variant'] = [parsed[g].variant for g in df.index]
    df['geocomp'] = [parsed[g].geocomp for g in df.index]

    parts_df = pd.DataFrame([parsed[g].parts for g in df.index], index=df.index)
    overlap = df.columns.intersection(parts_df.columns)
    if len(overlap) > 0:
        logger.warning(f"Columns overlap: using columns from new columns {overlap}")
        df = df.drop(columns=overlap)
    df = df.join(parts_df)

    if 'geometry' in df.columns:
        geo_col = df.pop('geometry')
        df.insert(len(df.columns), 'geometry', geo_col)
        df = gpd.GeoDataFrame(df, crs='epsg:4326')

    return df

def columns_to_geoidfq(df: DataFrame, variant: str = "00", geocomp: str = "00") -> DataFrame:
    """Build a GEOIDFQ string for each row from sumlevel and geo-field columns; stores result in 'geoidfq'."""
    df = df.reset_index()
    if 'index' in df.columns:
        df = df.drop(columns='index')

    if 'sumlevel' not in df.columns:
        logger.error("Dataframe must contain sumlevel column")
        raise ValueError

    sumlevels = list(df['sumlevel'].unique())
    logger.debug(f"Sumlevels ({','.join(str(s) for s in sumlevels)}) in data")

    for sumlevel in sumlevels:
        sl = sumlevel if isinstance(sumlevel, SumLevel) else SumLevel(sumlevel)
        geo_fields = [name for name, _ in _geoidfq_geo_fields(sl.sumlevel)]
        mask = df['sumlevel'] == sumlevel
        df.loc[mask, 'geoidfq'] = df.loc[mask].apply(
            lambda row, _sl=sl, _fields=geo_fields: str(GeoIDFQ.build(
                _sl,
                variant=variant,
                geocomp=geocomp,
                **{f: str(row[f]) for f in _fields},
            )),
            axis=1,
        )

    return df
