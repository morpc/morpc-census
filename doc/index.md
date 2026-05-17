# morpc-census

## Introduction

`morpc-census` is a Python package maintained by the MORPC data team for working with US Census Bureau data. It provides tools for connecting to the Census API, retrieving survey data, and structuring results as long-format tables with [frictionless](https://github.com/frictionlessdata/frictionless-py) metadata. It also contains utilities for working with Census geographic data including building frictionless resources for TIGERweb REST API endpoints.  

This package depends on [morpc-py](https://github.com/morpc/morpc-py) for shared MORPC utilities.

### Modules

- **morpc_census.api** — Connect to the Census API at `https://api.census.gov/data/`, retrieve survey data by group and geography, and structure results as long-format DataFrames with frictionless schema and resource files.
- **morpc_census.geos** — Geography utilities for building Census API queries, translating between Census GEOIDs and MORPC geography definitions, and fetching geographic metadata.
- **morpc_census.tigerweb** — Tools for interacting with the Census TIGERweb REST API to fetch geographic boundary data.

## Notebooks

- [Geography demo](/morpc-geos-demo)
- [Census API demo](/morpc-census-demo)
- [Poverty/race demo](/morpc-poverty-race-demo)

## Reference

- [API Reference](/api-reference)
