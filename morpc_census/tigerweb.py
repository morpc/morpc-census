"""Tools for fetching geographic boundary data from the Census TIGERweb REST API.

TIGERweb base URL: https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/
"""
from __future__ import annotations

import logging
import re
from os import PathLike
from typing import TYPE_CHECKING, Literal

import pandas as pd
import requests

if TYPE_CHECKING:
    from morpc_census.geos import Scope, SumLevel

logger = logging.getLogger(__name__)

current_endpoints: dict[str, int] = {
    'public use microdata areas': 0,
    'zip code tabulation areas': 2,
    'tribal tracts': 4,
    'tribal block groups': 6,
    'tracts': 8,
    'block groups': 10,
    'unified school districts': 14,
    'secondary school districts': 16,
    'elementary school districts': 18,
    'school district administrative areas': 84,
    'estates': 20,
    'county subdivisions': 22,
    'subbarrios': 24,
    'consolidated cities': 26,
    'incorporated places': 28,
    'designated places': 30,
    'alaska native regional corporations': 32,
    'tribal subdivisions': 34,
    'federal american indian reservations': 36,
    'off-reservation trust lands': 38,
    'state american indian reservations': 40,
    'hawaiian home lands': 42,
    'alaska native village statistical areas': 44,
    'oklahoma tribal statistical areas': 46,
    'state designated tribal statistical areas': 48,
    'tribal designated statistical areas': 50,
    'american indian joint-use areas': 52,
    'congressional districts': 54,
    'state legislative districts - upper': 56,
    'state legislative districts - lower': 58,
    'divisions': 60,
    'regions': 62,
    'states': 80,
    'counties': 82,
    'urban areas': 88,
    'combined statistical areas': 97,
    'metropolitan divisions': 95,
    'metropolitan statistical areas': 93,
    'micropolitan statistical areas': 91,
}


def get_tigerweb_layers_map(
    year: int | None = None,
    survey: Literal['ACS', 'DEC', 'current'] = 'ACS',
) -> dict[str, int]:
    """Return a mapping of layer names to MapServer IDs for a TIGERweb service.

    Parameters
    ----------
    year : int, optional
        Vintage year of the TIGERweb service (e.g. ``2024``). Not used when
        ``survey='current'``.
    survey : {'ACS', 'DEC', 'current'}
        Survey type. ``'ACS'`` requires 2012 or later; ``'DEC'`` accepts 2010
        or 2020; ``'current'`` fetches the most-recent geometries service
        (no year required).

    Returns
    -------
    dict[str, int]
        Layer names (lower-cased, year/prefix stripped) mapped to their MapServer layer IDs.

    Examples
    --------
    >>> layers = get_tigerweb_layers_map(2024, survey='ACS')
    >>> layers['tracts']
    8
    >>> layers = get_tigerweb_layers_map(survey='current')
    >>> layers['counties']
    82
    """
    if survey not in ['ACS', 'DEC', 'current']:
        raise ValueError(f"Invalid survey type {survey!r}. Must be 'ACS', 'DEC', or 'current'.")
    if survey == 'DEC' and year not in [2010, 2020]:
        raise ValueError(f"Invalid year {year} for Decennial Census. Must be 2010 or 2020.")
    if survey == 'ACS' and (year is None or year < 2012):
        raise ValueError(f"Invalid year {year} for ACS. Must be 2012 or later.")

    if survey == 'current':
        mapserver_url = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/"
    else:
        survey_slug = 'Census' if survey == 'DEC' else survey
        mapserver_url = f"https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_{survey_slug}{year}/MapServer/"

    logger.info(f"Fetching metadata from {mapserver_url}?f=pjson")
    r = requests.get(f"{mapserver_url}?f=pjson")
    if r.status_code != 200:
        raise RuntimeError(f"Failed to fetch data from {mapserver_url}: {r.status_code}")
    logger.info(f"Successful fetch from {r.url}")

    try:
        layers_json = r.json()
    except Exception:
        r.close()
        raise RuntimeError(f"Failed to parse JSON from {mapserver_url}")
    r.close()

    layers = pd.DataFrame(layers_json['layers'])[['id', 'name']]
    layers = layers.loc[~layers['name'].str.contains('Labels')]
    layer_map: dict[str, int] = layers.set_index('name')['id'].to_dict()

    def _normalize(name: str) -> str:
        name = name.lower().replace('census ', '')
        name = re.sub(r'^(19|20)\d{2} ', '', name)
        name = re.sub(r'^\d{3}(st|nd|rd|th) ', '', name)
        return name

    return {_normalize(k): v for k, v in layer_map.items()}


