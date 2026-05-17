# morpc-census

## Introduction

`morpc-census` is a Python package maintained by the MORPC data team for working with US Census Bureau data. It provides tools for connecting to the Census API, retrieving survey data, and structuring results as long-format tables with [frictionless](https://github.com/frictionlessdata/frictionless-py) metadata.

This package depends on [morpc-py](https://github.com/morpc/morpc-py) for shared MORPC utilities.

### Modules

- **morpc_census.api** — Census API client (`Endpoint`, `Group`, `CensusAPI`) and data structuring classes (`DimensionTable`, `RaceDimensionTable`) for reshaping results into long- and wide-format DataFrames with frictionless metadata.
- **morpc_census.geos** — Geography utilities: `Scope` (named query extents), `SumLevel` (summary level codes), `GeoIDFQ` (GEOID parser/builder), and functions for translating between Census GEOIDs and MORPC geography definitions.
- **morpc_census.tigerweb** — Fetches GeoDataFrames from the Census TIGERweb REST API.
- **morpc_census.constants** — Domain lookup tables for age groups, race, education, income-to-poverty, and NTD categories.

## Installation

```bash
pip install morpc-census
```

### Dev Install

To install an editable version for development:

```bash
git clone https://github.com/jinskeep-morpc/morpc-census.git
pip install -e /path/to/morpc-census/
```

Then import as:

```python
import morpc_census
```

## Usage

```python
from morpc_census import Endpoint, Group, CensusAPI, DimensionTable, RaceDimensionTable, SCOPES, SumLevel

ep  = Endpoint('acs/acs5', 2023)
grp = Group(ep, 'B01001')

# Fetch ACS 5-year age/sex data for counties in the 15-county region
api = CensusAPI(ep, SCOPES['region15'], group=grp, sumlevel=SumLevel('county'))

# Long-format DataFrame
print(api.long.head())

# Reshape into wide MultiIndex table and compute percentages
table = DimensionTable(api.long)
wide  = table.wide()
pct   = table.percent()

# Save data + frictionless schema + resource to disk
api.save('./output')
```

## Demos and Documentation

See [demos](https://jinskeep-morpc.github.io/morpc-census/) for examples and documentation.

---

## Roadmap — Code Improvements

- [x] Fix Python 3.10/3.11 syntax bug in `geos.py`
- [x] Cache `_get_api_key()`
- [x] Replace global `_avail_endpoints_cache` with `@functools.cache`
- [x] Avoid double-computing `wide()` inside `percent()`
- [x] Rename `DimensionTable.variable_type` → `value_cols`
- [x] Rename `map` parameter in `find_replace_variable_map` → `label_map`
- [x] Split `CensusAPI.melt()` into focused private helpers
- [x] Pin `numpy` as an explicit dependency
- [x] Add minimum version pins to all dependencies
- [x] Add module docstrings to `geos.py` and `tigerweb.py`
- [x] Validate or auto-fetch `tigerweb.py` `current_endpoints`

---

## Roadmap — Production Readiness & PyPI Release

### Phase 1 — Pre-release cleanup

- [x] Apply all code improvement items above
- [x] Fix the README usage example
- [x] Expand test coverage for offline paths (`wide()`, `percent()`, `remap()`, `drop()`, `melt()`)
- [x] Update `pyproject.toml` classifier from `Development Status :: 1 - Planning` to `4 - Beta`
- [x] Add a `CHANGELOG.md`
- [x] Add a `py.typed` marker file

### Phase 2 — Dependency audit

- [x] Assess whether `morpc` can be published to PyPI or replaced
- [x] Document installation order if `morpc` remains a private dependency
- [x] Pin `morpc` to a minimum version in `pyproject.toml`

### Phase 3 — CI/CD

- [x] Add GitHub Actions CI workflow (`pytest -m "not network"` on push/PR, Python 3.10–3.12)
- [x] Add build verification step (`python -m build` + `twine check`)
- [x] Add publish workflow triggered on release tags

### Phase 4 — Versioning & release

- [x] Switch to dynamic versioning via `setuptools-scm`
- [x] Tag `v0.1.0` and publish first release
- [x] Document versioning policy and breaking-change rules in `CONTRIBUTING.md`

### Phase 5 — Documentation

- [x] Auto-generate API reference docs from docstrings
- [x] Add `CONTRIBUTING.md` with setup instructions and PR process
- [x] Add usage examples to docstrings for commonly called functions

---

This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau.
