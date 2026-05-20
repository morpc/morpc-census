from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("morpc-census")
except PackageNotFoundError:
    __version__ = "0.1.0"

# Census API client and data structuring
from .api import (
    Endpoint,
    Group,
    CensusAPI,
    DimensionTable,
    RaceDimensionTable,
    get_all_avail_endpoints,
    get_dim_variables,
    get_concept_dims_from_long,
    CENSUS_DATA_BASE_URL,
    IMPLEMENTED_ENDPOINTS,
    MISSING_VALUES,
    VARIABLE_TYPES,
)

# Domain lookup tables
from .constants import (
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

# Geography query and translation utilities
from .geos import (
    GeoIDFQ,
    SumLevel,
    Scope,
    SCOPES,
    PSEUDOS,
    geoinfo_from_scope_sumlevel,
    geoinfo_from_params,
    geoids_from_scope,
    pseudos_from_scope_sumlevel,
    geoinfo_for_hierarchical_geos,
    fetch_geos_from_geoids,
    fetch_geos_from_scope_sumlevel,
    morpc_juris_part_to_full,
    census_geoid_to_morpc,
    morpc_geoid_to_census,
    geoidfq_to_columns,
    columns_to_geoidfq,
    describe_scope_sumlevel,
)

# TIGERweb REST API utilities
from .tigerweb import (
    get_tigerweb_layers_map,
    get_layer_url,
    resource_from_scope_sumlevel,
    # resource_from_geometry_sumlevel,  # disabled — needs gdf_from_resource spatial support
)
