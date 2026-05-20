# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Models
- Default to **Sonnet** for all code work.
- Use **Opus** for coordination, architecture, high-level design, and conceptual planning.
- Use **Haiku** for simple lookups, reading files, and lightweight tasks.

## Teams
- Use subagents for all non-trivial code work. Spawn agents with the appropriate model for the task.

## Git Workflow

### Before starting any work
1. Check the current branch. **Never make code changes directly on `main`.**
2. Ask the user which branch to work on, or propose a branch name and ask for confirmation before creating it.
3. Create the branch and switch to it before making any changes.

### While working
- Commit in logical units with clear, descriptive commit messages.
- Write tests for new functionality.
- Update documentation when behavior changes.

### When work is complete
1. Ask the user if they want to open a pull request.
2. If yes, push the branch and create a PR with a summary of what changed and why.

### Rules
- Never commit directly to `main` or `master`.
- Never force-push without explicit user confirmation.
- Always confirm before destructive git operations (reset --hard, branch deletion, etc.).

## Commands

```bash
# Dev install (editable)
pip install -e /path/to/morpc-census/

# Run tests (offline only — default)
pytest

# Run network tests (makes live Census API calls)
pytest -m network

# Run a single test
pytest tests/test_api.py::TestCensusapiName::test_no_sumlevel_no_variables -v
```

Tests are split by the `network` marker. The default `pytest` run (`-m 'not network'`) is safe to run without a Census API key. Network tests require `CENSUS_API_KEY` set in the environment or a `.env` file found via `find_dotenv(usecwd=True)`.

## Architecture

The package wraps the US Census Bureau API into a pandas-based data pipeline. Data always flows wide → long → DimensionTable → wide/percent.

### Module layout

- **`api.py`** — Core pipeline: `Endpoint` (survey + year), `Group` (variable group), `CensusAPI` (fetches + melts to long), `DimensionTable` / `RaceDimensionTable` (pivots + percentages). All network calls are lazy-cached via `@functools.cache` or `@cached_property`.
- **`geos.py`** — Geography layer: `Scope` (named query extent), `SumLevel` (summary level code ↔ query name), `GeoIDFQ` (GEOID parser). The `SCOPES` dict is the primary registry of named geographic extents (e.g. `region15`, `franklin`).
- **`tigerweb.py`** — Fetches GeoDataFrames from the TIGERweb REST API. The `current_endpoints` dict maps human-readable layer names to integer layer IDs — these are hand-maintained and can drift from the live API.
- **`constants.py`** — Pure data: lookup tables and sort orders for age groups, race, education, income-to-poverty, etc. No I/O.

### Data flow

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

### Key implementation details

- **No network on import.** `Endpoint.vintages`, `Endpoint.groups`, `Group.variables` are all `@cached_property`; `get_all_avail_endpoints()` and `_get_api_key()` are `@functools.cache`.
- **Variable batching.** The Census API allows ~50 fields per request; `_fetch_variables` batches at 48 and joins on `GEO_ID`.
- **MOE propagation.** Aggregation: `sqrt(sum(moe²))`. Percentage: Census Bureau derived proportion formula with fallback to addition form when radicand is negative.
- **Long-format model.** Variable labels use `!!`-delimited hierarchy (e.g. `Total:!!Male:!!Under 5 years`). `DimensionTable._parse_dims()` splits these into subtotal columns (ending with `:`) and leaf columns, then stores each as an ordered categorical.
- **MORPC dependency.** `morpc` (from `morpc-py`) is a private internal package not on PyPI. It must be installed manually before `morpc-census` can run. It provides `SUMLEVEL_DESCRIPTIONS`, `morpc.req.get_json_safely`, and `morpc.req.get_text_safely`.

### Census API variable suffixes

| Suffix | Meaning |
|--------|---------|
| `E` | Estimate |
| `M` | Margin of error |
| `PE` | Percent estimate |
| `PM` | Percent MOE |
| `N` | Total |

Census missing-value sentinels (`-222222222`, `-999999999`, etc.) are coerced to `NaN` during `melt()`.
