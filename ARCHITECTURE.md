# Architecture

The package wraps the US Census Bureau API into a pandas-based data pipeline. Data always flows wide → long → DimensionTable → wide/percent.

## Module layout

- **`api.py`** — Core pipeline: `Endpoint` (survey + year), `Group` (variable group), `CensusAPI` (fetches + melts to long), `DimensionTable` / `RaceDimensionTable` (pivots + percentages). All network calls are lazy-cached via `@functools.cache` or `@cached_property`.
- **`geos.py`** — Geography layer: `Scope` (named query extent), `SumLevel` (summary level code ↔ query name), `GeoIDFQ` (GEOID parser). The `SCOPES` dict is the primary registry of named geographic extents (e.g. `region15`, `franklin`). `SCOPES` is a `_LazyScopes` instance that defers `import morpc` until first access, keeping import side-effect free.
- **`tigerweb.py`** — Fetches GeoDataFrames from the TIGERweb REST API. The `current_endpoints` dict maps human-readable layer names to integer layer IDs — hand-maintained and can drift from the live API.
- **`constants.py`** — Pure data: lookup tables and sort orders for age groups, race, education, income-to-poverty, etc. No I/O.

## Data flow

```
Census API (wide CSV)
  └─ CensusAPI.data         # raw wide DataFrame, one col per variable
  └─ CensusAPI.long         # melted: geoidfq × variable, with estimate/moe columns
       └─ DimensionTable
            ├─ .wide()      # MultiIndex DataFrame: dim rows × (survey, geoidfq, …) cols
            ├─ .percent()   # same shape; estimate=% of total, MOE via Census proportion formula
            ├─ .remap()     # collapse variable labels, aggregate estimates + MOE
            └─ .drop()      # remove a dimension level ('summarize' or 'aggregate')
```

`CensusAPI.save(path)` writes three files: `{name}.long.csv`, `{name}.schema.yaml`, `{name}.resource.yaml` (frictionless descriptors).

`CensusAPI.load(resource_path)` is the inverse: it reads those files back into a `CensusAPI` instance **without** re-fetching survey data. The resource descriptor embeds a `_morpc` block (constructor args: survey, year, scope, sumlevel, group, variables) so the instance can be rebuilt faithfully; `long` is read from the CSV, `request` is restored from the descriptor's `sources`, and `data` is reconstructed from `long` via `_long_to_data()` (the inverse of `melt()`). Resources saved before `_morpc` existed cannot be loaded.

## Key implementation details

- **No network on import.** `Endpoint.vintages`, `Endpoint.groups`, `Group.variables` are all `@cached_property`; `get_all_avail_endpoints()` and `_get_api_key()` are `@functools.cache`.
- **Variable batching.** The Census API allows ~50 fields per request; `_fetch_variables` batches at 48 and joins on `GEO_ID`. When a `group` is set, `_fetch_group` uses the `group(CODE)` endpoint instead (no 50-field limit).
- **MOE propagation.** Aggregation: `sqrt(sum(moe²))`. Percentage: Census Bureau derived proportion formula with fallback to addition form when radicand is negative.
- **Long-format model.** Variable labels use `!!`-delimited hierarchy (e.g. `Total:!!Male:!!Under 5 years`). `DimensionTable._parse_dims()` splits these into subtotal columns (ending with `:`) and leaf columns, then stores each as an ordered categorical.
- **`morpc` dependency.** `morpc>=0.5.4` is a declared dependency that resolves from PyPI. It provides `SUMLEVEL_DESCRIPTIONS`, `morpc.req.get_json_safely`, and `morpc.req.get_text_safely`.

## Census API variable suffixes

| Suffix | Meaning |
|--------|---------|
| `E` | Estimate |
| `M` | Margin of error |
| `PE` | Percent estimate |
| `PM` | Percent MOE |
| `N` | Total |

Census missing-value sentinels (`-222222222`, `-999999999`, etc.) are coerced to `NaN` during `melt()`.