def get_layer_url(
    layer_name: str | SumLevel,
    year: int | None = None,
    survey: Literal['current', 'ACS', 'DEC'] = 'current',
) -> str:
    """Return the MapServer endpoint URL for a TIGERweb layer.

    Parameters
    ----------
    layer_name : str | SumLevel
        Layer name (e.g. ``'tracts'``) or a ``SumLevel`` instance whose
        ``tigerweb_name`` is used automatically.
    year : int, optional
        Vintage year. Required for ``'ACS'`` and ``'DEC'`` surveys.
    survey : {'current', 'ACS', 'DEC'}
        Survey type. Defaults to ``'current'`` (most recent geometries).

    Returns
    -------
    str
        MapServer endpoint URL for the requested layer.

    Examples
    --------
    >>> get_layer_url('tracts', year=2024, survey='ACS')
    'https://tigerweb.geo.census.gov/.../MapServer/8'
    """
    from morpc_census.geos import SumLevel

    if isinstance(layer_name, SumLevel):
        layer_name = layer_name.tigerweb_name

    if survey not in ['ACS', 'DEC', 'current']:
        raise ValueError(f"Invalid survey type {survey!r}. Must be 'current', 'ACS', or 'DEC'.")
    if survey == 'DEC' and year not in [2010, 2020]:
        raise ValueError(f"Invalid year {year} for Decennial Census. Must be 2010 or 2020.")
    if survey == 'ACS' and year is not None and year < 2012:
        raise ValueError(f"Invalid year {year} for ACS. Must be 2012 or later.")

    layer_name = layer_name.lower()
    baseurl = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"

    if survey == 'current':
        if layer_name not in current_endpoints:
            raise ValueError(f"Layer {layer_name!r} not found in current endpoints. Available: {list(current_endpoints)}")
        url = f"{baseurl}tigerWMS_Current/MapServer/{current_endpoints[layer_name]}"
    else:
        survey_slug = 'Census' if survey == 'DEC' else survey
        layers = get_tigerweb_layers_map(year, survey)
        if layer_name not in layers:
            raise ValueError(f"Layer {layer_name!r} not found for {survey} {year}. Available: {list(layers)}")
        url = f"{baseurl}tigerWMS_{survey_slug}{year}/MapServer/{layers[layer_name]}"

    logger.info(f"Layer URL: {url}")
    return url


def resource_from_scope_sumlevel(
    scope: str | Scope,
    sumlevel: str | SumLevel,
    archive: PathLike | None = None,
    max_record_count: int = 20,
):
    """Build a morpc REST API resource for all geographies at *sumlevel* within *scope*.

    Parameters
    ----------
    scope : str | Scope
        Geographic scope (e.g. ``'franklin'`` or a ``Scope`` instance).
    sumlevel : str | SumLevel
        Summary level name or ``SumLevel`` instance (e.g. ``'tract'``).
    archive : path-like, optional
        If provided, the resource is serialised to YAML at this path.
    max_record_count : int
        Maximum records per API page. Defaults to 20.

    Returns
    -------
    ArcGISResource
        Configured resource ready for fetching.
    """
    from morpc.rest_api import ArcGISResource
    from morpc_census.geos import Scope, SumLevel

    sc = scope if isinstance(scope, Scope) else Scope(scope)
    sl = sumlevel if isinstance(sumlevel, SumLevel) else SumLevel(sumlevel)

    url = get_layer_url(sl.tigerweb_name)
    where = sc.sql
    outfields = ",".join(['GEOID', 'NAME'] + [f.upper() for f in sl.parts])

    tigerweb_resource = ArcGISResource.from_url(
        name=f"censustigerweb-{sc.name}-{sl.hierarchy_string.lower()}",
        url=url,
        where=where,
        outfields=outfields,
        max_record_count=max_record_count,
    )

    if archive is not None:
        tigerweb_resource.to_yaml(archive)

    return tigerweb_resource


# TODO: resource_from_geometry_sumlevel is blocked on ArcGISResource support for
# Issue URL: https://github.com/jinskeep-morpc/morpc-census/issues/50
# spatial (geometry envelope) queries. ArcGISResource.to_geodataframe() paginates using
# totalRecords which does not accept spatial params, so fetching from this resource
# silently returns wrong results. Re-enable once ArcGISResource.from_url() supports
# geometry-based where clauses or a separate spatial fetch path is added.
#
# def resource_from_geometry_sumlevel(
#     geo,
#     scopename: str,
#     sumlevel: str | SumLevel,
#     archive: PathLike | None = None,
#     max_record_count: int = 20,
# ):
#     """Build an ArcGISResource for all geographies at *sumlevel* intersecting *geo*.
#
#     geo : GeoDataFrame | GeoSeries -- bounding box used as spatial filter
#     scopename : str -- label for the resource name (e.g. 'franklin')
#     sumlevel : str | SumLevel -- e.g. 'tract'
#     """
#     from morpc.rest_api import ArcGISResource, ArcGISControl
#     from morpc_census.geos import SumLevel
#     sl = sumlevel if isinstance(sumlevel, SumLevel) else SumLevel(sumlevel)
#     url = get_layer_url(sl.tigerweb_name)
#     outfields = ",".join(['GEOID', 'NAME'] + [f.upper() for f in sl.parts])
#     # NOTE: ArcGISResource.from_url() does not yet support geometry envelope queries.
#     # When re-enabling, pass geometry params via **kwargs and update to_geodataframe()
#     # to use a spatial record count rather than the service totalRecordCount.
#     tigerweb_resource = ArcGISResource.from_url(
#         name=f"censustigerweb-{scopename}-{sl.hierarchy_string.lower()}",
#         url=url,
#         outfields=outfields,
#         max_record_count=max_record_count,
#         geometry=",".join(str(x) for x in geo.total_bounds),
#         geometryType='esriGeometryEnvelope',
#         inSR=str(geo.crs.to_epsg()),
#         spatialRel='esriSpatialRelContains',
#     )
#     if archive is not None:
#         tigerweb_resource.to_yaml(archive)
#     return tigerweb_resource
