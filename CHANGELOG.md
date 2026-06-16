# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [0.5.0] — 2026-06-16

### Added

- **`CensusAPI.load(resource_path)`** — reconstructs a `CensusAPI` instance from the output of `CensusAPI.save()` (the long-format CSV + frictionless resource descriptor) **without re-fetching survey data**. The `.data` property is derived from the saved long table via the new `_long_to_data()` helper, which reverses `melt()`.
- `CensusAPI.save()` now embeds a `_morpc` block (survey, year, scope, sumlevel, group, variables) in the resource descriptor so `load()` can faithfully rebuild the instance. Resources saved by earlier versions lack this block and raise a clear error on load.

## [0.2.0] — 2026-05-20

### Added

- **`Group.concept_dims`** (cached property) — maps dim-column names to human-readable labels. Looks up curated overrides in `dim_names.json` and falls back to auto-inference from the group's concept string and variable label structure. No Census data API call required; uses only the groups metadata endpoint.
- **`get_dim_variables(group)`** — returns an ordered dict of unique values for each named dimension in a group (uses `Group.concept_dims` for names).
- **`get_concept_dims_from_long(long_df)`** — same dim-name lookup/inference operating on an existing long DataFrame, so downstream apps can resolve names without constructing a `Group` object.
- **`describe_scope_sumlevel(scope, sumlevel)`** — returns a natural-language description of the geography defined by a scope and summary level (e.g. `"tracts in 15-County Region"`, `"Franklin County"`).
- `dim_names.json` — curated dim-name overrides for complex group codes whose label structure defeats the auto-naming heuristic (e.g. B26101, B28006).

## [0.1.0] — 2026-05-16

Initial beta release.

### Added

- **`CensusAPI`** — fetches ACS/Decennial data by group or variable list, melts wide API response to a long-format DataFrame with `estimate`/`moe` columns, `concept`, `universe`, `survey`, `reference_period`, `geoidfq`, and `name`.
- **`DimensionTable`** — reshapes a `CensusAPI.long` DataFrame into a MultiIndex wide table (`wide()`), computes column percentages with Census Bureau derived proportion MOEs (`percent()`), remaps variable labels (`remap()`), and drops dimension levels by name, integer index, or list (`drop()`). Dimension columns are ordered categoricals preserving Census API hierarchy order.
- **`RaceDimensionTable`** — subclass of `DimensionTable` for analysing racial-iteration groups (e.g. B17020A–I). Normalises variable codes, concept, and universe across races, adds an ordered `race` column level, and computes within-race percentages.
- **`Endpoint` / `Group`** — lazy-cached wrappers around the Census API metadata endpoints (vintages, groups, variables).
- **`GeoIDFQ`** — parses, inspects, and constructs Census fully-qualified geographic IDs (e.g. `1400000US39049010100`).
- **`Scope` / `SumLevel` / `SCOPES`** — named geographic extents and summary-level utilities for building Census API `for`/`in` query parameters.
- **`fetch_geos_from_scope_sumlevel`** — fetches TIGERweb boundary GeoDataFrames for a named scope and summary level.
- **`get_tigerweb_layers_map`** — returns a live layer-name → MapServer-ID mapping for ACS, Decennial, or Current TIGERweb services.
- **`morpc_census.constants`** — lookup tables for age groups, race, education, income-to-poverty, and NTD categories.
- Frictionless schema and resource file generation via `CensusAPI.save()`.
- `@pytest.mark.network` test suite for live API calls; full offline suite runnable without a Census API key.

### Changed

- `HIGHLEVEL_DESC_FROM_ID` renamed to `HIGHLEVEL_DESC_TO_ID` (the dict maps descriptions *to* IDs, not from them).
- `DimensionTable.variable_type` renamed to `value_cols`.
- `find_replace_variable_map` parameter `map` renamed to `label_map` (avoids shadowing the Python builtin).
- `CensusAPI.melt()` refactored into focused private helpers (`_melt_wide_to_long`, `_attach_dataset_metadata`, `_pivot_and_coerce`).
- `wide()` column MultiIndex now uses canonical level order (`concept > universe > survey > geoidfq > name > [race] > reference_period > value_type`) and names the value-type level `'value_type'` instead of `None`.
- `percent()` MOE now uses the Census Bureau derived proportion formula.
- `_get_api_key()` and `get_all_avail_endpoints()` are `@functools.cache`-decorated (no repeated filesystem/network calls).
- `get_tigerweb_layers_map` accepts `survey='current'` (no year required).

### Fixed

- Python 3.10/3.11 syntax error in `geos.py` (`Scope.sql` f-string nesting).
- `NAME` column missing from variable-batch fetch results.
- `set_levels` race-ordering bug that swapped column labels without moving data.
