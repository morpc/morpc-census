## Namespace dimension files by survey family (acs_/dec_) (issue #109)

Renamed the ACS dimension data files so decennial equivalents can live
alongside them:

- morpc_census/dim_names.json   → acs_dim_names.json   (build-time, {dim_###: name})
- morpc_census/dims.json        → acs_dims.json        (runtime, {dim_###: {name, variables}})
- morpc_census/group_dims.json  → acs_group_dims.json  (runtime, {group: [dim_ids]})

Made the runtime loaders survey-aware via `_dim_family(survey)` (returns
"dec" for dec/* surveys, else "acs"); `_load_dims_json`, `_load_group_dims_json`,
and `_load_dim_names_json` now take a `family` arg and read `{family}_*.json`.
`_match_col_names` and `_parse_dims` thread the family through from the long
frame's `survey` column, so dec tables will use dec_* files once created and
ACS behavior is unchanged (all 197 api tests pass). Missing dec_* files fall
back to empty dicts (→ dim_N), so nothing breaks before they exist.

Updated ACS build scripts (build_dims, dim_namer, name_dimension_sets,
dim_similarity, dim_network) to read/write the acs_* filenames.

## fetch_dec_variable_groups.py — decennial variable-group catalog (issue #109)

Added `scripts/fetch_dec_variable_groups.py`, which walks the Census API
hierarchy (survey → vintages → groups → variables) for each implemented
decennial survey and writes `scripts/dec_variable_groups.json` (883 entries),
mirroring the structure of `acs_variable_groups.json` for the dimension-naming
pipeline.

- Surveys/vintages: dec/pl (2020, 2010, 2000), dec/dhc (2020), dec/sf1 (2010, 2000).
- Uses urllib directly — does NOT use `morpc_census.Endpoint` and sends no API key.
- `_filter_variables()` auto-detects two naming conventions:
  - Modern (`P1_001N`): keep codes ending `N`, drop annotations ending `NA`.
  - Legacy (`P001001`, race-iteration `P012A005`): regex `^[A-Z]+\d+[A-Z]?\d+$`,
    which excludes error vars (`P012001ERR`). Fixing the legacy regex to allow an
    embedded race letter eliminated 426 spuriously-empty dec/sf1 groups.
- `_normalize_universe()` maps blank universes via group-code prefix
  (P/PCT/PCO→Total population, H/HCT→Housing units, GQ→Group quarters population).
- Compound key `{slug}/{vintage}/{group}` distinguishes group codes that differ
  across vintages (dec/pl uses PL001-PL004 in 2000 but P1-P4 in 2010/2020).

## 2026-06-02 — Normalize universe strings for dec surveys (#107)

Added _UNIVERSE_CODE_MAP (raw API codes → readable strings) and _UNIVERSE_PREFIX_MAP (group code prefix fallback). Group.universe and CensusAPI.universe both normalize. CensusAPI.universe no longer cross-vintage-looks up for non-ACS surveys.

## 2026-06-02 — Drop all-null columns from DimensionTable flat export (#105)

Added data_flat.dropna(axis=1, how='all') in _to_wide_flat(). create_schema() calls _to_wide_flat() so the schema automatically reflects only populated columns.

## 2026-06-02 — Remove missingValues from DimensionTable.create_schema() (#103)

The table schema is for a processed wide output that doesn't use Census error codes. Removed top-level missingValues from the descriptor.

## 2026-06-01 — Enhance DimensionTable.create_resource() with rich metadata (#101)

Auto-extracts geography names and vintages from self.long; builds a richer auto-description including geography summary and years; adds _geographies and _vintages keys to the resource descriptor. Adds description param to save(). Adds info logging to save() matching CensusAPI style.

## 2026-06-01 — DimensionTable export methods (issue #99, feat/dimension-table-export)

Added `_to_wide_flat`, `create_schema`, `create_resource`, and `save` to `DimensionTable`. The flat export collapses the 7-level column MultiIndex from `wide()`/`percent()` into human-readable `"{geo name} - {year} ({value type})"` data column names, with dim columns as plain string columns. `concept`, `universe`, and `survey` move from column headers to resource metadata (`_concept`, `_universe`, `_survey`). Output: `{name}.csv` + `{name}.schema.yaml` + `{name}.resource.yaml`, validated with frictionless. Pattern follows `CensusAPI.save()` exactly.

## 2026-06-01 — Auto-detect drop method in DimensionTable.drop (issue #95)

Removed the `method` parameter from `DimensionTable.drop()`. The choice between filtering to pre-existing subtotal rows vs. summing leaf rows is now auto-detected via `_has_partial_subtotals()` — True when rows exist where the target dim is `''` but a sibling dim (with 2+ distinct global values) is non-empty. Also added a guard so dropping the last remaining dim column uses filter instead of crashing `_aggregate_dim` with empty `other_dims`. Explorer updated to remove `_choose_drop_method` helper and simplify all `dt.drop()` call sites.

# morpc-census dev notes

## 2026-05-18 — Fix numeric Census sentinel values in DimensionTable.wide() (branch fix/numeric-sentinel-values, closes #84)

`MISSING_VALUES` contains sentinel strings (e.g. `"-555555555"`) but `_pivot_and_coerce` coerces API values to float, so the string-keyed `replace()` silently missed float forms like `-555555555.0`. This caused `DimensionTable.percent()` to compute astronomical percent-MOE values (~20,000%) when the total row's MOE was a controlled-total sentinel. Added `_MISSING_VALUES_NUMERIC` (integer equivalents) and included them in `wide()`'s replace dict. After fix, sentinel `moe_T` becomes NaN → falls back to 0 in the formula → `m_x / T * 100` (Census Bureau recommended approach). 5 new tests in `TestMissingValueSentinels`.

---

## 2026-05-16 — Rewrite 01-morpc-geos-demo.ipynb around user workflow (branch doc/geos-demo-rewrite, closes #82)

Rewrote the geos demo notebook from 45 cells to 25, restructured around four sections: Scopes, Scales, Fetching geometries, GEOIDFQs.

**Removed:**
- `Scope('region15')` direct construction, `.params`, `.sql` — internal API not intended for callers
- `geoids_from_scope` subsection — lower-level detail that distracts from the main workflow
- Section 5 (TIGERweb resources) — `resource_from_scope_sumlevel` and the disabled `resource_from_geometry_sumlevel` TODO cell; `fetch_geos_from_scope_sumlevel` is the right primary entry point
- `Scope`, `geoids_from_scope`, `resource_from_scope_sumlevel` from imports

**Added/kept:**
- `geoinfo_from_scope_sumlevel` moved under Section 3 as a "Geographic IDs without geometry" subsection
- ASCII diagram for GEOIDFQ structure preserved from original
- Network-required note at the top of Section 3 (single note, not per-cell)
- GEOIDFQ parse from fetch result (last cell) — shows end-to-end connection


## 2026-05-16 — Fix: morpc is on PyPI; remove manual install steps from README, CI, and CONTRIBUTING

The Phase 2 assessment was wrong — `morpc` (the PyPI package name for `morpc-py`) is available on PyPI at version 0.4.3. Removed the `git clone morpc-py` workaround from README, both GitHub Actions workflows, and CONTRIBUTING.md. The `morpc>=0.4.3` pin in `pyproject.toml` is correct and resolves normally via PyPI.


## 2026-05-16 — Phase 5 documentation: API reference page and docstring examples (branch chore/phase5-docs, closes #80)

- **Docstring examples** added to four key callables: `CensusAPI` (class docstring), `DimensionTable.wide()`, `geoinfo_from_scope_sumlevel()`, and `fetch_geos_from_scope_sumlevel()`. All marked `# doctest: +SKIP` since they require network access. `fetch_geos_from_scope_sumlevel` also received a full Parameters/Returns section (was a one-liner).
- **`doc/api-reference.md`** — MyST Markdown API reference page documenting all key classes and functions with short code examples. Linked from `doc/index.md` under a new "Reference" section.
- **`CONTRIBUTING.md`** was already completed in Phase 4; checked off in README.
- Note: full auto-generated docs (sphinx autodoc / mkdocstrings) would require adding a second doc toolchain alongside the existing MyST setup. The manual API reference page is a pragmatic alternative that works within the existing infrastructure.


## 2026-05-16 — Phase 4 versioning: setuptools-scm, v0.1.0 tag, CONTRIBUTING.md (branch chore/phase4-versioning, closes #78)

- **Dynamic versioning:** Replaced hardcoded `__version__ = "0.1.0"` in `__init__.py` with `importlib.metadata.version("morpc-census")` (fallback `"0.1.0"` for non-installed editable environments). Removed `[tool.setuptools.dynamic]` `version = {attr = ...}` from `pyproject.toml`; added `[tool.setuptools_scm]` with `fallback_version = "0.1.0"`. `setuptools-scm` was already in `build-system.requires`.
- **`CONTRIBUTING.md`** created with: dev setup steps, test commands, branching/PR process, SemVer table, definition of breaking changes, and release tagging instructions.
- **`v0.1.0` tag** pushed after the PR merges (see release on GitHub).


## 2026-05-16 — Phase 3 CI/CD: GitHub Actions for test, build, and publish (branch chore/phase3-cicd, closes #76)

Added two GitHub Actions workflows:

- **`.github/workflows/ci.yml`** — runs on every push and PR to `main`. Two jobs: `test` (matrix: Python 3.10, 3.11, 3.12; runs `pytest -m "not network"`) and `build` (`python -m build` + `twine check dist/*`). Both jobs clone and install `morpc-py` from GitHub before installing `morpc-census`.
- **`.github/workflows/publish.yml`** — triggers on GitHub release publication. Builds the package, runs `twine check`, then publishes to PyPI using `pypa/gh-action-pypi-publish` with OIDC trusted publisher (no API token stored in secrets). Requires a `pypi` GitHub environment to be configured.

Note: CI will fail until the `morpc-py` repo is accessible to GitHub Actions runners (currently private). May need a deploy key or PAT secret (`GH_PAT`) added to the repo secrets and the clone step updated to use it.


## 2026-05-16 — Phase 2 dependency audit: pin morpc, document install order (branch chore/phase2-dependency-audit, closes #74)

**Assessment:** `morpc` (from `morpc-py`, currently v0.4.3) provides HTTP utilities (`morpc.req.get_json_safely/get_text_safely`), REST API helpers (`morpc.rest_api.resource/gdf_from_resource`), and MORPC-internal geography constants (`CONST_*`, `SUMLEVEL_*`, `CONST_REGIONS`). The geography constants are tightly coupled to internal MORPC data — publishing `morpc` to PyPI without significant restructuring is not practical. Recommendation: keep as private dependency.

**Actions taken:**
- Pinned `morpc>=0.4.3` in `pyproject.toml` (was unpinned).
- Updated README Installation section to clearly state that `morpc-py` must be installed manually before `morpc-census`, with explicit two-step install commands for both the regular and dev install cases.


## 2026-05-16 — Phase 1 production readiness: CHANGELOG, py.typed, beta classifier (branch chore/beta-classifier, closes #72)

Three Phase 1 items completed on one branch per the new per-phase branching rule:

- **`pyproject.toml` classifier** → `"Development Status :: 4 - Beta"` + `"Typing :: Typed"` classifier added.
- **`py.typed`** marker file created at `morpc_census/py.typed`; declared in `[tool.setuptools.package-data]` alongside `*.json`.
- **`CHANGELOG.md`** created with a `0.1.0` entry covering all features, renames, and fixes shipped to date.


## 2026-05-16 — Update pyproject.toml classifier to 4 - Beta (branch chore/beta-classifier, closes #72)

Changed `"Development Status :: 1 - Planning"` to `"Development Status :: 4 - Beta"` in `pyproject.toml`. The package has a stable public API, comprehensive offline test coverage, and multiple shipped features — Planning is no longer accurate.


## 2026-05-16 — Validate tigerweb current_endpoints via network test (branch fix/tigerweb-validate-endpoints, closes #70)

Extended `get_tigerweb_layers_map` to accept `survey='current'` (no year required), fetching from `tigerWMS_Current/MapServer/?f=pjson` and applying the same name normalization as the ACS/DEC paths. Added a `@pytest.mark.network` test `TestGetTigerwebLayersMap::test_current_endpoints_match_live_api` that compares every entry in the hardcoded `current_endpoints` dict against the live API response and reports any names missing or IDs that have drifted. Also fixed a stale `HIGHLEVEL_DESC_FROM_ID` import in `test_constants.py` (the constant was renamed to `HIGHLEVEL_DESC_TO_ID` in a prior session but the test was never updated). 237 tests passing.


## 2026-05-16 — Simplify README roadmap to checklist; update modules and current state (main, closes #69)

Stripped all detail text from both roadmap sections, leaving plain checkbox lines. Details for all items remain here in dev_notes. Updated modules section: `RaceDimensionTable` and `GeoIDFQ` added to descriptions, `morpc_census.constants` added as a fourth module entry. Checked off "Expand test coverage for offline paths" (completed across recent sessions). Added `RaceDimensionTable` to the usage example import.


## 2026-05-13 — RaceDimensionTable race column level is ordered categorical in race_map order (branch fix/highlevel-rename-fetch-name)

`RaceDimensionTable.__init__` now converts `self.long['race']` to `pd.Categorical` with the values of `effective_map` (insertion order) as the ordered categories. `DimensionTable.wide()` re-applies this categorical metadata to the column MultiIndex after `from_tuples()` strips it, using `set_levels` with the **current** level values (preserving code→label mapping) and the desired order only in the `categories=` argument.

A data-integrity bug was introduced and fixed in the same session: the first `set_levels` attempt passed the desired-order list as both VALUES and categories. `set_levels` changes level values without touching integer codes, so swapping the value order caused codes to point to the wrong labels (White Alone column held Black data). Fixed by passing `current_vals` (existing order) as values and `present_cats` (desired order) only as `categories`.

4 new tests: ordered dtype, column order matches `RACE_TABLE_MAP`, custom map order respected, and a regression guard that verifies data values match their column labels with distinct estimates per race. 123 tests passing.

## 2026-05-13 — DimensionTable.wide() column MultiIndex: canonical level order and 'value_type' level name (branch fix/highlevel-rename-fetch-name)

Two related changes to the `wide()` output column MultiIndex:

**Canonical level order** — Previously `wide()` reversed all levels (`reorder_levels(names[::-1])`), producing an arbitrary order that varied with the `col_dims` list. Now applies `_WIDE_COL_LEVEL_ORDER = ['concept', 'universe', 'survey', 'geoidfq', 'name', 'race', 'reference_period']` as a canonical ordering, with the value-type level always last. For `RaceDimensionTable`, `race` is inserted between `name` and `reference_period`. Unrecognized levels fall after `reference_period` and before `value_type`.

**`value_type` level name** — The pivot `values=list` call left the estimate/moe level unnamed (`None`). Renamed to `'value_type'` in `col_level_names` and updated `percent()`'s level lookup accordingly. Also reverted an unintended linter change (`values='type'` → `values='value'`) in `melt()`'s pivot that broke four tests.

## 2026-05-13 — Fix DimensionTable.percent() MOE calculation (branch fix/highlevel-rename-fetch-name)

`percent()` was dividing MOE by the total MOE, which is wrong. MOEs for proportions must use the Census Bureau derived proportion formula:

- `MOE(p) = (1/T) * sqrt(MOE_x² − p² * MOE_T²) * 100` when radicand ≥ 0
- `MOE(p) = (1/T) * sqrt(MOE_x² + p² * MOE_T²) * 100` when radicand < 0 (sampling variability)

Where `p = x/T` (the estimated proportion), `T` = total estimate, `MOE_x` = subgroup MOE, `MOE_T` = total MOE.

Implementation: identify estimate vs MOE columns by the last level of the column MultiIndex (name `None`, set by `wide()`'s `reorder_levels`). For each MOE column, find the corresponding estimate column by swapping the last level to `'estimate'`, then apply the vectorized formula using `clip(lower=0)` / `np.where`.

Also fixed an existing vacuous test `test_percent_within_each_race` that used `c[0] == 'estimate'` to select columns — the first level is race, not value type; fixed to `c[-1] == 'estimate'`. Added two new tests: one checking the standard formula against a hand-computed expected value (`sqrt(7)/100 * 100 ≈ 2.65%`), and one checking the addition fallback form when the radicand is negative.

119 tests passing.

## 2026-05-13 — DimensionTable.drop(): accept int index and list of str/int (branch fix/highlevel-rename-fetch-name)

`drop(dim)` now resolves integers as 0-based (or negative) column positions in `self.dims.columns`, and accepts a list of strings/integers to drop multiple dimensions in one call. When a list is passed, all items are resolved to column names relative to `self` upfront so that integer indices always refer to original positions rather than the shrinking set after each prior drop.

7 new tests: integer index, negative integer, out-of-range error, list of strings, list of integers, mixed list, and sequential list reduction.

Also fixed `TestRaceDimensionTable` fixture universe strings ("...alone for whom..." → "...alone population for whom...") to match the revised `_preprocess` regex `r'^.+?\s+population\s+'`.

117 tests passing.

## 2026-05-13 — Populate concept and universe per-row in variables-only mode (branch fix/highlevel-rename-fetch-name)

`CensusAPI.melt()` Step 5 previously set `concept=''` and `universe='Not defined — no group specified'` when `self.group is None`. Now, in variables-only mode:

- **concept** — built from `self.vars` metadata. `self.vars` keys carry the type suffix (`B01001_001E`); after Step 4 strips the suffix, we pre-build a `{base_code: concept}` dict and map it onto `long['variable']`. Result is `.capitalize()`'d.
- **universe** — group-level attribute not stored per-variable. We extract the group code from each base variable code via regex (`B01001_001` → `B01001`) and look up `self.endpoint.groups[gc]['universe']`. `endpoint.groups` is a `cached_property`, so the fetch happens at most once per session.

Four new tests in `TestCensusAPIGroupOptional`:
- `test_melt_concept_populated_from_vars_in_variables_only_mode`
- `test_melt_universe_populated_from_endpoint_groups_in_variables_only_mode`
- `test_melt_concept_empty_string_when_var_has_no_concept`
- `test_melt_universe_empty_string_when_group_not_in_endpoint_groups`

110 tests passing.

## 2026-05-13 — Rename HIGHLEVEL_DESC_FROM_ID → HIGHLEVEL_DESC_TO_ID; fix NAME missing from variable-batch fetch (branch fix/highlevel-rename-fetch-name, closes #64)

**Constant rename:** `HIGHLEVEL_DESC_FROM_ID` → `HIGHLEVEL_DESC_TO_ID` in `constants.py`. The old name implied the dict maps *from* an ID, but it maps *to* an ID (`{description: code}`). Updated re-export in `api.py` and public export in `__init__.py`.

**Batch fetch bug fix:** `CensusAPI._fetch_variables` was building batch requests as `GEO_ID + variables`, omitting `NAME`. This caused the `name` column to be absent from results in variables-only mode. Fix: include `NAME` in every batch request and index each result frame on `['GEO_ID', 'NAME']` (matching the group-fetch path). `BATCH_SIZE` adjusted 49 → 48 to keep total fields within the Census API's 50-field limit (GEO_ID + NAME + 48 variables = 50).

`TestFetchVariablesBatching` updated: `_response` fixture now includes NAME; batch-count tests updated to reflect BATCH_SIZE=48; `test_geoid_included_in_every_batch_request` → `test_geoid_and_name_included_in_every_batch_request`. 106 tests passing.

## 2026-05-12 — DimensionTable: dimension columns as ordered categoricals (branch fix/dimension-table-categoricals, closes #58)

`DimensionTable._parse_dims()` now converts each column of the returned `dims` DataFrame to `pandas.CategoricalDtype(ordered=True)` after building it. Categories are set in first-appearance order across the Census variable list, which is the hierarchical order the Census API uses (e.g., income-to-poverty thresholds low-to-high, age bands youngest-to-oldest). This lets callers sort and group dimensions without supplying an explicit sort key.

Also fixed two `FutureWarning`s about `observed=False` in `groupby` calls in `remap()` and `_aggregate_dim()` — both now pass `observed=True`, which is the correct behavior when operating on categoricals (we only want to aggregate the combinations that actually exist in the data).

Three new tests added to `TestDimensionTableParseDims`:
- `test_dims_columns_are_ordered_categoricals` — all columns have `.cat.ordered == True`
- `test_dims_categorical_order_matches_variable_order` — Male: before Female: in the standard fixture
- `test_dims_categorical_order_preserved_across_cross_vintage` — order preserved after vintage normalization

95 tests passing.
## 2026-05-12 — Add RaceDimensionTable class (branch feat/race-dimension-table, closes #59)

New `RaceDimensionTable(DimensionTable)` subclass in `morpc_census/api.py`. Accepts a concatenated `CensusAPI.long` DataFrame from multiple racial iteration group fetches (e.g. B17020A–I) and preprocesses it before delegating to `DimensionTable`:

- Extracts race letter from variable code (`B17020A_001` → `A`) and maps to a label via `RACE_TABLE_MAP` (or a caller-supplied `race_map`). Rows with unmapped codes are silently dropped.
- Normalizes variable code by stripping the race letter (`B17020A_001` → `B17020_001`) so all groups share the same variable namespace.
- Normalizes `concept` (strips trailing parenthetical race qualifier) and `universe` (replaces leading `"<Race> alone"` prefix with `"Population"`) so both fields are identical across races and do not inflate the column MultiIndex.
- Removes `'race'` from `variable_type` after `super().__init__`, so `race` becomes a column-level dimension in `wide()` / `percent()`. The inherited `percent()` naturally computes within-race percentages with no override.

Exported from `morpc_census/__init__.py`. 11 new tests in `TestRaceDimensionTable`. 103 tests passing.
## 2026-05-12 — Revise poverty/race demo notebook: markdown cells, MOE, suppression (branch doc/poverty-race-demo)

Rewrote `doc/03-morpc-poverty-race-demo.ipynb` in response to three requests:

1. **Markdown-first** — all explanatory `#` comments removed from code cells and replaced with preceding markdown cells; section headers, formula explanations, and caveats now in prose
2. **MOE throughout** — `wide_moe` pivot added alongside `wide_est`; CV computed per county × race using Census Bureau's derived proportion formula (ratio form, falling back to sum-in-quadrature when under-root is negative); same pattern applied to the non-white time-series derivation (MOE of differences in quadrature, then ratio formula for the derived rate)
3. **Suppression** — `SUPPRESS_CV = 0.40` constant defined at import; `rates.where(cvs <= SUPPRESS_CV)` masks unreliable cells; styled table uses `na_rep='—'`; time-series chart applies same mask, producing visible gaps for suppressed county/year combinations

## 2026-05-12 — Add poverty/race use-case demo notebook (branch doc/poverty-race-demo)

New notebook `doc/03-morpc-poverty-race-demo.ipynb` covering a full analysis scenario:

1. **Discovery** — `ep.groups` filtered for "poverty"; inspect B17001 variable structure and racial iteration convention (B17001A–I mapped by `RACE_TABLE_MAP`)
2. **Fetch** — B17001 full group for total poverty context; all 9 racial iteration tables in a single variables-only `CensusAPI` call (18 vars: `_001E` + `_002E` per table)
3. **Snapshot** — pivot to poverty rates per race × county; styled heatmap table + horizontal bar chart of region-wide totals
4. **Time series** — loop 2019–2023 fetching 4 variables per year (`B17001_001E`, `_002E`, `B17001H_001E`, `_002E`); compute non-white poverty rate = (total − White-not-Hispanic below poverty) / (total − White-not-Hispanic total); line chart per county
5. **Map** — compute 2019→2023 change in percentage points; join to `fetch_geos_from_scope_sumlevel('region15')` on `GEOIDFQ`; diverging choropleth centered at zero with county labels

Closes #57.

## 2026-05-12 — Fix variable_label in variables-only mode (branch refactor/api-class-integration)

`CensusAPI.vars` previously returned `{v: {} for v in self.variables}` when no group was set, causing `melt` to fall back to the raw variable code (e.g. `B01001_001E`) as the `variable_label` instead of the human-readable label (`Total:!!Male:!!Under 5 years`).

Fix: infer the group code from each variable name via regex (`B01001_001E` → `B01001`), group variables by inferred code, then fetch the `groups/{code}.json` metadata endpoint (one call per unique group) to get labels. Falls back to empty dict if the fetch fails. Tests updated — `test_vars_returns_placeholder_dict_when_no_group` replaced with two new tests covering the happy path and the error fallback. 203 total passing.

## 2026-05-11 — Clean up melt(); rename GEO_ID → GEOIDFQ in long output (branch refactor/api-class-integration)

`CensusAPI.melt()` reworked:
- `_type_code` inner function replaced by `_variable_type` which folds the `VARIABLE_TYPES` lookup in directly, so the lambda no longer calls it twice
- `_var_label` replaced by `_variable_label` using `str.partition('!!')` instead of `re.split`
- Variable-stripping lambda replaced by `_base_code` using a single `re.match` (was calling `re.findall` twice per value)
- `long['variable'].isin(self.vars)` → `.isin(self.vars.keys())` for explicit dict-key intent

`GEO_ID` column renamed to `GEOIDFQ` in the melt output (the Census API's `GEO_ID` field always carries the full GEOIDFQ string, not a short-form geoid). Updated everywhere this column is referenced:
- `define_schema()`: field name `'GEO_ID'` → `'GEOIDFQ'`; primaryKey updated; `'NAME' in self.data.columns` → `'NAME' in self.long.columns` (checks the already-transformed data)
- `DimensionTable.__init__`: exclusion list and groupby updated
- `DimensionTable.wide()`: `sort_index(level='GEO_ID', ...)` → `'GEOIDFQ'`
- `tests/_make_long()`: column renamed

179 tests passing.

## 2026-05-11 — Use GeoIDFQ throughout geoinfo/geoids functions (branch refactor/api-class-integration)

`geoinfo_from_params` and `geoids_from_scope` with `output='list'` previously returned `list[str]` (raw GEOIDFQ strings). Changed to return `list[GeoIDFQ]` by wrapping each result in `GeoIDFQ.parse()` at the return point. `geoinfo_from_scope_sumlevel` with `output='list'` likewise updated.

Two internal callers updated to use the GeoIDFQ objects directly instead of re-parsing:
- `pseudos_from_scope_sumlevel`: `GeoIDFQ.parse(parents[0]).sumlevel.sumlevel` → `parents[0].sumlevel.sumlevel`; f-string changed to `str(parent)` to produce the full GEOIDFQ string for pseudo predicates
- `geoinfo_from_scope_sumlevel`: `GeoIDFQ.parse(scope_geoids[0]).sumlevel` → `scope_geoids[0].sumlevel`

`morpc_juris_part_to_full`, `census_geoid_to_morpc`, `morpc_geoid_to_census` are left unchanged — they deal with MORPC-specific sumlevel codes (M10, M11, M23, M24, M25) that are not valid Census GEOIDFQs and cannot be parsed by `GeoIDFQ.parse()`. 179 tests passing.

## 2026-05-11 — Move fetch to CensusAPI private methods; fix variable batching (branch refactor/api-class-integration)

Removed the module-level `fetch` function and replaced it with three private methods on `CensusAPI`:

- `_fetch()` — dispatcher: injects the API key, then routes to `_fetch_group` or `_fetch_variables` based on whether `self.group` is set (no regex needed)
- `_fetch_group(url, params)` — uses `get_text_safely` to retrieve the group() form response (a flat text stream, not valid JSON) and parses it with `pd.read_csv`
- `_fetch_variables(url, params)` — slices `self.variables` into 49-variable batches, issues one `get_json_safely` call per batch with GEO_ID prepended, then joins all result frames on GEO_ID in a single operation

Fixed two bugs in the old `fetch` variable-list path:
1. Batches of 18 (default `var_batch_size=20` minus 2) instead of the intended 49; the batch-size parameter was confusing and unnecessary now that the limit is a fixed API invariant
2. After the second iteration, `census_data` had GEO_ID as a column (post-`reset_index`), so the third batch's `join` would align on positional index instead of GEO_ID — silently producing wrong data

`fetch` removed from `morpc_census/__init__.py` exports. `CensusAPI.__init__` now calls `self._fetch()` directly (no `.reset_index()` needed — both paths return with GEO_ID as a column). Test mocks updated from `patch('morpc_census.api.fetch', ...)` to `patch.object(CensusAPI, '_fetch', ...)`. Added `TestFetchVariablesBatching` (6 tests): single batch at 49 vars, two batches at 50 vars, two batches at 98 vars, join correctness, GEO_ID in every batch request, single-row result. 68 tests passing.

## 2026-05-11 — Make `group` optional in CensusAPI; add variables-only mode (branch refactor/api-class-integration)

`CensusAPI.__init__` signature changed: `group` is now `str | Group | None = None` (moved before `sumlevel`). A `ValueError` is raised at construction if both `group` and `variables` are `None`. Three modes are now supported:

- **group only** — fetches all variables in the group via `group(CODE)` query
- **group + variables** — validates that variables are a subset of the group; fetches only those variables
- **variables only** — fetches the listed variables directly; `self.group = None`

`self.endpoint` is now stored as an instance attribute and is always set (it comes from `self.group.endpoint` when group is present, otherwise from the `endpoint` arg directly). This allows `universe`, `vars`, `_build_name`, `_build_request`, `define_schema`, `create_resource`, and `melt` to all use `self.endpoint.*` instead of `self.group.endpoint.*`.

Guarded `self.group is None` paths added to: `universe` (returns fallback string), `vars` (returns `{v: {} for v in self.variables}` placeholder dict), `melt` (`concept` field is `''` when no group), `define_schema` (log tag skips group code), `create_resource` (title/description use variable list instead of group description).

`censusapi_name` updated: `group` param is now `str | Group | None = None`; when `None`, the group segment is omitted from the name string.

`tests/test_api.py`: `_make` updated to `CensusAPI(ep, scope, group='B01001', ...)` (new positional order). Added `TestCensusAPIGroupOptional` (8 tests): raises on no-group/no-variables, stores `None` group, uppercases variables, returns placeholder `vars`, returns fallback universe string, name excludes group part, request uses variable list. 62 tests passing.

## 2026-05-11 — Rename CensusAPI and DimensionTable instance attributes to snake_case (branch refactor/api-class-integration)

ALL_CAPS instance attributes are a non-standard convention; PEP 8 reserves that style for module-level constants. Renamed all instance attributes on `CensusAPI` and `DimensionTable` to snake_case: `SCOPE`→`scope`, `SUMLEVEL`→`sumlevel`, `VARIABLES`→`variables`, `GROUP`→`group`, `REQUEST`→`request`, `DATA`→`data`, `LONG`→`long`, `UNIVERSE`→`universe`, `VARS`→`vars`, `NAME`→`name`, `FILENAME`→`filename`, `SCHEMA_FILENAME`→`schema_filename`, `SCHEMA`→`schema`, `DATAPATH`→`datapath`, `DESC_TABLE`→`desc_table`. `DimensionTable.WIDE`→`_wide` (can't use `wide`, that's an existing method). Updated tests accordingly. 54 tests passing.

## 2026-05-11 — Remove redundant passthrough properties from CensusAPI (branch refactor/api-class-integration)

Removed `SURVEY`, `YEAR`, `GROUP` (string), `CONCEPT`, and `scope_obj` properties — they were simple one-liner delegations that added noise without adding value. Callers navigate the hierarchy directly: `api.GROUP.endpoint.year`, `api.GROUP.endpoint.survey`, `api.GROUP.code`, `api.GROUP.description`, `api.SCOPE`.

Renamed `VARIABLE_GROUP` → `GROUP` (the stored `Group` instance) to free the name and make attribute access shorter: `api.GROUP.code` vs `api.VARIABLE_GROUP.code`. Updated all internal usages in `UNIVERSE`, `VARS`, `_build_name`, `_build_request`, `melt`, `define_schema`, `create_resource`. Removed `test_scope_obj_returns_scope_instance`. 54 tests passing.

## 2026-05-11 — Collapse SurveyTable + Vintage into Endpoint class (branch refactor/api-class-integration)

Removed `SurveyTable` and `Vintage` as separate classes. Replaced with a single `Endpoint(survey, year)` class that validates the survey name against `IMPLEMENTED_ENDPOINTS` and the year against the Census API's available vintages in one constructor. `Endpoint.survey` is now a plain string (no intermediate wrapper object). All properties formerly on `Vintage` (`url`, `groups`, `vintages`) and all validation formerly in `SurveyTable` now live directly on `Endpoint`.

Updated throughout: `Group.endpoint` (was `.vintage`), `CensusAPI.__init__` takes `endpoint: Endpoint`, `censusapi_name` takes `endpoint: Endpoint`, `__init__.py` exports. `TestSurveyTable` and `TestVintage` merged into `TestEndpoint` (11 tests). 55 tests passing.

## 2026-05-11 — Switch CensusAPI and censusapi_name to Vintage + Group classes (branch refactor/api-class-integration)

`CensusAPI.__init__` signature changed from `(survey_table: str | SurveyTable, year: int, group, ...)` to `(vintage: Vintage, group: str | Group, ...)`. When `group` is a string it is normalized to `Group(vintage, group.upper())`; when it's already a `Group` instance, it's used directly and the `vintage` arg is ignored (the group carries its own vintage). This completes the push to have callers work with class instances rather than loose strings and ints.

`censusapi_name` signature likewise changed from `(survey_table: str, year: int, scope, group: str, ...)` to `(vintage: Vintage, scope, group: str | Group, ...)`. Accepts a `Group` instance for `group` (uses `.code`). `CensusAPI._build_name` now delegates directly to `censusapi_name` rather than duplicating the string-building logic.

`TestCensusapiName`: added `mock_endpoints` autouse fixture (patches `get_all_avail_endpoints`); all 12 test calls updated to pass `Vintage(...)` as first arg. `TestCensusAPIClassNormalization._make`: `Vintage('acs/acs5', 2023)` constructed inside the mock context and passed to `CensusAPI`. 63 tests passing.

## 2026-05-11 — Add Census API key support via python-dotenv (branch refactor/api-class-integration)

Added `_get_api_key()` function that loads `CENSUS_API_KEY` following dotenv convention: `load_dotenv(..., override=False)` so environment variables already set in the shell take precedence over `.env` file values. Uses `find_dotenv(usecwd=True)` to search upward from the current working directory for a `.env` file.

The key is injected into every Census API network call: `get_all_avail_endpoints`, `Vintage.groups`, `Group.universe`, `Group.variables`, and `fetch` (injected once at the top of `fetch` so both the group-query and variable-list code paths pick it up). When no key is found, calls proceed without the parameter (Census API still works, subject to unauthenticated rate limits).

`python-dotenv` added to `pyproject.toml` dependencies. 4 new tests in `TestGetApiKey` covering: key from env var, None when not set, `override=False` enforcement, and `usecwd=True` on `find_dotenv`. 81 tests passing.

## 2026-05-11 — Replace CensusAPI plain-string attributes with class hierarchy properties (branch refactor/api-class-integration)

`CensusAPI.SURVEY`, `YEAR`, `GROUP`, `CONCEPT` are now `@property` accessors that read directly from `self.VARIABLE_GROUP.vintage.survey.name`, `.vintage.year`, `.code`, and `.description` respectively. `UNIVERSE`, `VARS`, and `NAME` are `@cached_property` — computed once on first access, not eagerly in `__init__`.

Removed `_fetch_metadata()` entirely (its logic is now spread across the three cached_properties). Removed `validate()` no-op. Removed unused `OrderedDict` import. Moved `_build_name()` ahead of `_build_request()` in the class body. `CensusAPI.__init__` no longer sets `self.SURVEY`, `self.YEAR`, `self.GROUP`, `self.CONCEPT`, `self.UNIVERSE`, `self.VARS`, or calls `censusapi_name()` — all of those come from the class hierarchy lazily.

The `UNIVERSE` fallback (use 2023 vintage when year < 2023) now passes the already-normalized `SurveyTable` instance (`self.VARIABLE_GROUP.vintage.survey`) to the fallback `Vintage`, avoiding a redundant string re-validation.

Test: removed `api.CONCEPT = 'Sex by Age'` (was masking the property; property already returns the right value from mock data). 77 tests passing.

## 2026-05-07 — Move network helpers into class methods (branch refactor/api-class-integration)

Removed `get_table_groups`, `get_group_variables`, `get_group_universe` as standalone module functions. Logic now lives directly in the class that uses it: `Vintage.groups`, `Group.variables`, and `Group.universe` each do their own `get_json_safely` call. All three removed from `__init__.py` exports — callers access the data through class instances instead.

Tests updated: mock target changes from `morpc_census.api.get_table_groups` etc. to `morpc.req.get_json_safely`, with URL-dispatching side effects. `test_groups_delegates_to_get_table_groups` → `test_groups_fetches_from_api`; similarly for variables and universe.

## 2026-05-07 — Remove standalone validation/utility functions from api.py (branch refactor/api-class-integration)

Removed 7 standalone functions that were made redundant by the class hierarchy:
- `valid_survey_table`, `valid_vintage`, `valid_group`, `valid_variables` → validation now happens in `SurveyTable.__init__`, `Vintage.__init__`, `Group.__init__`, and `CensusAPI.__init__` respectively
- `get_query_url` → superseded by `Vintage.url`
- `get_params`, `get_api_request` → absorbed into `CensusAPI._build_request()`

The module now has a clear two-layer structure: (1) network primitives (`get_all_avail_endpoints`, `get_table_groups`, `get_group_variables`, `get_group_universe`, `fetch`) used by class cached_properties; (2) classes (`SurveyTable` → `Vintage` → `Group` → `CensusAPI`) that expose the hierarchy cleanly.

`CensusAPI._build_request()` builds `{url, params}` directly from `self.VARIABLE_GROUP.vintage.url` and `geoinfo_from_scope_sumlevel(self.SCOPE, self.SUMLEVEL)`.

Tests: removed `TestValidSurveyTable`, `TestValidVintage`, `TestGetParams` (all covered by `TestSurveyTable`/`TestVintage`/`TestGroup`); added 3 edge-case tests to `TestSurveyTable`; updated `_make` to patch `morpc_census.geos.geoinfo_from_scope_sumlevel` instead of the removed `get_api_request`. 77 tests passing.

## 2026-05-07 — Add SurveyTable, Vintage, Group classes to api.py (branch refactor/api-class-integration, issue #54)

Added three classes representing the Census API endpoint hierarchy:

- `SurveyTable(name)` — validates against `IMPLEMENTED_ENDPOINTS`; `vintages` cached_property calls `get_all_avail_endpoints()` once.
- `Vintage(survey, year)` — accepts str or SurveyTable; validates year against `survey.vintages`; `url` property; `groups` cached_property calls `get_table_groups()` once.
- `Group(vintage, code)` — requires a Vintage instance; uppercases code; validates against `vintage.groups`; `description` reads from already-cached groups dict; `variables` cached_property; `universe` property.

`CensusAPI.__init__` updated: constructs `Group(Vintage(survey_table, year), group)` instead of calling `valid_survey_table`/`valid_vintage`/`valid_group` separately. All three validation steps happen inside the class constructors. `self.VARIABLE_GROUP` holds the Group instance; `self.SURVEY`/`self.YEAR`/`self.GROUP` remain as plain strings for backwards compat. `_fetch_metadata` delegates to `self.VARIABLE_GROUP.description`/`.universe`/`.variables`. `validate()` is now a no-op.

All three classes exported from `morpc_census/__init__.py`. `tests/test_api.py` updated: `TestCensusAPIClassNormalization._make` now mocks `get_all_avail_endpoints`/`get_table_groups` instead of the removed `valid_survey_table`/`valid_vintage`/`valid_group` calls. Three new test classes added: `TestSurveyTable` (7 tests), `TestVintage` (9 tests), `TestGroup` (10 tests). Total: 89 tests passing.

## 2026-05-07 — Extract domain lookup tables to constants.py (branch refactor/api-class-integration, issue #53)

Created `morpc_census/constants.py` containing the 10 domain lookup tables that were defined at the top of `api.py` but not used within it: `HIGHLEVEL_GROUP_DESC`, `HIGHLEVEL_DESC_FROM_ID`, `AGEGROUP_MAP`, `AGEGROUP_SORT_ORDER`, `RACE_TABLE_MAP`, `EDUCATION_ATTAIN_MAP`, `EDUCATION_ATTAIN_SORT_ORDER`, `INCOME_TO_POVERTY_MAP`, `INCOME_TO_POVERTY_SORT_ORDER`, `NTD_AGEMAP`, `NTD_AGEMAP_ORDER`.

`api.py` re-exports them via `from morpc_census.constants import ...` so the public API is unchanged. `__init__.py` now imports them from `constants` directly. API-machinery constants (`MISSING_VALUES`, `VARIABLE_TYPES`, `CENSUS_DATA_BASE_URL`, `IMPLEMENTED_ENDPOINTS`) stay in `api.py`.

Note: test suite revealed a pre-existing inconsistency — `NTD_AGEMAP` maps to `'19 to 64 years'` but `NTD_AGEMAP_ORDER` has `'20 to 64 years'`. Not fixed here; tracked for a future cleanup.

## 2026-05-07 — Integrate Scope/SumLevel classes into api.py (branch refactor/api-class-integration, issue #52)

`censusapi_name` now accepts `str | Scope` for scope and `str | SumLevel | None` for sumlevel. Replaced `from morpc import HIERARCHY_STRING_FROM_CENSUSNAME` lookup with `SumLevel.hierarchy_string` property. Falls back to `sl.name` when `hierarchy_string` is None (partially-constructed SumLevel edge case).

`get_api_request` type hints updated to `str | Scope` / `str | SumLevel | None`.

`CensusAPI.__init__` now normalizes both parameters on entry:
- `self.SCOPE` is always a `Scope` instance (was a lowercase string)
- `self.SUMLEVEL` is always a `SumLevel` or `None` (was a lowercase string or None)
- `censusapi_name` called with already-normalized objects so name is consistent

`CensusAPI.scope_obj` simplified — returns `self.SCOPE` directly (was `SCOPES[self.SCOPE]`).

`CensusAPI.create_resource` updated: `self.SUMLEVEL.plural` replaces manual `f'{sumlevel}s'` string; `self.SCOPE.name` replaces raw `self.SCOPE` in title/description.

`from __future__ import annotations` and `TYPE_CHECKING` guard added for geos imports.

9 new tests in `TestCensusAPIClassNormalization`; 4 new cases added to `TestCensusapiName`. All 46 api tests pass.

## 2026-05-07 — Fix resource_from_geometry_sumlevel; fix notebook frictionless.Resource access (branch refactor/tigerweb-class-integration)

`resource_from_geometry_sumlevel` was broken: it passed spatial params (`geometry`, `geometryType`, `inSR`, `spatialRel`) as kwargs to `morpc.rest_api.resource()`, which only accepts `(name, url, where, outfields, max_record_count)`. Fixed by building `frictionless.Resource` directly, using `totalRecordCount(url, where='1=1')` for total_records and `maxRecordCount` for the service page size.

Demo notebook cells fixed: `frictionless.Resource` has no `.url`, `.where`, `.outfields`, or `.params` attributes. Correct access: `.path` for the URL; `.to_dict()['_metadata']['params']` for the query dict (`where` → `params['where']`, `outFields` → `params['outFields']`, `geometry` → `params['geometry']`).

## 2026-05-07 — Refactor tigerweb module; add SumLevel.parts (branch refactor/tigerweb-class-integration, issue #47)

Added `SumLevel.parts` property (list of geo component field names, e.g. `['state', 'county']`). Implemented using `_geoidfq_geo_fields` so the geo schema lives in one place.

`tigerweb.py` fully refactored:
- `outfields_from_scale` removed — callers now build outfields inline with `SumLevel.parts`
- `get_layer_url` accepts `str | SumLevel`; if given a SumLevel it reads `tigerweb_name` directly
- `where_from_scope` accepts `str | Scope`; delegates to `Scope.sql`
- `resource_from_scope_scale` and `resource_from_geometry_scale` accept `str | Scope` and `str | SumLevel`; all `SUMLEVEL_DESCRIPTIONS`, `SUMLEVEL_FROM_CENSUSQUERY`, and `HIERARCHY_STRING_FROM_CENSUSNAME` lookups replaced with `SumLevel` properties (`tigerweb_name`, `hierarchy_string`, `parts`)
- Type hints and docstrings added throughout; `from __future__ import annotations` added
- Congressional-district regex in `get_tigerweb_layers_map` generalised (was hard-coded to "11Xth")

`outfields_from_scale` removed from `__init__.py` exports.

## 2026-05-07 — Rename *_sumlevel_scope → *_scope_sumlevel; add Scope/SumLevel types to fetch (branch refactor/geos-class-integration)

Renamed `pseudos_from_sumlevel_scope` → `pseudos_from_scope_sumlevel` and `fetch_geos_from_sumlevel_scope` → `fetch_geos_from_scope_sumlevel` throughout geos.py, __init__.py, and the demo notebook. Consistent with the `geoinfo_from_scope_sumlevel` naming convention (scope first, then sumlevel).

`fetch_geos_from_scope_sumlevel` now accepts `str | Scope` and `str | SumLevel | None`, normalising to `Scope`/`SumLevel` objects before delegating to `geoinfo_from_scope_sumlevel`.

## 2026-05-07 — Refactor fetch_geos_from_geoids; GeoIDFQ.__repr__ shows components (branch refactor/geos-class-integration)

Extracted `_fetch_layer(sumlevel: SumLevel, geoids, year, survey, chunk_size)` private helper that handles one sumlevel group, including internal chunking. Uses `sl.tigerweb_name` directly instead of re-accessing `morpc.SUMLEVEL_DESCRIPTIONS`.

`fetch_geos_from_geoids` signature changed from `list[str]` to `list[GeoIDFQ]`. Groups by `SumLevel` key (not raw string) before calling `_fetch_layer`. Eliminates duplicated if/else chunking logic and the pattern of chunking by position before grouping by sumlevel.

`fetch_geos_from_sumlevel_scope` updated to parse GEOIDFQ strings into `GeoIDFQ` objects once, then uses `.geoid` for the short ID (replaces `x.split('US')[-1]`).

`GeoIDFQ.__repr__` changed to show parsed components (e.g. `GeoIDFQ(sumlevel='140', variant='00', geocomp='00', state='39', county='041', tract='010100')`) instead of the raw concatenated string. `__str__` unchanged — still returns the full GEOIDFQ string.

## 2026-05-07 — Reorder geos functions; integrate Scope/SumLevel/GeoIDFQ (branch refactor/geos-class-integration)

Reordered functions in logical dependency order:
geoinfo_from_params → geoids_from_scope → get_query_req → pseudos_from_sumlevel_scope → geoinfo_for_hierarchical_geos → geoinfo_from_scope_sumlevel

Class integration:
- geoids_from_scope: now accepts str | Scope
- pseudos_from_sumlevel_scope: accepts str | SumLevel and str | Scope; uses GeoIDFQ.parse for parent sumlevel
- geoinfo_for_hierarchical_geos: accepts str | Scope and str | SumLevel; calls sl.get_query_req() and sc.params directly
- geoinfo_from_scope_sumlevel: accepts str | Scope and str | SumLevel | None; derives scope_sl via GeoIDFQ.parse; compares SumLevel instances
- columns_to_geoidfq: fixed for new GeoIDFQ.build(**kwargs) API; handles SumLevel objects in sumlevel column


## 2026-05-07 — GeoIDFQ parts as kwargs; SumLevel.get_query_req (branch refactor/geos-class-integration, #45)

Two refactors on branch refactor/geos-class-integration:
1. GeoIDFQ converted from @dataclass to regular class — geo components (state, county, tract, etc.)
   are now kwargs stored as direct attributes instead of a parts dict. Access as g.state, g.county, etc.
   GeoIDFQ.build() signature changed: parts dict replaced with **kwargs. parts property kept for compat.
2. SumLevel.get_query_req(year) added as a method. Module-level get_query_req() now delegates to it.


## 2026-05-07 — Fix GeoIDFQ with SumLevel sumlevel type (main, 460075e)

Fixed three bugs introduced when GeoIDFQ.sumlevel was changed to str | SumLevel:
- __str__ crashed with TypeError when sumlevel is a SumLevel (added .sumlevel code extraction)
- build() lost its variant="00" default (now resolves None → current_variant or "00")
- SumLevel.__post_init__ crashed on desc["current_variant"] for sumlevels that don't have it yet (changed to .get())

Updated test suite:
- test_geoidfq_class: sumlevel assertions now compare to SumLevel instances
- test_geos_classes: removed two equality tests (lookup vs explicit differ in metadata); updated tract singular/plural to match morpc-py values
- test_smoke: lazy-load check now uses subprocess to avoid test-ordering interference


## 2026-05-06 — Defer morpc imports so network is only needed when a function runs (closes #43)

`import morpc_census` was hanging whenever the Census API was slow or unresponsive because `morpc/__init__.py` unconditionally imports `morpc.census`, which makes a live HTTP request at module level with no timeout.

**`geos.py`**:
- Removed top-level `import morpc` and `import morpc.req`
- Replaced the module-level `STATE_SCOPES` / `COUNTY_SCOPES` / `MORPC_REGION_SCOPES` / `SCOPES` construction (which required `morpc` constants) with a `_LazyScopes` dict subclass. `_LazyScopes` overrides all dict access methods to call `_load()` on first use; `_load()` imports morpc and builds the full dict at that point. The public `SCOPES` API (`SCOPES["franklin"]`, `"us" in SCOPES`, `list(SCOPES.keys())`) is unchanged.
- Added `import morpc` inside `geoinfo_from_scope_sumlevel` and `fetch_geos_from_geoids` (the two remaining functions with bare `morpc.*` references)
- Added `import morpc.req` inside `geoinfo_from_params`

**`api.py`**:
- Removed top-level `from morpc.req import get_json_safely, get_text_safely`
- Added `from morpc.req import get_json_safely` inside `get_all_avail_endpoints`, `get_table_groups`, `get_group_variables`, and `get_group_universe`
- Added `from morpc.req import get_json_safely, get_text_safely` inside `fetch`

After this change, `import morpc_census` requires no network access. Network calls only happen when a function that talks to the Census API or TIGERweb is actually invoked.

Added two tests to `tests/test_smoke.py`: `test_geos_module_imports` and `test_scopes_is_not_accessed_at_import` (asserts `SCOPES._loaded is False` immediately after import).
## 2026-05-06 — Populate SumLevel metadata fields from SUMLEVEL_DESCRIPTIONS in __post_init__ (closes #41)

Extended `SumLevel.__post_init__` so that `singular`, `plural`, `hierarchy_string`, and `tigerweb_name` are populated automatically from `morpc.SUMLEVEL_DESCRIPTIONS` whenever the lookup runs (i.e. when constructing by name or code alone):

- `SumLevel("county")` now yields `singular="county"`, `plural="counties"`, `hierarchy_string="COUNTY"`, `tigerweb_name="counties"`
- `SumLevel("050")` yields the same result via the code path
- Fully-explicit construction (`SumLevel(name, sumlevel, ...)`) still skips `__post_init__` lookup, so explicitly-supplied metadata (including `None`) is preserved unchanged

Implementation: refactored both branches of `__post_init__` to store the matched `desc` dict, then set all four metadata fields with `object.__setattr__` in a shared tail block. Used a `for/else` to detect the not-found case in the query-name branch.

Updated `tests/test_geos_classes.py` (35 tests total, up from 31):
- Renamed `test_optional_fields_default_to_none` → `test_explicit_metadata_fields_default_to_none` to clarify it applies to explicit construction only
- Added 4 new tests: `from_name_populates_metadata`, `from_code_populates_metadata`, `from_name_and_from_code_metadata_match`, `from_name_tract_populates_metadata`

## 2026-05-06 — Update geos demo notebook to show updated SumLevel usage (closes #39)

Updated `doc/01-morpc-geos-demo.ipynb` to reflect the `SumLevel` changes from PR #38:

- Intro: "Scale" → "SumLevel" in the two-bullet summary
- Imports: replaced `valid_sumlevel` with `SumLevel`
- Section 2 heading: "Scales — choosing resolution" → "Summary levels — choosing resolution"; updated prose to describe the name/code lookup and optional metadata fields
- Replaced `valid_sumlevel("county")` cell with `SumLevel("county")` — shows auto-fill of the three-digit code
- Replaced `valid_sumlevel("tract")` cell with `SumLevel("050")` — shows auto-fill of the query name from the code
- Added cell showing optional metadata fields default to `None` when constructing by name/code alone
- Added cell showing a fully-specified `SumLevel` with all metadata fields supplied explicitly
- Updated ValueError cell to use `SumLevel("neighborhood")` instead of `valid_sumlevel`
## 2026-05-06 — Add optional metadata fields to SumLevel (closes #37)

Added four optional fields to the `SumLevel` dataclass sourced from `morpc.SUMLEVEL_DESCRIPTIONS`:

- `singular: str | None` — singular display name (e.g. `"county"`)
- `plural: str | None` — plural display name (e.g. `"counties"`)
- `hierarchy_string: str | None` — hierarchical label (e.g. `"COUNTY"`)
- `tigerweb_name: str | None` — TIGERweb REST API layer name (renamed from `censusRestAPI_layername`; e.g. `"counties"`)

All fields default to `None` and are not auto-filled in `__post_init__` — callers supply them explicitly when needed. Being part of a frozen dataclass, they are immutable after construction.

Added 3 new tests in `tests/test_geos_classes.py`: defaults to `None`, explicit values stored correctly, frozen after construction.

## 2026-05-06 — Allow Scope and SumLevel to be constructed from name or code alone (closes #37)

Added `__post_init__` lookup to both dataclasses so they can be constructed from a single identifier without needing to know all fields upfront:

- `Scope("franklin")` — `for_param` and `in_param` are looked up from the built-in `SCOPES` registry; fully-explicit construction unchanged
- `SumLevel("county")` — three-digit code looked up from `morpc.SUMLEVEL_DESCRIPTIONS`; fills `sumlevel` field
- `SumLevel("050")` — query name looked up from `morpc.SUMLEVEL_DESCRIPTIONS`; fills `name` field
- Both raise `ValueError` with a helpful message for unrecognized names or codes

Implementation notes:
- `Scope.for_param` changed from required (`str`) to optional (`str | None = None`); `__post_init__` triggers the lookup when `None`
- `SumLevel.sumlevel` changed from required to optional (`str = ""`); `__post_init__` detects whether the `name` argument is a three-digit code (regex `^\d{3}$`) or a query name and fills the missing field using `object.__setattr__` (required for frozen dataclasses)
- `valid_sumlevel()` simplified to a one-line wrapper around `SumLevel()` since the validation logic now lives in `__post_init__`

Added 12 new tests in `tests/test_geos_classes.py` (total now 28):
- `TestSumLevel`: 8 new tests — lookup by name, lookup by code, equality with explicit form, invalid name/code raise `ValueError`
- `TestScopeFromName`: 4 new tests — lookup by name for county/region/national scope, invalid name raises `ValueError`

## 2026-05-06 — Align api.py with geos.py: sumlevel rename, type hints, tests, notebook rewrite (closes #35)

Updated `morpc_census/api.py` to match the geos.py changes made in PRs #31–#34:

- All `scale` references renamed to `sumlevel`: `self.SCALE` → `self.SUMLEVEL`, `scale_part` → `sumlevel_part`, `scale_str` → `sumlevel_str`, keyword argument `scale=` → `sumlevel=` in `CensusAPI`, `get_api_request`, and `censusapi_name`
- `CensusAPI.validate()` now calls `valid_sumlevel()` (from geos.py) to validate the sumlevel and stores the result as `self._sumlevel` (a `SumLevel` object)
- Added two new `CensusAPI` properties: `scope_obj` (returns the `Scope` object for the dataset's geographic extent) and `geoidfqs` (parses the `GEO_ID` column into a list of `GeoIDFQ` objects)
- Added type hints and docstrings to all public functions: `valid_survey_table`, `valid_vintage`, `get_query_url`, `get_table_groups`, `valid_group`, `get_group_variables`, `get_group_universe`, `valid_variables`, `get_params`, `fetch`, `find_replace_variable_map`, `censusapi_name`, `get_api_request`
- Deleted `morpc_census/census.py` (dead code; its three helper functions were unused) and removed its imports from `__init__.py`

Added `tests/test_api.py` with 33 offline tests (all pass):
- `TestValidSurveyTable` (6 tests): recognized/unrecognized/partial/empty endpoints
- `TestGetParams` (5 tests): group query string and variable list comma-join
- `TestCensusapiName` (8 tests): no sumlevel, tract/county sumlevels, variables suffix, dec, lowercase
- `TestFindReplaceVariableMap` (5 tests): basic replacement, sequential codes, unmatched, duplicates, prefix
- `TestDimensionTableDescriptionTable` (5 tests): DataFrame shape, index, column split
- `TestValidVintage` (4 tests): mocked `get_all_avail_endpoints`, valid/invalid year, unknown survey

Updated `tests/test_smoke.py`: removed `test_census_module_imports` since `census.py` was deleted.

Rewrote `doc/02-morpc-census-demo.ipynb` with a usage-first narrative:
- Section 1: available surveys (`IMPLEMENTED_ENDPOINTS`, `get_all_avail_endpoints`)
- Section 2: variables in a group (`get_table_groups`, `get_group_variables`)
- Section 3: scopes and sumlevels (`SCOPES`, `PSEUDOS`)
- Section 4: fetching data (`CensusAPI`, `.DATA`, `.LONG`, `.scope_obj`, `.geoidfqs`)
- Section 5: analyzing with `DimensionTable` (`.wide()`, `.percent()`)
- Section 6: time series (concat LONG from multiple years)
- Section 7: saving data (`.save()`)

## 2026-05-05 16:30 — Rename 'scale' to 'sumlevel' throughout geos.py (closes #33)

Removed every use of the term "scale" from `morpc_census/geos.py` and replaced with "sumlevel":

- `valid_scale` → `valid_sumlevel`; parameter `scale` → `sumlevel`; log messages and error text updated
- `get_query_req`: parameter `scale` → `sumlevel`; internal variable `sumlevel` → `sumlevel_code` to avoid shadowing
- `geoinfo_for_hierarchical_geos`: parameter `scale` → `sumlevel`; updated internal call and format string
- `geoinfo_from_scope_scale` → `geoinfo_from_scope_sumlevel`: parameter `scale` → `sumlevel`; local variable `scale_sumlevel` → `query_sumlevel`; all log messages and comments updated
- `pseudos_from_scale_scope` → `pseudos_from_sumlevel_scope`: parameter `scale` → `sumlevel`; local variable `sumlevel` (parent code) → `parent_sumlevel` to avoid shadowing
- `fetch_geos_from_scale_scope` → `fetch_geos_from_sumlevel_scope`: parameter `scale` → `sumlevel`; internal call updated

Updated `morpc_census/__init__.py` exports to match new names.
Updated `doc/01-morpc-geos-demo.ipynb`: import, function calls, and keyword argument `scale=` → `sumlevel=`.

All 50 tests pass.

## 2026-05-05 16:00 — Add type hints and docstrings to geos.py (closes #31)

Added type annotations and short docstrings to all public functions in `morpc_census/geos.py`:

- Added `from geopandas import GeoDataFrame` to top-level imports for use in return types
- `valid_scale`, `valid_scope` — added `str` param types, `SumLevel` / `bool | None` returns, one-line docstrings
- `get_query_req` — annotated `scale: str`, `year: str`, `-> dict`; added docstring
- `geoinfo_for_hierarchical_geos` — replaced empty multi-line docstring stub with signature `(str, str) -> DataFrame` and one-line docstring
- `geoinfo_from_scope_scale` — added `-> list | DataFrame | dict` return type; tightened existing docstring to standard NumPy style
- `geoids_from_scope` — added `scope: str`, `-> list | DataFrame`; added docstring
- `pseudos_from_scale_scope` — added `(str, str) -> list[str]`; added docstring
- `geoinfo_from_params` — corrected return type from `-> list` to `-> list | DataFrame`; collapsed verbose docstring to one line
- `fetch_geos_from_geoids` — added `geoidfqs: list[str]`, `chunk_size: int`, `-> GeoDataFrame`; collapsed docstring to one line
- `fetch_geos_from_scale_scope` — fully annotated `(str, str | None, int | None, Literal, int) -> GeoDataFrame`; collapsed docstring
- `morpc_juris_part_to_full`, `census_geoid_to_morpc`, `morpc_geoid_to_census` — added param types and `-> DataFrame` returns; preserved existing verbose docstrings
- `geoidfq_to_columns` — added `-> DataFrame | GeoDataFrame`; added docstring
- `columns_to_geoidfq` — added docstring

All 50 existing tests pass.

## 2026-05-05 15:30 — Rewrite 01-morpc-geos-demo.ipynb — add GeoIDFQ, usage-first framing (closes #29)

Rewrote `doc/01-morpc-geos-demo.ipynb` with a usage-first structure. Replaced the previous version (which led with dataclass field tables and internal implementation details) with a workflow-oriented narrative covering four topics:

- **Scopes** — lists `SCOPES.keys()`, looks up individual scopes, notes the 15-county region's multi-county `for_param`
- **Scales** — calls `valid_scale()` on recognized names, shows the returned `SumLevel` fields, demonstrates the `ValueError` for unrecognized names
- **Fetching geometries** — `fetch_geos_from_scale_scope(scope, scale)` with county and tract examples; `.plot()` calls; network note at section header
- **GEOIDFQs** — `GeoIDFQ.parse()`, `.parts`, `.geoid`, `GeoIDFQ.build()`, `str()`, and a worked example parsing the `GEO_ID` column from a prior fetch

Also added a `test_geoidfq_class.py` expansion (closes #27) covering sumlevels 100, 140, 150 in the same PR #28 cycle (previously noted at 2026-05-05 11:02).

## 2026-05-05 11:02 — Add GeoIDFQ class, refactor geoidfq_to_columns / columns_to_geoidfq (closes #25)

Added `GeoIDFQ` dataclass to `morpc_census/geos.py` to encapsulate GEOIDFQ parsing and construction:

- `GeoIDFQ.parse(geoidfq_str)` — slices a GEOIDFQ string into `sumlevel`, `variant`, `geocomp`, and `parts` using field widths from `SUMLEVEL_DESCRIPTIONS[sumlevel]["geoidfq_format"]`
- `GeoIDFQ.build(sumlevel, parts, variant="00", geocomp="00")` — constructs from components; raises `ValueError` for MORPC sumlevels (no `geoidfq_format`) or mismatched parts keys
- `__str__()` — reconstructs the full GEOIDFQ string
- `geoid` property — short-form ID after `"US"` (used in REST API queries)
- Variant codes documented in class docstring per Census geo-variant system (`"00"` default; `"01"`–`"59"` CDs; `"Ux"`/`"Lx"` SLDs; `"Mx"` CBSAs; `"Cx"` UAs; `"Px"` PUMAs; `"Zx"` ZCTAs)

Refactored `geoidfq_to_columns` to use `GeoIDFQ.parse()` instead of inline regex + slicing. Fixed `columns_to_geoidfq` — it referenced `SUMLEVEL_DESCRIPTIONS[sumlevel]['current_variant']` (a key that does not exist), causing a `KeyError` at runtime; replaced with `GeoIDFQ.build()` and an explicit `variant` parameter (default `"00"`).

34 new tests in `tests/test_geoidfq_class.py` covering sumlevels 040, 050, 100, 140, 150, 160, 310, and 500; all 54 tests pass.

## 2026-05-05 10:39 — Rename Scale class to SumLevel (closes #23)

Renamed the `Scale` dataclass to `SumLevel` everywhere it appeared:
- Class definition and `-> SumLevel` return type in `morpc_census/geos.py`
- Export in `morpc_census/__init__.py`
- `TestScale` → `TestSumLevel` and all constructor calls in `tests/test_geos_classes.py`
- Import and cell source in `doc/01-morpc-geos-demo.ipynb`

## 2026-05-05 10:15 — Rewrite 01-morpc-geos-demo.ipynb for Scale and Scope (closes #18)

Deleted the old notebook (which documented morpc-py's `load_spatial_data` / `assign_geo_identifiers` — unrelated to morpc-census) and replaced it with a focused demo of the new `Scope` and `Scale` classes. The new notebook covers:
- Constructing a `Scope` directly and reading `.params`
- Browsing the built-in `SCOPES` dict
- Constructing a `Scale` directly and using `valid_scale()`
- Using `fetch_geos_from_scale_scope()` with scope and scale name strings

Network-dependent cells are marked with a note. No new unit tests added — the classes are tested in `tests/test_geos_classes.py`.

## 2026-05-05 10:09 — Add Scale and Scope dataclasses to geos module (closes #16)

Replaced the plain `dict[str, dict]` pattern in `SCOPES` with two dataclasses:

- **`Scope(name, for_param, in_param=None)`** — represents a named Census API geography scope. The `.params` property returns the `{"for": ..., "in": ...}` dict consumed by Census API calls.
- **`Scale(name, sumlevel)`** — frozen dataclass pairing a Census query name (e.g. `"county"`) with its summary level code (e.g. `"050"`).

`STATE_SCOPES`, `COUNTY_SCOPES`, and `MORPC_REGION_SCOPES` now produce `list[Scope]` instead of lists of dicts. `SCOPES` is now typed `dict[str, Scope]`. All internal call sites that accessed `SCOPES[scope]` as a raw dict were updated to use `.params`.

`valid_scale()` now returns a `Scale` object instead of `True`, giving callers the resolved sumlevel without a second lookup into `morpc.SUMLEVEL_DESCRIPTIONS`.

Both classes exported from `morpc_census/__init__.py`. 16 new tests in `tests/test_geos_classes.py`; all pass.

## 2026-05-05 09:41 — dev_notes.md: add times to headers, reorder descending (closes #14)

Added times to all section headers (format `YYYY-MM-DD HH:MM — Title`).
Reordered sections descending so the most recent entry is always at the top.
Times for historical entries back-filled from git commit timestamps.

## 2026-05-05 08:54 — Rewrite README.md and doc/index.md

Both files were copied from morpc-py and described the full morpc-py package.
Rewrote to describe morpc-census: its purpose (Census API access, long-format
tables, frictionless metadata), its four modules (api, geos, census, tigerweb),
installation instructions using the correct package name and import path
(`morpc_census`), and links to the remaining notebooks.

## 2026-05-05 08:49 — Remove non-census notebooks from doc/

Deleted notebooks and log files from `doc/` that covered morpc-py features
unrelated to census (countylookup, varlookup, REST API, frictionless, plot,
color, and the general morpc-py demo log). Kept:
- `05-morpc-geos-demo.ipynb` — geos is part of morpc-census
- `07-morpc-census-demo.ipynb` and its rendered HTML

## 2026-05-04 18:00 — Split from morpc-py, refactor api module

### Context
morpc-census was a direct fork of morpc-py, meaning both repos contained identical
files. The goal is for morpc-census to be an independent package focused solely on
Census data tools. morpc-py remains the general-purpose MORPC utility library.
morpc-census may depend on morpc-py; morpc-py must not depend on morpc-census.

### Package split (step 1): remove morpc-py-specific files
Deleted from morpc-census everything that belongs to morpc-py and has nothing to
do with Census data:
- `morpc/morpc.py` — MORPC-specific constants (county IDs, region maps, etc.)
- `morpc/logs.py` — logging configuration
- `morpc/geocode.py` — Nominatim geocoding
- `morpc/color/` — MORPC branding colors
- `morpc/plot/` — MORPC plotting utilities
- `tests/test_utils.py` — tests for morpc-py utility functions

Updated `morpc/__init__.py` to remove imports of the deleted modules.
Updated `pyproject.toml`: removed `IPython`, `xlsxwriter`, `plotnine` dependencies
(only needed by the deleted modules).

### Package split (step 2): separate namespaces, remove all shared files
Moved census module to a dedicated `morpc_census/` top-level package so that
morpc-census and morpc-py no longer share the `morpc` namespace.

- Created `morpc_census/` with `api.py`, `census.py`, `geos.py`, `tigerweb.py`,
  `__init__.py`, and JSON data files moved from `morpc/census/`.
- Updated all internal cross-imports: `from morpc.census.X` → `from morpc_census.X`.
- Deleted the entire `morpc/` directory from morpc-census, including the shared
  utilities `req.py`, `utils.py`, `frictionless/`, `rest_api/` — these come from
  morpc-py at runtime via the declared dependency.
- Updated `app/app.py`: `from morpc.census import api` → `from morpc_census import api`.
- Updated `pyproject.toml`:
  - Package name: `morpc-census`
  - Added `morpc` (morpc-py) as a dependency
  - Version now sourced from `morpc_census.__version__`
  - `morpc_census.__init__` sets `__version__ = "0.1.0"`

External imports that reference `morpc.*` (constants, `morpc.req`, `morpc.frictionless`,
`morpc.rest_api`) are intentionally left as-is — they resolve through the installed
morpc-py package.

### api.py refactor
Rewrote `morpc_census/api.py` to fix correctness issues and clean up structure.

**Bugs fixed:**
- `ALL_AVAIL_ENDPOINTS` was referenced in `valid_vintage()` but never defined
  (the code that built it was commented out because it made a network call at
  import time). Replaced with `get_all_avail_endpoints()`, a lazy function that
  fetches once and caches the result.
- `from morpc.frictionless import ...` in `save()` and `create_resource()` replaced
  with direct frictionless calls — morpc-census no longer bundles those wrappers.

**Structural changes:**
- Removed unused imports: `from enum import unique`, `from types import NoneType`,
  `from numpy import var`.
- Consolidated imports: `json`, `re`, `os`, `numpy`, `pandas`, `StringIO` moved to
  top-level.
- `get()` renamed to `fetch()` to avoid shadowing the Python built-in.
- `CensusAPI._fetch_metadata()` extracted to group the three API calls that retrieve
  `CONCEPT`, `UNIVERSE`, and `VARS`.
- `CensusAPI.melt()` simplified: cleaner `id_vars` logic, more defensive regex.
- `CensusAPI.define_schema()` uses a `_VALUE_FIELD_DEFS` dict instead of a chain
  of `if column ==` blocks.
- `CensusAPI.save()` creates output directory automatically; uses `pathlib.Path`.
- `CensusAPI.create_resource()` builds a frictionless `Resource` descriptor directly.
- `DimensionTable`: fixed `!= None` → `is not None`; removed `wrapping_func` (text
  wrapping belongs in a presentation layer, not a data class);
  `create_description_table()` rewritten to avoid integer-index fragility.

## 2026-05-12 — Reduce Census API requests from 4 to 2 per CensusAPI call (branch refactor/api-class-integration)

`CensusAPI(ep, scope, group=group, sumlevel='tract')` was making 4 network calls:
1. `geoinfo_from_scope_sumlevel` → `geoids_from_scope` (geoinfo API)
2. `pseudos_from_scope_sumlevel` → `geoids_from_scope` again (duplicate)
3. `_fetch_group` (Census data API)
4. `melt` → `Group.universe` → separate `/groups` API call

Two fixes, reducing to 2 calls (1 geoinfo + 1 data):

- `pseudos_from_scope_sumlevel` now accepts an optional `scope_geoids` parameter; `geoinfo_from_scope_sumlevel` passes its already-fetched list instead of re-fetching
- `Endpoint.groups` now stores the `universe` field (stripping the trailing space present in the Census API response); `Group.universe` is demoted from `@cached_property` to `@property` and reads from the cached `endpoint.groups` dict — no separate API call needed

Test updates: `test_universe_fetches_from_api` → `test_universe_from_groups_cache` (one `get_json_safely` call instead of two); `test_groups_fetches_from_api` fixture and assertion updated to include `universe`.

## 2026-05-12 — Lowercase column names in long output; cached Group.universe (branch refactor/api-class-integration)

User changes to `api.py`:
- `melt()` now renames `GEO_ID` → `geoidfq` and `NAME` → `name` (lowercase) in the long output; all downstream references updated (`sort_values`, `define_schema`, `DimensionTable`, `DimensionTable.wide`)
- `Group.universe` promoted from `@property` to `@cached_property` to avoid repeated network calls for the same group

Test update: `_make_long()` fixture in `tests/test_api.py` updated to use `'geoidfq'` and `'name'` to match the new output schema.

## 2026-05-12 — Add API key handling to geos.py (branch refactor/api-class-integration)

Three Census API request sites in `geos.py` were passing no API key, causing anonymous requests that are rate-limited. Added `_get_api_key()` (same implementation as in `api.py`, defined locally to avoid a circular import) and wired it in:

- `SumLevel.get_query_req()`: passes `params={'key': k}` when key is set
- `geoinfo_from_params()`: appends `key` to the existing params dict before the request
- `geoids_from_scope()`: copies `sc.params` to avoid mutating the Scope object, then appends `key`

## 2026-05-11 — Rewrite census demo notebook (branch refactor/api-class-integration)

`doc/02-morpc-census-demo.ipynb` rewritten from scratch with a user-focused narrative. Old notebook used the pre-refactor API (`CensusAPI('acs/acs5', 2023, 'B01001', scope='region15')` positional form, `.LONG`/`.DATA`/`.FILENAME` uppercase attrs, `GEO_ID` column, `get_table_groups`/`get_group_variables` standalone functions). New notebook:

- Uses `Endpoint` and `Group` objects explicitly so the discovery/validation workflow is visible
- `CensusAPI(ep, 'region15', group=group)` new keyword-argument signature
- References `api.long`, `api.data`, `api.filename`, `api.name` (lowercase)
- Shows `api.geoidfqs` and `GeoIDFQ` field access
- Demonstrates variables-only mode (`group=None, variables=[...]`)
- Uses `DimensionTable(api.long)` with `GEOIDFQ` column (not `GEO_ID`)
- Single "Network required" note at section header instead of inline on every cell
- 11 sections: Endpoints, Groups, Scopes, Fetching, Long output, GEOIDFQs, Sumlevel, Variables-only, DimensionTable, Time series, Saving
- Added `_VALUE_FIELD_DEFS` module-level dict for schema field definitions.

## 2026-05-12 — Rework DimensionTable (branch refactor/api-class-integration)

`DimensionTable` redesigned around explicit dimension parsing.

**`_parse_dims(dim_names=None)`** replaces `create_description_table()`:
- Splits each `variable_label` by `!!` into subtotals (ending with `:`) and leaves (no `:`)
- Subtotals are left-aligned into the first S columns; leaves into the next L columns (S and L are the max depths across all variables)
- This keeps the same concept in the same column even when paths have different depths — e.g. B05004 where Sex appears as a leaf at depth 2, 3, or 4 depending on the nativity level
- Result stored as `self.dims` (DataFrame indexed by `variable`); `:` suffix preserved for drop() logic, stripped for display in `wide()`

**`drop(dim, method='summarize')`** replaces `droplevels` parameter in `wide()`/`percent()`:
- `method='summarize'`: keep only rows where `dim == ''` (already aggregated over that dimension); returns a new `DimensionTable`
- `method='aggregate'`: sum leaf rows (dim != '') per group; propagate MOE via `sqrt(sum(moe_i²))`; returns a new `DimensionTable`
- Aggregate only uses leaf rows (where dropped dim is non-empty) to avoid double-counting pre-computed subtotal rows

**`remap(variable_map)`** (moved from `__init__`):
- Applies `find_replace_variable_map`, aggregates collapsed rows, fixes MOE via `sqrt(sum(moe²))` (was `.sum()` which is wrong for ACS MOE)
- Returns `self` for chaining; rebuilds `self.dims` after relabeling

**`wide()`**: simplified — no `droplevels` parameter, no MISSING_VALUES list comprehension (uses `df.replace(dict, np.nan)`)

**`percent(decimals=2)`**: identifies total row explicitly via `all(v == '' for v in vals[1:])` instead of fragile `.T.iloc[:, 0]` position assumption

**Removed**: `create_description_table()`, `variable_map`/`variable_order` constructor parameters

Tests: `TestDimensionTableDescriptionTable` replaced by `TestDimensionTableParseDims`, `TestDimensionTableDrop`, `TestDimensionTableRemap` — 23 new tests, all pass. 197 total passing.

## 2026-05-12 — Fix DimensionTable._parse_dims() — inflated dims on cross-vintage concat (branch refactor/api-class-integration)

**Bug**: `DimensionTable(pd.concat([b01001_2018.long, b01001.long])).wide()` produced a 5-level row MultiIndex instead of 3, with `('', '', 'Total', '', '')` as the grand total row instead of `('Total', '', '')`.

**Root cause**: Two related issues in `_parse_dims`:
1. `drop_duplicates()` was called on both `variable` and `variable_label`, so the same variable code (e.g. `B01001_001`) appeared twice in `unique` — once with the 2018 label `'Total'` (no colon) and once with the 2023 label `'Total:'` (colon). Both versions entered the alignment calculation.
2. Older Census API vintages omit the trailing `:` from subtotal segment labels (e.g. `'Total!!Male!!Under 5 years'` instead of `'Total:!!Male:!!Under 5 years'`). With both formats present, S (max subtotal depth) and L (max leaf depth) inflated independently — S=2 from 2023, L=3 from 2018 → n=5.

**Fix**: 
- `drop_duplicates(subset='variable')` — one label per variable code; the first occurrence wins
- Label normalization via tree structure: strip all trailing `:` to form a set of clean label paths, then for each segment, add `:` if (a) the original segment already had `:` (Census convention preserved), or (b) the segment's path prefix has children in the clean label set (older vintages fixed by tree structure). Normalization runs before S/L computation, so both vintages produce the same dimensions.

5 new tests in `TestDimensionTableCrossVintage`. 202 total passing.

## 2026-05-12 — Fix DimensionTable.percent() — values appearing in column headers (branch refactor/api-class-integration)

**Bug**: `percent()` returned a table where dimension values (e.g. `('Total', 'Male')`) appeared as column headers instead of row index values.

**Root cause**: The implementation transposed `wide()`, dropped the total column, called `reset_index()`, then tried to identify metadata columns via `non_value_cols = [c for c in pct.columns if c not in self.variable_type]`. After `reset_index()` on a transposed DataFrame, the columns were MultiIndex tuples like `('Total', 'Male')` and `('Total', 'Female')`. These tuples are not in `variable_type` (`['estimate', 'moe']`), so they were included in `non_value_cols` and became index levels. The subsequent `.T` then put them into column headers.

**Fix**: Replaced the transpose-and-reconstruct approach with a direct operation on the `wide()` output — find the total row by integer position, divide each column individually by its total value, drop the total row, and return. Output has the same structure as `wide()` (dimension values as row index, geographies as column MultiIndex) but with percentage values and no total row.
