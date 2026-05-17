# API Reference

Key classes and functions exported from `morpc_census`.

---

## Census API — `morpc_census.api`

### `Endpoint`

```python
Endpoint(dataset: str, year: int)
```

Represents a Census API survey and vintage year. Lazy-loads available groups and variables on first access.

- `Endpoint('acs/acs5', 2023)` — ACS 5-year estimates, 2023 vintage
- `.groups` — `dict` of all variable groups for this endpoint (cached)
- `.vintages` — list of available vintage years (cached)

---

### `Group`

```python
Group(endpoint: Endpoint, code: str)
```

Wraps a Census variable group (e.g. `'B01001'`).

- `.variables` — `dict` of all variables in the group (cached)

---

### `CensusAPI`

```python
CensusAPI(endpoint, scope, group=None, sumlevel=None, variables=None)
```

Fetches survey data and reshapes it to a long-format DataFrame.

| Attribute | Description |
|-----------|-------------|
| `.data`   | Raw wide DataFrame from the Census API |
| `.long`   | Long-format DataFrame: one row per geography × variable |
| `.vars`   | Per-variable metadata dict |

```python
ep  = Endpoint('acs/acs5', 2023)
grp = Group(ep, 'B01001')
api = CensusAPI(ep, SCOPES['region15'], group=grp, sumlevel=SumLevel('county'))
api.long.head()
```

Save long data + frictionless schema to disk:

```python
api.save('./output')
```

---

### `DimensionTable`

```python
DimensionTable(long: DataFrame)
```

Reshapes a `CensusAPI.long` DataFrame into a MultiIndex wide table.

| Method | Description |
|--------|-------------|
| `.wide()` | Pivot to wide MultiIndex DataFrame |
| `.percent(_wide=None)` | Column percentages; MOE via Census Bureau derived proportion formula |
| `.remap(label_map)` | Collapse variable labels and aggregate estimates |
| `.drop(dim)` | Remove a dimension level by name, integer index, or list |

Column MultiIndex level order: `concept > universe > survey > geoidfq > name > reference_period > value_type`

---

### `RaceDimensionTable`

```python
RaceDimensionTable(long: DataFrame, race_map=None)
```

Subclass of `DimensionTable` for racial-iteration groups (e.g. B17020A–I). Adds an ordered `race` column level; `percent()` computes within-race percentages.

---

## Geography — `morpc_census.geos`

### `SCOPES`

Dict of named geographic extents. Common keys: `'region15'`, `'franklin'`, `'us'`.

```python
from morpc_census import SCOPES
list(SCOPES.keys())
```

---

### `SumLevel`

```python
SumLevel(name_or_code: str)
```

Maps between summary level names and Census API codes.

```python
SumLevel('county')   # → code '050'
SumLevel('050')      # → name 'county'
```

---

### `GeoIDFQ`

Parse and construct Census fully-qualified geographic IDs.

```python
GeoIDFQ.parse('1400000US39049010100')
# → GeoIDFQ(sumlevel='140', variant='00', parts={'state': '39', 'county': '049', 'tract': '010100'})

GeoIDFQ.build('050', {'state': '39', 'county': '049'})
# → GeoIDFQ(...)
str(GeoIDFQ.build('050', {'state': '39', 'county': '049'}))
# → '0500000US39049'
```

---

### `geoinfo_from_scope_sumlevel`

```python
geoinfo_from_scope_sumlevel(scope, sumlevel=None, output='list')
```

Returns GEOIDFQs for all geographies at `sumlevel` within `scope`.

```python
geoinfo_from_scope_sumlevel('region15')                        # list of GEOIDFQ strings
geoinfo_from_scope_sumlevel('franklin', 'tract', output='table')  # DataFrame
```

---

### `fetch_geos_from_scope_sumlevel`

```python
fetch_geos_from_scope_sumlevel(scope, sumlevel=None, year=None, survey='current')
```

Fetches a GeoDataFrame of boundaries from TIGERweb.

```python
geos   = fetch_geos_from_scope_sumlevel('region15')           # county boundaries
tracts = fetch_geos_from_scope_sumlevel('franklin', 'tract')  # tracts in Franklin County
geos.plot()
```
