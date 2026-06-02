"""
Tests for pure/offline functions in morpc_census.api.

Network-dependent functions are tested with mocked dependencies.
"""

import pytest
import pandas as pd
from unittest.mock import patch

from morpc_census.api import (
    censusapi_name,
    find_replace_variable_map,
    DimensionTable,
    RaceDimensionTable,
    Endpoint,
    _get_api_key,
    Group,
    CensusAPI,
    IMPLEMENTED_ENDPOINTS,
    TimeSeries,
    RaceTable,
)
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

def _make_long():
    """Minimal LONG DataFrame suitable for DimensionTable tests."""
    return pd.DataFrame({
        'variable': ['B01_001', 'B01_002', 'B01_003'],
        'variable_label': ['Total:', 'Total:!!Male:', 'Total:!!Female:'],
        'geoidfq': ['0500000US39049'] * 3,
        'name': ['Franklin County, Ohio'] * 3,
        'concept': ['Test concept'] * 3,
        'universe': ['Population'] * 3,
        'survey': ['acs/acs5'] * 3,
        'reference_period': [2023] * 3,
        'estimate': [100, 50, 50],
        'moe': [5, 3, 3],
    })


# ---------------------------------------------------------------------------
# TestCensusapiName
# ---------------------------------------------------------------------------

class TestCensusapiName:
    _fake_endpoints = {'acs/acs5': [2020, 2023], 'dec/pl': [2020]}

    @pytest.fixture(autouse=True)
    def mock_endpoints(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints):
            yield

    def test_no_sumlevel_no_variables(self):
        name = censusapi_name(Endpoint('acs/acs5', 2023), 'franklin', 'B01001')
        assert name == 'census-acs-acs5-2023-franklin-b01001'

    def test_with_sumlevel_tract(self):
        # HIERARCHY_STRING_FROM_CENSUSNAME['tract'] == 'COUNTY-TRACT'
        # 'COUNTY-TRACT'.replace('-', '').lower() == 'countytract'
        name = censusapi_name(Endpoint('acs/acs5', 2023), 'franklin', 'B01001', sumlevel='tract')
        assert name == 'census-acs-acs5-2023-countytract-franklin-b01001'

    def test_with_sumlevel_county(self):
        # HIERARCHY_STRING_FROM_CENSUSNAME['county'] == 'COUNTY'
        name = censusapi_name(Endpoint('acs/acs5', 2023), 'ohio', 'B01001', sumlevel='county')
        assert name == 'census-acs-acs5-2023-county-ohio-b01001'

    def test_with_variables_appends_suffix(self):
        name = censusapi_name(
            Endpoint('acs/acs5', 2023), 'franklin', 'B01001',
            variables=['B01001_001E', 'B01001_002E'],
        )
        assert name.endswith('-select-variables')

    def test_no_variables_no_suffix(self):
        name = censusapi_name(Endpoint('acs/acs5', 2023), 'franklin', 'B01001')
        assert 'select-variables' not in name

    def test_sumlevel_and_variables_combined(self):
        name = censusapi_name(
            Endpoint('acs/acs5', 2023), 'franklin', 'B01001',
            sumlevel='tract',
            variables=['B01001_001E'],
        )
        assert 'countytract' in name
        assert name.endswith('-select-variables')

    def test_result_is_lowercase(self):
        name = censusapi_name(Endpoint('acs/acs5', 2023), 'Franklin', 'B01001')
        assert name == name.lower()

    def test_dec_endpoint(self):
        name = censusapi_name(Endpoint('dec/pl', 2020), 'ohio', 'P1')
        assert name == 'census-dec-pl-2020-ohio-p1'

    def test_accepts_scope_instance(self):
        from morpc_census.geos import Scope
        name = censusapi_name(Endpoint('acs/acs5', 2023), Scope('franklin'), 'B01001')
        assert name == 'census-acs-acs5-2023-franklin-b01001'

    def test_accepts_sumlevel_instance(self):
        from morpc_census.geos import SumLevel
        name = censusapi_name(Endpoint('acs/acs5', 2023), 'ohio', 'B01001', sumlevel=SumLevel('county'))
        assert name == 'census-acs-acs5-2023-county-ohio-b01001'

    def test_scope_instance_matches_string(self):
        from morpc_census.geos import Scope
        ep = Endpoint('acs/acs5', 2023)
        assert (
            censusapi_name(ep, Scope('franklin'), 'B01001')
            == censusapi_name(ep, 'franklin', 'B01001')
        )

    def test_sumlevel_instance_matches_string(self):
        from morpc_census.geos import SumLevel
        ep = Endpoint('acs/acs5', 2023)
        assert (
            censusapi_name(ep, 'ohio', 'B01001', sumlevel=SumLevel('county'))
            == censusapi_name(ep, 'ohio', 'B01001', sumlevel='county')
        )


# ---------------------------------------------------------------------------
# TestFindReplaceVariableMap
# ---------------------------------------------------------------------------

class TestFindReplaceVariableMap:
    def test_basic_replacement(self):
        labels = ['Total!!Male', 'Total!!Female']
        variables = ['B01001_002E', 'B01001_026E']
        new_labels, _ = find_replace_variable_map(labels, variables, {'Male': 'Men', 'Female': 'Women'})
        assert new_labels == ['Total!!Men', 'Total!!Women']

    def test_new_variable_codes_are_sequential(self):
        labels = ['Total!!Male', 'Total!!Female']
        variables = ['B01001_002E', 'B01001_026E']
        _, new_vars = find_replace_variable_map(labels, variables, {'Male': 'Men', 'Female': 'Women'})
        assert new_vars == ['B01001_M00', 'B01001_M01']

    def test_unmatched_label_unchanged(self):
        labels = ['Total:', 'Total:!!Male:']
        variables = ['B01001_001E', 'B01001_002E']
        new_labels, _ = find_replace_variable_map(labels, variables, {'Female': 'Women'})
        assert new_labels == ['Total:', 'Total:!!Male:']

    def test_duplicate_new_labels_share_variable_code(self):
        labels = ['Total!!Male!!Under5', 'Total!!Male!!5to9']
        variables = ['B01001_003E', 'B01001_004E']
        new_labels, new_vars = find_replace_variable_map(
            labels, variables, {'Under5': 'Youth', '5to9': 'Youth'}
        )
        assert new_labels == ['Total!!Male!!Youth', 'Total!!Male!!Youth']
        assert new_vars[0] == new_vars[1]

    def test_var_id_prefix_comes_from_first_variable(self):
        labels = ['A', 'B']
        variables = ['C17002_001E', 'C17002_002E']
        _, new_vars = find_replace_variable_map(labels, variables, {})
        assert all(v.startswith('C17002_M') for v in new_vars)


# ---------------------------------------------------------------------------
# Shared fixture for cross-dimension alignment (B05004-like)
# ---------------------------------------------------------------------------

def _make_long_cross():
    """Long DataFrame with a cross-cutting dimension (nativity × sex).

    Mirrors B05004 structure: sex appears at multiple depths.
    After _parse_dims the sex dimension should always be in the last column.
    """
    rows = [
        # variable,    label,                                       est, moe
        ('B05_001', 'Total:',                                       300, 10),
        ('B05_002', 'Total:!!Male',                                 150,  7),
        ('B05_003', 'Total:!!Female',                               150,  7),
        ('B05_004', 'Total:!!Native:',                              200,  8),
        ('B05_005', 'Total:!!Native:!!Male',                        100,  5),
        ('B05_006', 'Total:!!Native:!!Female',                      100,  5),
        ('B05_007', 'Total:!!Foreign-born:',                        100,  6),
        ('B05_008', 'Total:!!Foreign-born:!!Male',                   50,  4),
        ('B05_009', 'Total:!!Foreign-born:!!Female',                 50,  4),
        ('B05_010', 'Total:!!Foreign-born:!!Naturalized:',           60,  4),
        ('B05_011', 'Total:!!Foreign-born:!!Naturalized:!!Male',     30,  3),
        ('B05_012', 'Total:!!Foreign-born:!!Naturalized:!!Female',   30,  3),
    ]
    variables, labels, estimates, moes = zip(*rows)
    n = len(rows)
    return pd.DataFrame({
        'variable': list(variables),
        'variable_label': list(labels),
        'geoidfq': ['0500000US39049'] * n,
        'name': ['Franklin County, Ohio'] * n,
        'concept': ['Test'] * n,
        'universe': ['Population'] * n,
        'survey': ['acs/acs5'] * n,
        'reference_period': [2023] * n,
        'estimate': list(estimates),
        'moe': list(moes),
    })


# ---------------------------------------------------------------------------
# Shared fixture for cross-vintage label inconsistency
# ---------------------------------------------------------------------------

def _make_long_timeseries():
    """Two years of B01001-like data with age groups.

    The 2018 vintage omits the trailing ':' on subtotal segments; 2018 rows
    appear first so drop_duplicates keeps the colon-free labels.  After
    normalization by tree structure, _parse_dims should produce the same
    dims shape as a single-year table built from the 2023 labels.
    """
    rows_2018 = [
        # variable,  label (no ':'),             est, moe, year
        ('B01_001', 'Total',                     95,  5, 2018),
        ('B01_002', 'Total!!Male',               48,  3, 2018),
        ('B01_003', 'Total!!Male!!Under 5',      20,  2, 2018),
        ('B01_004', 'Total!!Female',             47,  3, 2018),
        ('B01_005', 'Total!!Female!!Under 5',    19,  2, 2018),
    ]
    rows_2023 = [
        ('B01_001', 'Total:',                   100,  5, 2023),
        ('B01_002', 'Total:!!Male:',             50,  3, 2023),
        ('B01_003', 'Total:!!Male:!!Under 5',    21,  2, 2023),
        ('B01_004', 'Total:!!Female:',           50,  3, 2023),
        ('B01_005', 'Total:!!Female:!!Under 5',  20,  2, 2023),
    ]
    rows = rows_2018 + rows_2023
    variables, labels, estimates, moes, years = zip(*rows)
    n = len(rows)
    return pd.DataFrame({
        'variable':        list(variables),
        'variable_label':  list(labels),
        'geoidfq':         ['0500000US39049'] * n,
        'name':            ['Franklin County, Ohio'] * n,
        'concept':         ['Test'] * n,
        'universe':        ['Population'] * n,
        'survey':          ['acs/acs5'] * n,
        'reference_period': list(years),
        'estimate':        list(estimates),
        'moe':             list(moes),
    })


# ---------------------------------------------------------------------------
# TestDimensionTableParseDims
# ---------------------------------------------------------------------------

class TestDimensionTableParseDims:
    def test_dims_is_dataframe(self):
        assert isinstance(DimensionTable(_make_long()).dims, pd.DataFrame)

    def test_dims_indexed_by_variable(self):
        long = _make_long()
        dims = DimensionTable(long).dims
        assert dims.index.name == 'variable'
        assert set(long['variable'].unique()) == set(dims.index)

    def test_dims_row_count_matches_unique_variables(self):
        long = _make_long()
        dims = DimensionTable(long).dims
        assert len(dims) == long['variable'].nunique()

    def test_dims_column_count(self):
        # _make_long: labels have depth 1 (Total) and 2 (Total!!Male/Female) → 2 cols
        dims = DimensionTable(_make_long()).dims
        assert dims.shape[1] == 2

    def test_dims_total_in_first_column(self):
        dims = DimensionTable(_make_long()).dims
        assert dims.iloc[:, 0].eq('Total').all()

    def test_dims_sex_in_second_column(self):
        # Male and Female appear only at depth 1 → second column, not shifted further
        dims = DimensionTable(_make_long()).dims
        assert dims.loc['B01_002', dims.columns[1]] == 'Male'
        assert dims.loc['B01_003', dims.columns[1]] == 'Female'

    def test_dims_leaf_aligned_to_last_column(self):
        # Cross-dim fixture: Male/Female are leaves → always in the last column
        dims = DimensionTable(_make_long_cross()).dims
        last_col = dims.columns[-1]
        sex_rows = ['B05_002', 'B05_003', 'B05_005', 'B05_006',
                    'B05_008', 'B05_009', 'B05_011', 'B05_012']
        assert all(dims.loc[v, last_col] in ('Male', 'Female') for v in sex_rows)

    def test_dims_nativity_not_in_last_column(self):
        # Nativity values (Native, Foreign-born, Naturalized) appear at fixed depths
        # and should NOT be shifted into the last (sex) column.
        dims = DimensionTable(_make_long_cross()).dims
        last_col = dims.columns[-1]
        assert not dims.loc[:, last_col].isin(['Native', 'Foreign-born', 'Naturalized']).any()

    def test_dim_names_parameter_renames_columns(self):
        dims = DimensionTable(_make_long(), dim_names=['total', 'sex']).dims
        assert list(dims.columns) == ['total', 'sex']

    def test_dim_names_partial_renames_remainder(self):
        # 4-column cross fixture; supply only 2 names → rest get dim_2, dim_3
        dims = DimensionTable(_make_long_cross(), dim_names=['total', 'nativity']).dims
        assert dims.columns[0] == 'total'
        assert dims.columns[1] == 'nativity'
        assert dims.columns[2] == 'dim_2'
        assert dims.columns[3] == 'dim_3'

    def test_dims_columns_are_ordered_categoricals(self):
        dims = DimensionTable(_make_long()).dims
        for col in dims.columns:
            assert hasattr(dims[col], 'cat'), f"column {col!r} is not categorical"
            assert dims[col].cat.ordered, f"column {col!r} is not ordered"

    def test_dims_categorical_order_matches_variable_order(self):
        # _make_long returns B01_001 (Total), B01_002 (Male), B01_003 (Female)
        # The second column categories should follow that order.
        dims = DimensionTable(_make_long()).dims
        second_col = dims.iloc[:, 1]
        cats = list(second_col.cat.categories)
        assert cats.index('Male') < cats.index('Female')

    def test_dims_categorical_order_preserved_across_cross_vintage(self):
        # Cross-vintage fixture: 2018 labels lack ':' but produce the same segments.
        # Male and Female should still appear in variable order.
        dims = DimensionTable(_make_long_timeseries()).dims
        second_col = dims.iloc[:, 1]
        cats = [c for c in second_col.cat.categories if c != '']
        assert cats.index('Male') < cats.index('Female')


def _make_long_dec_sex_by_age():
    """Modern decennial (dec/dhc) Sex-by-Age long frame (P12-style codes/labels).

    Modern dec variable codes carry an underscore + ``N`` suffix, so the group
    code (``P12``) is recoverable for the dec_group_dims lookup.
    """
    rows = [
        ('P12_001N', ' !!Total:'),
        ('P12_002N', ' !!Total:!!Male:'),
        ('P12_003N', ' !!Total:!!Male:!!Under 5 years'),
        ('P12_004N', ' !!Total:!!Male:!!5 to 9 years'),
        ('P12_026N', ' !!Total:!!Female:'),
        ('P12_027N', ' !!Total:!!Female:!!Under 5 years'),
        ('P12_028N', ' !!Total:!!Female:!!5 to 9 years'),
    ]
    return pd.DataFrame({
        'variable': [c for c, _ in rows],
        'variable_label': [lbl for _, lbl in rows],
        'geoidfq': ['0500000US39049'] * len(rows),
        'name': ['Franklin County, Ohio'] * len(rows),
        'concept': ['Sex By Age For Selected Age Categories'] * len(rows),
        'universe': ['Total population'] * len(rows),
        'survey': ['dec/dhc'] * len(rows),
        'reference_period': [2020] * len(rows),
        'estimate': [100, 50, 10, 12, 50, 9, 11],
    })


class TestDimensionTableDecNaming:
    """Decennial dim naming resolves via the dec_* dimension files (survey-aware)."""

    def test_modern_dec_resolves_named_columns(self):
        dims = DimensionTable(_make_long_dec_sex_by_age()).dims
        assert list(dims.columns) == ['Total', 'Sex', 'Age']

    def test_dim_family_selects_dec_for_decennial_surveys(self):
        from morpc_census.api import _dim_family
        assert _dim_family('dec/pl') == 'dec'
        assert _dim_family('dec/dhc') == 'dec'
        assert _dim_family('acs/acs5') == 'acs'
        assert _dim_family('acs/acs1') == 'acs'
        assert _dim_family(None) == 'acs'

    def test_acs_unaffected_by_dec_files(self):
        # A real ACS group (B01001) must still resolve via the acs_* files.
        acs = pd.DataFrame({
            'variable': ['B01001_001', 'B01001_002', 'B01001_026'],
            'variable_label': ['Total:', 'Total:!!Male:', 'Total:!!Female:'],
            'geoidfq': ['0500000US39049'] * 3,
            'name': ['Franklin County, Ohio'] * 3,
            'concept': ['Sex by Age'] * 3,
            'universe': ['Total population'] * 3,
            'survey': ['acs/acs5'] * 3,
            'reference_period': [2023] * 3,
            'estimate': [100, 50, 50],
        })
        dims = DimensionTable(acs).dims
        assert list(dims.columns) == ['Total', 'Sex']


# ---------------------------------------------------------------------------
# TestDimensionTableCrossVintage
# ---------------------------------------------------------------------------

class TestDimensionTableCrossVintage:
    """_parse_dims must handle cross-vintage concatenations where older vintages
    omit the trailing ':' from subtotal segments.  Colons are stripped in all
    cases, so both vintages produce the same clean segment values."""

    def test_same_column_count_as_single_vintage(self):
        # Both single-vintage (2023 only) and combined should give the same n
        single = DimensionTable(
            _make_long_timeseries().loc[
                _make_long_timeseries()['reference_period'] == 2023
            ]
        ).dims
        combined = DimensionTable(_make_long_timeseries()).dims
        assert combined.shape[1] == single.shape[1]

    def test_no_duplicate_variable_rows(self):
        # Each variable code must appear exactly once in dims
        dims = DimensionTable(_make_long_timeseries()).dims
        assert dims.index.is_unique

    def test_row_count_equals_unique_variables(self):
        long_ts = _make_long_timeseries()
        dims = DimensionTable(long_ts).dims
        assert len(dims) == long_ts['variable'].nunique()

    def test_cross_vintage_labels_produce_consistent_dims(self):
        # 2018 labels ('Total', 'Total!!Male') and 2023 labels ('Total:', 'Total:!!Male:')
        # both reduce to the same clean segments ('Total', 'Male', 'Female').
        dims = DimensionTable(_make_long_timeseries()).dims
        assert dims.iloc[:, 0].eq('Total').all()
        assert dims.loc['B01_002', dims.columns[1]] == 'Male'
        assert dims.loc['B01_004', dims.columns[1]] == 'Female'

    def test_no_colons_in_dim_values(self):
        # Shift-right strips ':' from all segments; no dim value should contain one.
        dims = DimensionTable(_make_long_timeseries()).dims
        for col in dims.columns:
            assert not dims[col].str.endswith(':').any()


# ---------------------------------------------------------------------------
# TestDimensionTableDrop
# ---------------------------------------------------------------------------

def _make_long_leaves():
    """Long DataFrame with no partial subtotals — pure cross-product of two dims.

    Only the grand-total row (all dims '') exists as a subtotal; no pre-computed
    dim-specific subtotals are present.  Dropping any real dim must therefore
    aggregate rather than filter.
    """
    rows = [
        # variable,    label,                          est, moe
        ('B99_001', 'Total:',                          400, 10),
        ('B99_002', 'Total:!!TypeA:!!ValX',            100,  5),
        ('B99_003', 'Total:!!TypeA:!!ValY',            100,  5),
        ('B99_004', 'Total:!!TypeB:!!ValX',            100,  5),
        ('B99_005', 'Total:!!TypeB:!!ValY',            100,  5),
    ]
    variables, labels, estimates, moes = zip(*rows)
    n = len(rows)
    return pd.DataFrame({
        'variable': list(variables),
        'variable_label': list(labels),
        'geoidfq': ['0500000US39049'] * n,
        'name': ['Franklin County, Ohio'] * n,
        'concept': ['Test'] * n,
        'universe': ['Population'] * n,
        'survey': ['acs/acs5'] * n,
        'reference_period': [2023] * n,
        'estimate': list(estimates),
        'moe': list(moes),
    })


class TestDimensionTableDrop:
    def test_drop_returns_dimension_table(self):
        dt = DimensionTable(_make_long())
        result = dt.drop('dim_1')
        assert isinstance(result, DimensionTable)

    def test_drop_reduces_dim_count_by_one(self):
        dt = DimensionTable(_make_long())
        result = dt.drop('dim_1')
        assert len(result.dims.columns) == len(dt.dims.columns) - 1

    def test_drop_invalid_dim_raises(self):
        dt = DimensionTable(_make_long())
        with pytest.raises(ValueError, match="not in"):
            dt.drop('nonexistent')

    # --- auto-detection: filter path ---

    def test_drop_auto_detects_filter(self):
        # _make_long_cross has pre-computed sex subtotals for each nativity value.
        # Dropping the sex dim should auto-detect the filter path and keep only
        # those rows (where sex == ''), not sum the leaf rows.
        dt = DimensionTable(_make_long_cross())
        sex_col = dt.dims.columns[-1]
        result = dt.drop(sex_col)
        # Leaf sex rows (Male / Female) must be absent from the result
        sex_vals = result.dims[result.dims.columns[-1]] if result.dims.columns.tolist() else None
        assert 'Male' not in dt.long.merge(
            result.long[['variable']], on='variable'
        )['variable_label'].str.cat()
        # The pre-existing nativity subtotals are present
        assert len(result.long) > 0

    def test_drop_filter_keeps_subtotal_rows_only(self):
        # Concrete variable check: after filtering out sex, only rows where
        # sex == '' (B05_001, B05_004, B05_007, B05_010) survive.
        dt = DimensionTable(_make_long_cross())
        sex_col = dt.dims.columns[-1]
        result = dt.drop(sex_col)
        expected_vars = {
            v for v in dt.dims.index if dt.dims.loc[v, sex_col] == ''
        }
        assert set(result.long['variable'].unique()) == expected_vars

    def test_drop_filter_removes_leaf_rows(self):
        dt = DimensionTable(_make_long_cross())
        sex_col = dt.dims.columns[-1]
        result = dt.drop(sex_col)
        remaining_cols = list(result.dims.columns)
        if remaining_cols:
            last = remaining_cols[-1]
            assert not result.dims[last].isin(['Male', 'Female']).any()

    def test_drop_removes_grand_total_row(self):
        # After dropping a dim, any row where ALL remaining dims are '' (grand-
        # total row) must be excluded from the result.
        dt = DimensionTable(_make_long_cross())
        sex_col = dt.dims.columns[-1]
        result = dt.drop(sex_col)
        # The grand-total row (all remaining dims == '') must not be present
        if len(result.dims.columns) > 0:
            all_empty = (result.dims == '').all(axis=1)
            assert not all_empty.any(), "Grand-total row survived the drop"

    # --- auto-detection: aggregate path ---

    def test_drop_auto_detects_aggregate(self):
        # _make_long_leaves has no partial subtotals — only a grand-total row.
        # Dropping a real dim must aggregate the leaf rows.
        dt = DimensionTable(_make_long_leaves())
        type_col = dt.dims.columns[-2]   # 'TypeA'/'TypeB' column
        result = dt.drop(type_col)
        # Result should have two rows (one per Val category) with summed estimates
        assert len(result.dims) == 2

    def test_drop_aggregate_sums_estimates(self):
        # Drop the type dim from the leaves fixture; each Val total = TypeA + TypeB = 200.
        import numpy as np
        dt = DimensionTable(_make_long_leaves())
        type_col = dt.dims.columns[-2]
        result = dt.drop(type_col)
        val_col = result.dims.columns[-1]
        valx_var = result.dims.loc[result.dims[val_col] == 'ValX'].index[0]
        est = result.long.loc[result.long['variable'] == valx_var, 'estimate'].iloc[0]
        assert est == 200  # 100 (TypeA/ValX) + 100 (TypeB/ValX)

    def test_drop_aggregate_propagates_moe(self):
        import numpy as np
        dt = DimensionTable(_make_long_leaves())
        type_col = dt.dims.columns[-2]
        result = dt.drop(type_col)
        val_col = result.dims.columns[-1]
        valx_var = result.dims.loc[result.dims[val_col] == 'ValX'].index[0]
        moe = result.long.loc[result.long['variable'] == valx_var, 'moe'].iloc[0]
        expected = np.sqrt(5**2 + 5**2)
        assert abs(moe - expected) < 1e-9

    # --- integer / list indexing ---

    def test_drop_by_integer_index(self):
        dt = DimensionTable(_make_long())
        first_col = dt.dims.columns[0]
        result_by_name = dt.drop(first_col)
        result_by_int = dt.drop(0)
        assert list(result_by_int.dims.columns) == list(result_by_name.dims.columns)
        assert set(result_by_int.long['variable']) == set(result_by_name.long['variable'])

    def test_drop_by_negative_integer_index(self):
        dt = DimensionTable(_make_long())
        last_col = dt.dims.columns[-1]
        result_by_name = dt.drop(last_col)
        result_by_int = dt.drop(-1)
        assert list(result_by_int.dims.columns) == list(result_by_name.dims.columns)

    def test_drop_integer_out_of_range_raises(self):
        dt = DimensionTable(_make_long())
        with pytest.raises(IndexError, match="out of range"):
            dt.drop(99)

    def test_drop_list_of_strings(self):
        dt = DimensionTable(_make_long_cross())
        cols = list(dt.dims.columns)
        result = dt.drop(cols)
        assert len(result.dims.columns) == 0

    def test_drop_list_of_integers(self):
        dt = DimensionTable(_make_long_cross())
        n = len(dt.dims.columns)
        result = dt.drop(list(range(n)))
        assert len(result.dims.columns) == 0

    def test_drop_list_mixed_string_and_int(self):
        dt = DimensionTable(_make_long_cross())
        cols = list(dt.dims.columns)
        result = dt.drop([cols[0], -1])
        assert len(result.dims.columns) == len(cols) - 2

    def test_drop_list_reduces_dims_sequentially(self):
        dt = DimensionTable(_make_long_cross())
        first_col = dt.dims.columns[0]
        last_col = dt.dims.columns[-1]
        result = dt.drop([first_col, last_col])
        assert first_col not in result.dims.columns
        assert last_col not in result.dims.columns


# ---------------------------------------------------------------------------
# TestDimensionTableRemap
# ---------------------------------------------------------------------------

class TestDimensionTableRemap:
    def test_remap_returns_self(self):
        dt = DimensionTable(_make_long())
        result = dt.remap({'Male:': 'Men:', 'Female:': 'Women:'})
        assert result is dt

    def test_remap_updates_variable_labels(self):
        dt = DimensionTable(_make_long())
        dt.remap({'Male:': 'Men:', 'Female:': 'Women:'})
        assert 'Male:' not in dt.long['variable_label'].values
        assert any('Men:' in v for v in dt.long['variable_label'].values)

    def test_remap_rebuilds_dims(self):
        dt = DimensionTable(_make_long())
        dt.remap({'Male:': 'Men:', 'Female:': 'Women:'})
        assert 'Male:' not in dt.dims.values.flatten()

    def test_remap_aggregates_duplicate_labels(self):
        # Map both Male: and Female: to the same label → two rows collapse into one
        dt = DimensionTable(_make_long())
        dt.remap({'Male:': 'People:', 'Female:': 'People:'})
        # After remapping, only two unique variable_labels: Total: and People:
        assert dt.long['variable_label'].nunique() == 2

    def test_remap_sums_estimates_for_collapsed_rows(self):
        dt = DimensionTable(_make_long())
        dt.remap({'Male:': 'People:', 'Female:': 'People:'})
        people_var = dt.long.loc[
            dt.long['variable_label'].str.contains('People:'), 'variable'
        ].iloc[0]
        est = dt.long.loc[dt.long['variable'] == people_var, 'estimate'].iloc[0]
        assert est == 100  # 50 + 50


# ---------------------------------------------------------------------------
# TestRaceDimensionTable
# ---------------------------------------------------------------------------

def _make_long_racial():
    """Minimal concatenated long DataFrame from two racial iteration groups.

    Simulates B17020A (White Alone) and B17020B (Black or African American
    Alone), each with three variables: total, below-poverty subtotal, and
    one age leaf row.
    """
    groups = [
        ('A', 'POVERTY STATUS BY AGE (WHITE ALONE)',
         'White alone population for whom poverty status is determined'),
        ('B', 'POVERTY STATUS BY AGE (BLACK OR AFRICAN AMERICAN ALONE)',
         'Black or African American alone population for whom poverty status is determined'),
    ]
    rows = []
    for code, concept, universe in groups:
        for num, label, est, moe in [
            ('001', 'Total:',                                    200, 10),
            ('002', 'Total:!!Below poverty level:',               50,  5),
            ('003', 'Total:!!Below poverty level:!!Under 6 years', 20,  4),
        ]:
            rows.append({
                'variable':       f'B17020{code}_{num}',
                'variable_label': label,
                'geoidfq':        '0500000US39049',
                'name':           'Franklin County, Ohio',
                'concept':        concept,
                'universe':       universe,
                'survey':         'acs/acs5',
                'reference_period': 2023,
                'estimate':       est,
                'moe':            moe,
            })
    return pd.DataFrame(rows)


class TestRaceDimensionTable:
    def test_race_column_added(self):
        assert 'race' in RaceDimensionTable(_make_long_racial()).long.columns

    def test_race_values_mapped(self):
        rdt = RaceDimensionTable(_make_long_racial())
        assert set(rdt.long['race']) == {
            'White Alone', 'Black or African American Alone'
        }

    def test_variable_normalized(self):
        rdt = RaceDimensionTable(_make_long_racial())
        assert rdt.long['variable'].str.match(r'^B17020_\d+$').all()

    def test_variable_type_excludes_race(self):
        assert 'race' not in RaceDimensionTable(_make_long_racial()).value_cols

    def test_race_in_wide_column_index(self):
        wide = RaceDimensionTable(_make_long_racial()).wide()
        assert 'race' in wide.columns.names

    def test_wide_has_column_per_race(self):
        wide = RaceDimensionTable(_make_long_racial()).wide()
        races = set(wide.columns.get_level_values('race').unique())
        assert races == {'White Alone', 'Black or African American Alone'}

    def test_wide_race_level_is_ordered_categorical(self):
        wide = RaceDimensionTable(_make_long_racial()).wide()
        level = wide.columns.get_level_values('race')
        assert hasattr(level, 'dtype') and str(level.dtype) == 'category'
        assert level.dtype.ordered

    def test_wide_race_level_order_matches_race_map(self):
        from morpc_census.api import RACE_TABLE_MAP
        wide = RaceDimensionTable(_make_long_racial()).wide()
        level = wide.columns.get_level_values('race')
        # Unique values in column order should follow RACE_TABLE_MAP insertion order
        present_in_order = list(dict.fromkeys(level))
        map_order = [v for v in RACE_TABLE_MAP.values() if v in set(present_in_order)]
        assert present_in_order == map_order

    def test_wide_race_level_order_respects_custom_race_map(self):
        custom_map = {'B': 'Black', 'A': 'White'}  # B before A intentionally
        rdt = RaceDimensionTable(_make_long_racial(), race_map=custom_map)
        wide = rdt.wide()
        present_in_order = list(dict.fromkeys(wide.columns.get_level_values('race')))
        assert present_in_order == ['Black', 'White']

    def test_wide_race_column_data_matches_label(self):
        # Regression: set_levels was relabeling columns without moving data,
        # causing 'White Alone' to contain Black data and vice versa.
        long = pd.DataFrame([
            {'variable': 'B17020A_001', 'variable_label': 'Total:', 'geoidfq': '0500000US39049',
             'name': 'Franklin', 'concept': 'X', 'universe': 'Y', 'survey': 'acs/acs5',
             'reference_period': 2023, 'estimate': 100, 'moe': 5},
            {'variable': 'B17020B_001', 'variable_label': 'Total:', 'geoidfq': '0500000US39049',
             'name': 'Franklin', 'concept': 'X', 'universe': 'Y', 'survey': 'acs/acs5',
             'reference_period': 2023, 'estimate': 999, 'moe': 5},
        ])
        rdt = RaceDimensionTable(long, race_map={'A': 'White Alone', 'B': 'Black Alone'})
        w = rdt.wide()
        race_idx = w.columns.names.index('race')
        for col in w.columns:
            if col[-1] == 'estimate':
                race = col[race_idx]
                val = float(w[col].iloc[0])
                if race == 'White Alone':
                    assert val == 100.0, f"White Alone column contains {val}, expected 100"
                elif race == 'Black Alone':
                    assert val == 999.0, f"Black Alone column contains {val}, expected 999"

    def test_percent_within_each_race(self):
        pct = RaceDimensionTable(_make_long_racial()).percent()
        # Fixture: below-poverty (50) / total (200) = 25% for each race.
        # variable_type is the last level of the column MultiIndex.
        est_cols = [c for c in pct.columns if c[-1] == 'estimate']
        assert len(est_cols) > 0, "No estimate columns found — check column MultiIndex structure"
        for col in est_cols:
            below_row = pct[col][pct[col].notna()].iloc[0]
            assert below_row == 25.0

    def test_percent_moe_uses_derived_proportion_formula(self):
        import numpy as np
        # Simple 3-row table: total (est=100, moe=5), subgroup (est=60, moe=4)
        long = pd.DataFrame({
            'variable':        ['B01_001', 'B01_002'],
            'variable_label':  ['Total:', 'Total:!!Male:'],
            'geoidfq':         ['0500000US39049'] * 2,
            'name':            ['Franklin County'] * 2,
            'concept':         ['Test'] * 2,
            'universe':        ['Pop'] * 2,
            'survey':          ['acs/acs5'] * 2,
            'reference_period': [2023] * 2,
            'estimate':        [100, 60],
            'moe':             [5, 4],
        })
        dt = DimensionTable(long)
        pct = dt.percent()
        moe_cols = [c for c in pct.columns if c[-1] == 'moe']
        assert len(moe_cols) > 0
        male_moe_pct = float(pct[moe_cols[0]].iloc[0])
        # p = 60/100 = 0.6; radicand = 4²  − 0.6² * 5² = 16 − 9 = 7
        expected = round(np.sqrt(7) / 100 * 100, 2)  # = sqrt(7) ≈ 2.65
        assert abs(male_moe_pct - expected) < 0.01

    def test_percent_moe_uses_addition_form_when_radicand_negative(self):
        import numpy as np
        # Choose values where MOE_x² < p² * MOE_T²:
        # est=10, moe_x=1, total_est=100, moe_T=20 → radicand = 1 − (0.1)²*400 = 1−4 = −3
        long = pd.DataFrame({
            'variable':        ['B01_001', 'B01_002'],
            'variable_label':  ['Total:', 'Total:!!Sub:'],
            'geoidfq':         ['0500000US39049'] * 2,
            'name':            ['Franklin County'] * 2,
            'concept':         ['Test'] * 2,
            'universe':        ['Pop'] * 2,
            'survey':          ['acs/acs5'] * 2,
            'reference_period': [2023] * 2,
            'estimate':        [100, 10],
            'moe':             [20, 1],
        })
        dt = DimensionTable(long)
        pct = dt.percent()
        moe_cols = [c for c in pct.columns if c[-1] == 'moe']
        sub_moe_pct = float(pct[moe_cols[0]].iloc[0])
        # p = 0.1; radicand = 1 − 0.01*400 = −3 → use addition form
        expected = round(np.sqrt(1 + 0.01 * 400) / 100 * 100, 2)  # sqrt(5)/100*100
        assert abs(sub_moe_pct - expected) < 0.01

    def test_unknown_race_code_dropped(self):
        long = _make_long_racial()
        extra = long.iloc[:3].copy()
        extra['variable'] = extra['variable'].str.replace('B17020A', 'B17020Z')
        combined = pd.concat([long, extra], ignore_index=True)
        rdt = RaceDimensionTable(combined)
        assert 'Z' not in rdt.long['race'].values
        assert len(rdt.long) == len(long)

    def test_custom_race_map(self):
        custom = {'A': 'White', 'B': 'Black'}
        rdt = RaceDimensionTable(_make_long_racial(), race_map=custom)
        assert set(rdt.long['race']) == {'White', 'Black'}

    def test_concept_normalized(self):
        rdt = RaceDimensionTable(_make_long_racial())
        assert (rdt.long['concept'] == 'POVERTY STATUS BY AGE').all()

    def test_universe_normalized(self):
        rdt = RaceDimensionTable(_make_long_racial())
        assert rdt.long['universe'].str.startswith('Population').all()


# ---------------------------------------------------------------------------
# TestMissingValueSentinels
# ---------------------------------------------------------------------------

class TestMissingValueSentinels:
    """Numeric Census sentinel values must be treated as NaN in wide() and percent()."""

    def _make_sentinel_long(self, moe_total=-555555555):
        """Two-row long DF where the total row has a numeric sentinel MOE."""
        return pd.DataFrame({
            'variable':         ['B01_001', 'B01_002'],
            'variable_label':   ['Total:', 'Total:!!Male:'],
            'geoidfq':          ['0500000US39049'] * 2,
            'name':             ['Franklin County'] * 2,
            'concept':          ['Test'] * 2,
            'universe':         ['Pop'] * 2,
            'survey':           ['acs/acs5'] * 2,
            'reference_period': [2023] * 2,
            'estimate':         [1_000_000, 490_000],
            'moe':              [float(moe_total), 5_200.0],
        })

    def test_numeric_missing_values_constant_populated(self):
        from morpc_census.api import _MISSING_VALUES_NUMERIC
        assert -555555555 in _MISSING_VALUES_NUMERIC
        assert -999999999 in _MISSING_VALUES_NUMERIC
        assert -222222222 in _MISSING_VALUES_NUMERIC

    def test_wide_replaces_numeric_sentinel_moe_with_nan(self):
        import numpy as np
        dt = DimensionTable(self._make_sentinel_long())
        wide = dt.wide()
        moe_cols = [c for c in wide.columns if c[-1] == 'moe']
        total_row = wide.iloc[0]
        total_moe = float(total_row[moe_cols[0]])
        assert np.isnan(total_moe), f"Expected NaN, got {total_moe}"

    def test_percent_moe_reasonable_when_total_moe_is_sentinel(self):
        # When total MOE is a sentinel (→ NaN → treated as 0), the formula
        # falls back to moe_x / T * 100.  For m_x=5200, T=1_000_000: 0.52%.
        # The result must NOT be astronomical (the broken pre-fix value was ~20 000%).
        dt = DimensionTable(self._make_sentinel_long())
        pct = dt.percent()
        moe_cols = [c for c in pct.columns if c[-1] == 'moe']
        assert len(moe_cols) > 0
        pct_moe = float(pct[moe_cols[0]].iloc[0])
        assert pct_moe < 100, f"Percent MOE should be <100%, got {pct_moe}"
        assert pct_moe > 0, f"Percent MOE should be positive, got {pct_moe}"

    def test_percent_estimate_unaffected_by_sentinel_moe(self):
        dt = DimensionTable(self._make_sentinel_long())
        pct = dt.percent()
        est_cols = [c for c in pct.columns if c[-1] == 'estimate']
        pct_est = round(float(pct[est_cols[0]].iloc[0]), 1)
        assert pct_est == 49.0, f"Percent estimate should still be ~49%, got {pct_est}"

    def test_all_numeric_sentinel_codes_replaced(self):
        import numpy as np
        from morpc_census.api import _MISSING_VALUES_NUMERIC
        for sentinel in _MISSING_VALUES_NUMERIC:
            long = self._make_sentinel_long(moe_total=sentinel)
            dt = DimensionTable(long)
            wide = dt.wide()
            moe_cols = [c for c in wide.columns if c[-1] == 'moe']
            total_moe = float(wide.iloc[0][moe_cols[0]])
            assert np.isnan(total_moe), f"Sentinel {sentinel} not replaced with NaN"


# ---------------------------------------------------------------------------
# TestCensusAPIClassNormalization
# ---------------------------------------------------------------------------

class TestCensusAPIClassNormalization:
    """Test that CensusAPI normalizes scope/sumlevel strings to class instances."""

    _fake_vars = {'B01001_001E': {'label': 'Total:'}}
    _fake_groups = {'B01001': {'description': 'Sex by Age', 'variables': ''}}
    _fake_data = pd.DataFrame({'GEO_ID': ['0500000US39049'], 'NAME': ['Franklin County']})

    _fake_endpoints = {'acs/acs5': [2022, 2023]}

    # Raw JSON responses matching the three Census API endpoints the classes call
    _groups_json = {'groups': [{'name': 'B01001', 'description': 'Sex by Age', 'variables': '', 'universe ': 'All people'}]}
    _vars_json = {'variables': {'B01001_001E': {'label': 'Total:'}, 'GEO_ID': {}, 'NAME': {}}}

    def _census_json(self, url, **kwargs):
        if url.endswith('/groups.json'):
            return self._groups_json
        if url.endswith('/groups'):
            return self._groups_json
        if 'groups/B01001.json' in url:
            return self._vars_json
        raise ValueError(f"Unexpected URL in test: {url}")

    def _make(self, scope, sumlevel=None, group='B01001', variables=None):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc.req.get_json_safely', side_effect=self._census_json), \
             patch('morpc_census.geos.geoinfo_from_scope_sumlevel', return_value={'for': 'county:049'}), \
             patch.object(CensusAPI, '_fetch', return_value=self._fake_data):
            ep = Endpoint('acs/acs5', 2023)
            return CensusAPI(ep, scope, group=group, sumlevel=sumlevel, variables=variables, return_long=False)

    def test_scope_string_stored_as_scope_instance(self):
        from morpc_census.geos import Scope
        api = self._make('franklin')
        assert isinstance(api.scope, Scope)

    def test_scope_instance_passed_through(self):
        from morpc_census.geos import Scope
        sc = Scope('franklin')
        api = self._make(sc)
        assert api.scope is sc

    def test_scope_name_is_correct(self):
        api = self._make('franklin')
        assert api.scope.name == 'franklin'

    def test_sumlevel_string_stored_as_sumlevel_instance(self):
        from morpc_census.geos import SumLevel
        api = self._make('franklin', sumlevel='county')
        assert isinstance(api.sumlevel, SumLevel)

    def test_sumlevel_none_stays_none(self):
        api = self._make('franklin')
        assert api.sumlevel is None

    def test_sumlevel_instance_passed_through(self):
        from morpc_census.geos import SumLevel
        sl = SumLevel('county')
        api = self._make('franklin', sumlevel=sl)
        assert api.sumlevel is sl

    def test_sumlevel_name_is_correct(self):
        from morpc_census.geos import SumLevel
        api = self._make('franklin', sumlevel='county')
        assert api.sumlevel.name == 'county'
        assert isinstance(api.sumlevel, SumLevel)

    def test_create_resource_title_uses_sumlevel_plural_and_scope_name(self):
        import frictionless
        api = self._make('franklin', sumlevel='county')
        api.filename = 'test.csv'
        api.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            api.create_resource()
        assert 'franklin' in captured['title']
        assert 'counties' in captured['title']  # SumLevel('county').plural


# ---------------------------------------------------------------------------
# TestCensusAPIGroupOptional
# ---------------------------------------------------------------------------

class TestFetchVariablesBatching:
    """Tests for _fetch_variables batching logic."""

    _fake_endpoints = {'acs/acs5': [2023]}
    _geo = '0500000US39049'
    _name = 'Franklin County'

    def _make_api(self, n_variables):
        """Build a CensusAPI with _fetch stubbed so we can call _fetch_variables directly."""
        variables = [f'B01001_{i:03d}E' for i in range(1, n_variables + 1)]
        stub = pd.DataFrame({'GEO_ID': [self._geo], 'NAME': [self._name]})
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc_census.geos.geoinfo_from_scope_sumlevel', return_value={'for': 'county:049'}), \
             patch.object(CensusAPI, '_fetch', return_value=stub):
            ep = Endpoint('acs/acs5', 2023)
            return CensusAPI(ep, 'franklin', variables=variables, return_long=False)

    def _response(self, variables):
        """Simulate a Census API JSON list-of-lists response including GEO_ID and NAME."""
        return [
            ['GEO_ID', 'NAME'] + list(variables),
            [self._geo, self._name] + ['1'] * len(variables),
        ]

    def test_single_request_when_48_variables(self):
        api = self._make_api(48)
        with patch('morpc.req.get_json_safely', return_value=self._response(api.variables)) as mock:
            result = api._fetch_variables(api.request['url'], {})
        mock.assert_called_once()
        assert 'GEO_ID' in result.columns
        assert set(api.variables).issubset(set(result.columns))

    def test_two_requests_when_49_variables(self):
        api = self._make_api(49)
        responses = [self._response(api.variables[:48]), self._response(api.variables[48:])]
        with patch('morpc.req.get_json_safely', side_effect=responses) as mock:
            result = api._fetch_variables(api.request['url'], {})
        assert mock.call_count == 2
        assert set(api.variables).issubset(set(result.columns))

    def test_three_requests_when_97_variables(self):
        api = self._make_api(97)
        responses = [
            self._response(api.variables[:48]),
            self._response(api.variables[48:96]),
            self._response(api.variables[96:]),
        ]
        with patch('morpc.req.get_json_safely', side_effect=responses) as mock:
            result = api._fetch_variables(api.request['url'], {})
        assert mock.call_count == 3  # ceil(97/48) == 3
        assert set(api.variables).issubset(set(result.columns))

    def test_batched_results_joined_on_geoid(self):
        api = self._make_api(49)
        batch1 = [['GEO_ID', 'NAME'] + api.variables[:48],
                  [self._geo, self._name] + ['A'] * 48]
        batch2 = [['GEO_ID', 'NAME'] + api.variables[48:],
                  [self._geo, self._name] + ['B']]
        with patch('morpc.req.get_json_safely', side_effect=[batch1, batch2]):
            result = api._fetch_variables(api.request['url'], {})
        row = result.loc[0]
        assert row['GEO_ID'] == self._geo
        assert row[api.variables[0]] == 'A'
        assert row[api.variables[48]] == 'B'

    def test_geoid_and_name_included_in_every_batch_request(self):
        api = self._make_api(49)
        responses = [self._response(api.variables[:48]), self._response(api.variables[48:])]
        with patch('morpc.req.get_json_safely', side_effect=responses) as mock:
            api._fetch_variables(api.request['url'], {})
        for call in mock.call_args_list:
            get_param = call.kwargs['params']['get']
            assert get_param.startswith('GEO_ID,NAME,')

    def test_single_row_result(self):
        api = self._make_api(49)
        responses = [self._response(api.variables[:48]), self._response(api.variables[48:])]
        with patch('morpc.req.get_json_safely', side_effect=responses):
            result = api._fetch_variables(api.request['url'], {})
        assert len(result) == 1


class TestCensusAPIGroupOptional:
    """Test CensusAPI behavior when group is None."""

    _fake_endpoints = {'acs/acs5': [2022, 2023]}
    _fake_data = pd.DataFrame({'GEO_ID': ['0500000US39049'], 'NAME': ['Franklin County']})

    def _make_no_group(self, variables):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc_census.geos.geoinfo_from_scope_sumlevel', return_value={'for': 'county:049'}), \
             patch.object(CensusAPI, '_fetch', return_value=self._fake_data):
            ep = Endpoint('acs/acs5', 2023)
            return CensusAPI(ep, 'franklin', variables=variables, return_long=False)

    def test_no_group_no_variables_raises(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints):
            ep = Endpoint('acs/acs5', 2023)
            with pytest.raises(ValueError, match="At least one of 'group' or 'variables'"):
                CensusAPI(ep, 'franklin')

    def test_variables_only_stores_none_group(self):
        api = self._make_no_group(['B01001_001E', 'B01001_002E'])
        assert api.group is None

    def test_variables_only_stores_variables(self):
        api = self._make_no_group(['B01001_001E', 'B01001_002E'])
        assert api.variables == ['B01001_001E', 'B01001_002E']

    def test_variables_uppercased(self):
        api = self._make_no_group(['b01001_001e'])
        assert api.variables == ['B01001_001E']

    def test_vars_fetches_labels_from_group_endpoint_when_no_group(self):
        fake_group_vars = {
            'variables': {
                'B01001_001E': {'label': 'Estimate!!Total:', 'concept': 'SEX BY AGE'},
                'B01001_002E': {'label': 'Estimate!!Total:!!Male:', 'concept': 'SEX BY AGE'},
            }
        }
        api = self._make_no_group(['B01001_001E'])
        with patch('morpc.req.get_json_safely', return_value=fake_group_vars):
            assert api.vars['B01001_001E']['label'] == 'Estimate!!Total:'

    def test_vars_falls_back_to_empty_dict_on_fetch_error(self):
        api = self._make_no_group(['B01001_001E'])
        with patch('morpc.req.get_json_safely', side_effect=Exception('network error')):
            assert api.vars == {'B01001_001E': {}}

    def test_universe_returns_fallback_string_when_no_group(self):
        api = self._make_no_group(['B01001_001E'])
        assert 'no group' in api.universe

    def test_name_has_no_group_part_when_no_group(self):
        api = self._make_no_group(['B01001_001E'])
        assert 'none' not in api.name
        assert 'b01001' not in api.name

    def test_build_request_uses_variable_list_when_no_group(self):
        api = self._make_no_group(['B01001_001E', 'B01001_002E'])
        assert api.request['params']['get'] == 'B01001_001E,B01001_002E'

    # ------------------------------------------------------------------
    # melt() concept and universe in variables-only mode
    # ------------------------------------------------------------------

    _fake_raw = pd.DataFrame({
        'GEO_ID':       ['0500000US39049'],
        'NAME':         ['Franklin County'],
        'B01001_001E':  ['1000'],
        'B01001_001M':  ['50'],
    })
    _fake_vars = {
        'B01001_001E': {'label': 'Estimate!!Total:', 'concept': 'SEX BY AGE'},
        'B01001_001M': {'label': 'Margin of Error!!Total:', 'concept': 'SEX BY AGE'},
    }
    _fake_groups = {
        'B01001': {'description': 'Sex by Age', 'variables': '', 'universe': 'Total population'},
    }

    def _make_no_group_for_melt(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc_census.geos.geoinfo_from_scope_sumlevel', return_value={'for': 'county:049'}), \
             patch.object(CensusAPI, '_fetch', return_value=self._fake_raw):
            ep = Endpoint('acs/acs5', 2023)
            api = CensusAPI(ep, 'franklin', variables=['B01001_001E', 'B01001_001M'], return_long=False)
        api.__dict__['vars'] = self._fake_vars
        api.endpoint.__dict__['groups'] = self._fake_groups
        return api

    def test_melt_concept_populated_from_vars_in_variables_only_mode(self):
        api = self._make_no_group_for_melt()
        long = api.melt()
        assert (long['concept'] == 'Sex by age').all()

    def test_melt_universe_populated_from_endpoint_groups_in_variables_only_mode(self):
        api = self._make_no_group_for_melt()
        long = api.melt()
        assert (long['universe'] == 'Total population').all()

    def test_melt_concept_empty_string_when_var_has_no_concept(self):
        api = self._make_no_group_for_melt()
        api.__dict__['vars'] = {'B01001_001E': {}, 'B01001_001M': {}}
        long = api.melt()
        assert (long['concept'] == '').all()

    def test_melt_universe_empty_string_when_group_not_in_endpoint_groups(self):
        api = self._make_no_group_for_melt()
        api.endpoint.__dict__['groups'] = {}
        long = api.melt()
        assert (long['universe'] == '').all()


# ---------------------------------------------------------------------------
# TestGetApiKey
# ---------------------------------------------------------------------------

class TestGetApiKey:
    def setup_method(self):
        _get_api_key.cache_clear()

    def test_returns_key_from_environment(self):
        with patch.dict('os.environ', {'CENSUS_API_KEY': 'testkey123'}), \
             patch('dotenv.load_dotenv'), patch('dotenv.find_dotenv', return_value=''):
            assert _get_api_key() == 'testkey123'

    def test_returns_none_when_not_set(self):
        env = {k: v for k, v in __import__('os').environ.items() if k != 'CENSUS_API_KEY'}
        with patch.dict('os.environ', env, clear=True), \
             patch('dotenv.load_dotenv'), patch('dotenv.find_dotenv', return_value=''):
            assert _get_api_key() is None

    def test_dotenv_called_with_override_false(self):
        """dotenv convention: environment variables take precedence over .env values."""
        with patch.dict('os.environ', {}, clear=True), \
             patch('dotenv.load_dotenv') as mock_ld, \
             patch('dotenv.find_dotenv', return_value='/fake/.env'):
            _get_api_key()
        _, kwargs = mock_ld.call_args
        assert kwargs.get('override') is False

    def test_find_dotenv_called_with_usecwd(self):
        with patch.dict('os.environ', {}, clear=True), \
             patch('dotenv.load_dotenv'), \
             patch('dotenv.find_dotenv', return_value='') as mock_fd:
            _get_api_key()
        mock_fd.assert_called_once_with(usecwd=True)


# ---------------------------------------------------------------------------
# TestEndpoint
# ---------------------------------------------------------------------------

class TestEndpoint:
    _fake_endpoints = {'acs/acs5': [2022, 2023], 'dec/pl': [2020]}

    def _make_endpoint(self, survey='acs/acs5', year=2023):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints):
            return Endpoint(survey, year)

    # Survey validation
    def test_invalid_survey_raises_value_error(self):
        with pytest.raises(ValueError, match="not available or not yet implemented"):
            Endpoint('acs/acs99', 2023)

    def test_raises_for_empty_survey(self):
        with pytest.raises(ValueError):
            Endpoint('', 2023)

    def test_raises_for_partial_survey(self):
        with pytest.raises(ValueError):
            Endpoint('acs', 2023)

    # Year validation
    def test_year_stored_as_int(self):
        ep = self._make_endpoint(year='2023')
        assert ep.year == 2023
        assert isinstance(ep.year, int)

    def test_invalid_year_raises_value_error(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints):
            with pytest.raises(ValueError, match="not an available vintage"):
                Endpoint('acs/acs5', 2019)

    # Repr / equality / hash
    def test_repr(self):
        ep = self._make_endpoint()
        assert repr(ep) == "Endpoint('acs/acs5', 2023)"

    def test_equality(self):
        assert self._make_endpoint() == self._make_endpoint()
        assert self._make_endpoint() != self._make_endpoint('dec/pl', 2020)

    def test_hashable(self):
        ep1 = self._make_endpoint()
        ep2 = self._make_endpoint()
        assert hash(ep1) == hash(ep2)
        assert len({ep1, ep2}) == 1

    # Properties
    def test_url_property(self):
        ep = self._make_endpoint()
        assert ep.url == 'https://api.census.gov/data/2023/acs/acs5?'

    def test_vintages_property(self):
        ep = self._make_endpoint()
        assert ep.vintages == [2022, 2023]

    # Network
    def test_groups_fetches_from_api(self):
        raw = {'groups': [{'name': 'B01001', 'description': 'Sex by Age', 'variables': '', 'universe ': 'All people'}]}
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc.req.get_json_safely', return_value=raw):
            ep = Endpoint('acs/acs5', 2023)
            groups = ep.groups
        assert groups == {'B01001': {'description': 'Sex by Age', 'variables': '', 'universe': 'All people'}}


# ---------------------------------------------------------------------------
# TestGroup
# ---------------------------------------------------------------------------

class TestGroup:
    _fake_endpoints = {'acs/acs5': [2022, 2023]}
    _groups_json = {'groups': [{'name': 'B01001', 'description': 'Sex by Age', 'variables': '', 'universe ': 'All people'}]}
    _vars_json = {'variables': {'B01001_001E': {'label': 'Total:'}, 'GEO_ID': {}, 'NAME': {}}}

    def _make_group(self, code='B01001'):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc.req.get_json_safely', return_value=self._groups_json):
            ep = Endpoint('acs/acs5', 2023)
            return Group(ep, code)

    def test_code_uppercased(self):
        g = self._make_group('b01001')
        assert g.code == 'B01001'

    def test_invalid_code_raises_value_error(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc.req.get_json_safely', return_value=self._groups_json):
            ep = Endpoint('acs/acs5', 2023)
            with pytest.raises(ValueError, match="not a valid group"):
                Group(ep, 'BOGUS')

    def test_non_endpoint_raises_type_error(self):
        with pytest.raises(TypeError, match="endpoint must be an Endpoint instance"):
            Group('not-an-endpoint', 'B01001')

    def test_description_from_endpoint_groups(self):
        g = self._make_group()
        assert g.description == 'Sex by Age'

    def test_repr(self):
        g = self._make_group()
        assert repr(g) == "Group('acs/acs5', 2023, 'B01001')"

    def test_equality(self):
        g1 = self._make_group()
        g2 = self._make_group()
        assert g1 == g2

    def test_hashable(self):
        g1 = self._make_group()
        g2 = self._make_group()
        assert hash(g1) == hash(g2)

    def test_variables_fetches_from_api(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc.req.get_json_safely', side_effect=[self._groups_json, self._vars_json]):
            ep = Endpoint('acs/acs5', 2023)
            g = Group(ep, 'B01001')
            variables = g.variables
        assert variables == {'B01001_001E': {'label': 'Total:'}}
        assert 'GEO_ID' not in variables
        assert 'NAME' not in variables

    def test_universe_from_groups_cache(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=self._fake_endpoints), \
             patch('morpc.req.get_json_safely', return_value=self._groups_json):
            ep = Endpoint('acs/acs5', 2023)
            g = Group(ep, 'B01001')
            universe = g.universe
        assert universe == 'All people'


class TestNormalizeUniverse:
    """Tests for _normalize_universe — the universe code/prefix resolution helper."""

    def test_known_code_mapped(self):
        from morpc_census.api import _normalize_universe
        assert _normalize_universe('TOTAL_POP', 'P1') == 'Total population'

    def test_housing_unit_code_mapped(self):
        from morpc_census.api import _normalize_universe
        assert _normalize_universe('HOUSING_UNIT', 'H1') == 'Housing units'

    def test_18_over_code_mapped(self):
        from morpc_census.api import _normalize_universe
        assert _normalize_universe('TOTAL_POP_18_OVER', 'P3') == 'Population 18 years and over'

    def test_human_readable_string_passthrough(self):
        from morpc_census.api import _normalize_universe
        assert _normalize_universe('Occupied housing units', 'H10') == 'Occupied housing units'

    def test_empty_P_prefix_infers_total_population(self):
        from morpc_census.api import _normalize_universe
        assert _normalize_universe('', 'P1') == 'Total population'
        assert _normalize_universe('', 'PL001') == 'Total population'
        assert _normalize_universe('', 'PCT001') == 'Total population'

    def test_empty_H_prefix_infers_housing_units(self):
        from morpc_census.api import _normalize_universe
        assert _normalize_universe('', 'H1') == 'Housing units'
        assert _normalize_universe('', 'HCT001') == 'Housing units'

    def test_empty_unknown_prefix_falls_back_to_total_population(self):
        from morpc_census.api import _normalize_universe
        assert _normalize_universe('', 'UNKNOWN001') == 'Total population'

    def test_group_universe_with_raw_code(self):
        """Group.universe normalises raw API codes to human-readable text."""
        fake_endpoints = {'dec/pl': [2020]}
        fake_groups = {'groups': [
            {'name': 'P1', 'description': 'RACE', 'variables': '', 'universe': 'TOTAL_POP'},
            {'name': 'H1', 'description': 'OCCUPANCY STATUS', 'variables': '', 'universe': 'HOUSING_UNIT'},
        ]}
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=fake_endpoints), \
             patch('morpc.req.get_json_safely', return_value=fake_groups):
            ep = Endpoint('dec/pl', 2020)
            assert Group(ep, 'P1').universe == 'Total population'
            assert Group(ep, 'H1').universe == 'Housing units'

    def test_group_universe_with_empty_string(self):
        """Group.universe infers from group code prefix when API returns empty string."""
        fake_endpoints = {'dec/pl': [2000]}
        fake_groups = {'groups': [
            {'name': 'PL001', 'description': 'RACE', 'variables': '', 'universe': ''},
            {'name': 'PL002', 'description': 'HISPANIC', 'variables': '', 'universe': ''},
        ]}
        with patch('morpc_census.api.get_all_avail_endpoints', return_value=fake_endpoints), \
             patch('morpc.req.get_json_safely', return_value=fake_groups):
            ep = Endpoint('dec/pl', 2000)
            assert Group(ep, 'PL001').universe == 'Total population'
            assert Group(ep, 'PL002').universe == 'Total population'


# ---------------------------------------------------------------------------
# Shared helpers for TimeSeries and RaceTable tests
# ---------------------------------------------------------------------------

def _make_long_for_year(year):
    """_make_long() with reference_period set to year."""
    df = _make_long()
    df['reference_period'] = year
    return df


def _make_fake_census_call(long_df, year, survey='acs/acs5', scope='franklin', group_code=None):
    """Build a CensusAPI-like object without hitting the network."""
    from morpc_census.geos import Scope
    api = CensusAPI.__new__(CensusAPI)
    api.long = long_df.copy()
    api.scope = Scope(scope)
    api.sumlevel = None
    api.group = None
    api.variables = None
    ep = Endpoint.__new__(Endpoint)
    ep.survey = survey
    ep.year = year
    api.endpoint = ep
    api.request = {
        'url': f'https://api.census.gov/data/{year}/{survey}?',
        'params': {'get': f'group({group_code or "B01001"})'},
    }
    return api


def _make_timeseries(years=(2022, 2023)):
    import logging
    from morpc_census.api import Group
    ts = TimeSeries.__new__(TimeSeries)
    ts.survey = 'acs/acs5'
    ts.years = sorted(years)
    ts.logger = logging.getLogger('morpc_census.api.TimeSeries')
    calls = {y: _make_fake_census_call(_make_long_for_year(y), y) for y in ts.years}
    ts.calls = calls
    first = next(iter(calls.values()))
    ts.scope = first.scope
    ts.sumlevel = None
    ts.variables = None
    # Attach a minimal fake Group so create_resource() can access .code and .description
    grp = Group.__new__(Group)
    grp.code = 'B01001'
    grp.__dict__['description'] = 'Sex by Age'
    ep_grp = Endpoint.__new__(Endpoint)
    ep_grp.survey = 'acs/acs5'
    ep_grp.year = ts.years[0]
    grp.endpoint = ep_grp
    ts.group = grp
    ts.long = pd.concat([c.long for c in calls.values()], ignore_index=True)
    return ts


def _make_race_long_for_code(code):
    """Build a long DataFrame whose variable codes match racial iteration format B17020{code}_NNN."""
    from morpc_census.api import RACE_TABLE_MAP
    race_label = RACE_TABLE_MAP.get(code, code)
    rows = []
    for num, label, est, moe in [
        ('001', 'Total:',                                     200, 10),
        ('002', 'Total:!!Below poverty level:',                50,  5),
        ('003', 'Total:!!Below poverty level:!!Under 6 years', 20,  4),
    ]:
        rows.append({
            'variable':       f'B17020{code}_{num}',
            'variable_label': label,
            'geoidfq':        '0500000US39049',
            'name':           'Franklin County, Ohio',
            'concept':        f'POVERTY STATUS BY AGE ({race_label.upper()})',
            'universe':       f'{race_label} population for whom poverty status is determined',
            'survey':         'acs/acs5',
            'reference_period': 2023,
            'estimate':       est,
            'moe':            moe,
        })
    return pd.DataFrame(rows)


def _make_race_table(race_codes=('A', 'B')):
    import logging
    from morpc_census.geos import Scope
    from morpc_census.api import Group, RACE_TABLE_MAP
    rt = RaceTable.__new__(RaceTable)
    rt.survey = 'acs/acs5'
    rt.logger = logging.getLogger('morpc_census.api.RaceTable')
    rt.base_code = 'B17020'
    ep = Endpoint.__new__(Endpoint)
    ep.survey = 'acs/acs5'
    ep.year = 2023
    rt.endpoint = ep
    rt.scope = Scope('franklin')
    rt.sumlevel = None
    calls = {}
    for code in race_codes:
        long_df = _make_race_long_for_code(code)
        call = _make_fake_census_call(long_df, 2023, group_code=f'B17020{code}')
        grp = Group.__new__(Group)
        grp.code = f'B17020{code}'
        race_label = RACE_TABLE_MAP.get(code, code)
        grp.__dict__['description'] = f'POVERTY STATUS BY AGE ({race_label.upper()})'
        ep2 = Endpoint.__new__(Endpoint)
        ep2.survey = 'acs/acs5'
        ep2.year = 2023
        grp.endpoint = ep2
        call.group = grp
        call.request = {
            'url': f'https://api.census.gov/data/2023/acs/acs5?',
            'params': {'get': f'group(B17020{code})'},
        }
        calls[code] = call
    rt.calls = calls
    rt.long = pd.concat([c.long for c in calls.values()], ignore_index=True)
    return rt


# Shared fake data / JSON for full-constructor integration tests
_ts_fake_data = pd.DataFrame({
    'GEO_ID':      ['0500000US39049'],
    'NAME':        ['Franklin County'],
    'B01001_001E': ['100'],
    'B01001_001M': ['5'],
})

_ts_groups_json = {
    'groups': [
        {'name': 'B01001', 'description': 'Sex by Age', 'variables': '', 'universe ': 'All people'}
    ]
}
_ts_vars_json = {
    'variables': {
        'B01001_001E': {'label': 'Estimate!!Total:'},
        'B01001_001M': {'label': 'Margin of Error!!Total:'},
        'GEO_ID': {},
        'NAME': {},
    }
}


def _ts_census_json(url, **kwargs):
    if url.endswith('/groups.json'):
        return _ts_groups_json
    if '/groups/B01001.json' in url:
        return _ts_vars_json
    raise ValueError(f"Unexpected URL in TimeSeries test: {url}")


def _make_timeseries_via_init(years=(2022, 2023)):
    with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2022, 2023]}), \
         patch('morpc.req.get_json_safely', side_effect=_ts_census_json), \
         patch('morpc_census.geos.geoinfo_from_scope_sumlevel', return_value={'for': 'county:049'}), \
         patch.object(CensusAPI, '_fetch', return_value=_ts_fake_data):
        return TimeSeries('acs/acs5', list(years), 'franklin', group='B01001')


_rt_fake_groups = {
    'B17020A': {
        'description': 'POVERTY STATUS BY AGE (WHITE ALONE)',
        'variables': '',
        'universe ': 'White alone population for whom poverty status is determined',
    },
    'B17020B': {
        'description': 'POVERTY STATUS BY AGE (BLACK OR AFRICAN AMERICAN ALONE)',
        'variables': '',
        'universe ': 'Black or African American alone population for whom poverty status is determined',
    },
}

_rt_fake_data = pd.DataFrame({
    'GEO_ID':       ['0500000US39049'],
    'NAME':         ['Franklin County'],
    'B17020A_001E': ['200'],
    'B17020A_001M': ['10'],
})

_rt_vars_json = {
    'variables': {
        'B17020A_001E': {'label': 'Estimate!!Total:'},
        'B17020A_001M': {'label': 'Margin of Error!!Total:'},
        'GEO_ID': {},
        'NAME': {},
    }
}


def _rt_race_json(url, **kwargs):
    if url.endswith('/groups.json'):
        return {
            'groups': [
                {'name': k, 'description': v['description'], 'variables': v['variables'],
                 'universe ': v['universe ']}
                for k, v in _rt_fake_groups.items()
            ]
        }
    if '/groups/B17020' in url:
        return _rt_vars_json
    raise ValueError(f"Unexpected URL in RaceTable test: {url}")


def _make_race_table_via_init(race_codes=None):
    with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}), \
         patch('morpc.req.get_json_safely', side_effect=_rt_race_json), \
         patch('morpc_census.geos.geoinfo_from_scope_sumlevel', return_value={'for': 'county:049'}), \
         patch.object(CensusAPI, '_fetch', return_value=_rt_fake_data):
        ep = Endpoint('acs/acs5', 2023)
        ep.__dict__['groups'] = _rt_fake_groups
        return RaceTable(ep, 'franklin', 'B17020', race_codes=race_codes)


# ---------------------------------------------------------------------------
# TestTimeSeries
# ---------------------------------------------------------------------------

class TestTimeSeries:

    # ------------------------------------------------------------------
    # Constructor tests (via _make_timeseries_via_init)
    # ------------------------------------------------------------------

    def test_calls_has_one_entry_per_year(self):
        ts = _make_timeseries_via_init(years=(2022, 2023))
        assert len(ts.calls) == 2

    def test_calls_keyed_by_integer_year(self):
        ts = _make_timeseries_via_init(years=(2022, 2023))
        assert all(isinstance(k, int) for k in ts.calls.keys())

    def test_years_are_sorted(self):
        ts = _make_timeseries_via_init(years=(2023, 2022))
        assert ts.years == [2022, 2023]

    def test_long_has_both_reference_periods(self):
        ts = _make_timeseries_via_init(years=(2022, 2023))
        assert set(ts.long['reference_period']) == {2022, 2023}

    def test_long_row_count_equals_sum_of_per_year_rows(self):
        ts = _make_timeseries_via_init(years=(2022, 2023))
        total = sum(call.long.shape[0] for call in ts.calls.values())
        assert len(ts.long) == total

    # ------------------------------------------------------------------
    # Name tests (via _make_timeseries)
    # ------------------------------------------------------------------

    def test_name_contains_year_range(self):
        ts = _make_timeseries()
        assert '2022-2023' in ts.name

    def test_name_single_year(self):
        ts = _make_timeseries(years=(2023,))
        # Single-year name should not have a year range segment like '2023-2023'
        assert '2023-2023' not in ts.name
        # And the year should appear exactly once
        assert ts.name.count('2023') == 1

    def test_name_is_lowercase(self):
        ts = _make_timeseries()
        assert ts.name == ts.name.lower()

    # ------------------------------------------------------------------
    # create_resource() tests (via _make_timeseries)
    # ------------------------------------------------------------------

    def test_create_resource_title_has_year_range(self):
        import frictionless
        ts = _make_timeseries()
        ts.filename = 'test.long.csv'
        ts.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            ts.create_resource()
        assert '–2023' in captured.get('title', '') or '2022–2023' in captured.get('title', '')

    def test_create_resource_sources_count(self):
        import frictionless
        ts = _make_timeseries()
        ts.filename = 'test.long.csv'
        ts.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            ts.create_resource()
        assert len(captured.get('sources', [])) == len(ts.years)

    def test_create_resource_source_urls_contain_year(self):
        import frictionless
        ts = _make_timeseries()
        ts.filename = 'test.long.csv'
        ts.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            ts.create_resource()
        sources = captured.get('sources', [])
        for year, source in zip(ts.years, sources):
            assert str(year) in source['path']

    def test_create_resource_source_titles_mention_year(self):
        import frictionless
        ts = _make_timeseries()
        ts.filename = 'test.long.csv'
        ts.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            ts.create_resource()
        sources = captured.get('sources', [])
        for year, source in zip(ts.years, sources):
            assert str(year) in source['title']

    # ------------------------------------------------------------------
    # define_schema() tests (via _make_timeseries)
    # ------------------------------------------------------------------

    def test_define_schema_primary_key_includes_reference_period(self):
        ts = _make_timeseries()
        schema = ts.define_schema()
        assert 'reference_period' in schema.primary_key

    def test_define_schema_has_estimate_field(self):
        ts = _make_timeseries()
        schema = ts.define_schema()
        assert any(f.name == 'estimate' for f in schema.fields)

    # ------------------------------------------------------------------
    # dimension_table() test
    # ------------------------------------------------------------------

    def test_dimension_table_returns_dimension_table(self):
        ts = _make_timeseries()
        assert isinstance(ts.dimension_table(), DimensionTable)

    # ------------------------------------------------------------------
    # save() tests (via _make_timeseries, uses tmp_path fixture)
    # ------------------------------------------------------------------

    def test_save_writes_long_csv(self, tmp_path):
        ts = _make_timeseries()
        fake_result = MagicMock()
        fake_result.valid = True
        with patch('frictionless.Resource.validate', return_value=fake_result):
            ts.save(tmp_path)
        assert (tmp_path / f'{ts.name}.long.csv').exists()

    def test_save_writes_schema_yaml(self, tmp_path):
        ts = _make_timeseries()
        fake_result = MagicMock()
        fake_result.valid = True
        with patch('frictionless.Resource.validate', return_value=fake_result):
            ts.save(tmp_path)
        assert (tmp_path / f'{ts.name}.schema.yaml').exists()

    def test_save_writes_resource_yaml(self, tmp_path):
        ts = _make_timeseries()
        fake_result = MagicMock()
        fake_result.valid = True
        with patch('frictionless.Resource.validate', return_value=fake_result):
            ts.save(tmp_path)
        assert (tmp_path / f'{ts.name}.resource.yaml').exists()


# ---------------------------------------------------------------------------
# TestRaceTable
# ---------------------------------------------------------------------------

class TestRaceTable:

    # ------------------------------------------------------------------
    # Constructor tests (via _make_race_table_via_init)
    # ------------------------------------------------------------------

    def test_base_code_uppercased(self):
        # Use __new__ + direct attribute assignment to verify uppercasing logic
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}), \
             patch('morpc.req.get_json_safely', side_effect=_rt_race_json), \
             patch('morpc_census.geos.geoinfo_from_scope_sumlevel', return_value={'for': 'county:049'}), \
             patch.object(CensusAPI, '_fetch', return_value=_rt_fake_data):
            ep = Endpoint('acs/acs5', 2023)
            ep.__dict__['groups'] = _rt_fake_groups
            rt = RaceTable(ep, 'franklin', 'b17020')
        assert rt.base_code == 'B17020'

    def test_calls_keyed_by_race_letter(self):
        rt = _make_race_table_via_init()
        assert set(rt.calls.keys()) == {'A', 'B'}
        assert all(isinstance(k, str) and len(k) == 1 for k in rt.calls.keys())

    def test_long_has_rows_from_each_race(self):
        rt = _make_race_table_via_init()
        total = sum(call.long.shape[0] for call in rt.calls.values())
        assert len(rt.long) == total

    def test_nonexistent_codes_silently_skipped(self):
        rt = _make_race_table_via_init(race_codes=['A', 'Z'])
        assert list(rt.calls.keys()) == ['A']

    def test_no_valid_codes_raises_value_error(self):
        ep = Endpoint.__new__(Endpoint)
        ep.survey = 'acs/acs5'
        ep.year = 2023
        ep.__dict__['groups'] = {}
        with pytest.raises(ValueError, match="No racial iteration groups found"):
            RaceTable(ep, 'franklin', 'B17020', race_codes=['Z'])

    # ------------------------------------------------------------------
    # Name tests (via _make_race_table)
    # ------------------------------------------------------------------

    def test_name_ends_with_race(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table()
        assert rt.name.endswith('-race')

    def test_name_contains_base_code(self):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table()
        assert 'b17020' in rt.name

    # ------------------------------------------------------------------
    # create_resource() tests (via _make_race_table)
    # ------------------------------------------------------------------

    def test_create_resource_title_contains_by_race(self):
        import frictionless
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table()
        rt.filename = 'test.long.csv'
        rt.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            rt.create_resource()
        assert 'by race' in captured.get('title', '').lower()

    def test_create_resource_sources_count(self):
        import frictionless
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table()
        rt.filename = 'test.long.csv'
        rt.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            rt.create_resource()
        assert len(captured.get('sources', [])) == len(rt.calls)

    def test_create_resource_source_titles_include_race_label(self):
        import frictionless
        from morpc_census.api import RACE_TABLE_MAP
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table(race_codes=('A',))
        rt.filename = 'test.long.csv'
        rt.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            rt.create_resource()
        sources = captured.get('sources', [])
        assert len(sources) == 1
        assert RACE_TABLE_MAP['A'] in sources[0]['title']

    def test_create_resource_source_titles_include_race_code(self):
        import frictionless
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table(race_codes=('A',))
        rt.filename = 'test.long.csv'
        rt.schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(frictionless.Resource, 'from_descriptor', side_effect=lambda d: captured.update(d)):
            rt.create_resource()
        sources = captured.get('sources', [])
        assert len(sources) == 1
        assert 'B17020A' in sources[0]['title']

    # ------------------------------------------------------------------
    # define_schema() tests (via _make_race_table)
    # ------------------------------------------------------------------

    def test_define_schema_primary_key_includes_reference_period(self):
        rt = _make_race_table()
        schema = rt.define_schema()
        assert 'reference_period' in schema.primary_key

    def test_define_schema_has_estimate_field(self):
        rt = _make_race_table()
        schema = rt.define_schema()
        assert any(f.name == 'estimate' for f in schema.fields)

    # ------------------------------------------------------------------
    # dimension_table() test
    # ------------------------------------------------------------------

    def test_dimension_table_returns_race_dimension_table(self):
        rt = _make_race_table()
        assert isinstance(rt.dimension_table(), RaceDimensionTable)

    # ------------------------------------------------------------------
    # save() tests (via _make_race_table, uses tmp_path fixture)
    # ------------------------------------------------------------------

    def test_save_writes_long_csv(self, tmp_path):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table()
        fake_result = MagicMock()
        fake_result.valid = True
        with patch('frictionless.Resource.validate', return_value=fake_result):
            rt.save(tmp_path)
        assert (tmp_path / f'{rt.name}.long.csv').exists()

    def test_save_writes_schema_yaml(self, tmp_path):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table()
        fake_result = MagicMock()
        fake_result.valid = True
        with patch('frictionless.Resource.validate', return_value=fake_result):
            rt.save(tmp_path)
        assert (tmp_path / f'{rt.name}.schema.yaml').exists()

    def test_save_writes_resource_yaml(self, tmp_path):
        with patch('morpc_census.api.get_all_avail_endpoints', return_value={'acs/acs5': [2023]}):
            rt = _make_race_table()
        fake_result = MagicMock()
        fake_result.valid = True
        with patch('frictionless.Resource.validate', return_value=fake_result):
            rt.save(tmp_path)
        assert (tmp_path / f'{rt.name}.resource.yaml').exists()


# ---------------------------------------------------------------------------
# TestDimensionTableExport
# ---------------------------------------------------------------------------

class TestDimensionTableExport:
    """Tests for DimensionTable._to_wide_flat / create_schema / create_resource / save."""

    def _make_dt(self):
        """Two-geography cross-dim DimensionTable (nativity × sex)."""
        base = _make_long_cross()
        # Add a second geography so data columns span two geos
        geo2 = base.copy()
        geo2['geoidfq'] = '0500000US39041'
        geo2['name'] = 'Delaware County, Ohio'
        import pandas as _pd
        return DimensionTable(_pd.concat([base, geo2], ignore_index=True))

    # --- _to_wide_flat ---

    def test_to_wide_flat_has_dim_columns(self):
        dt = self._make_dt()
        flat = dt._to_wide_flat()
        for dim in dt.dims.columns:
            assert dim in flat.columns, f"Dim column '{dim}' missing from flat output"

    def test_to_wide_flat_data_col_format(self):
        dt = self._make_dt()
        flat = dt._to_wide_flat()
        data_cols = [c for c in flat.columns if c not in dt.dims.columns]
        assert len(data_cols) > 0
        for col in data_cols:
            # Must match "{name} - {year} ({label})"
            assert ' - ' in col and '(' in col and col.endswith(')')

    def test_to_wide_flat_no_metadata_cols(self):
        dt = self._make_dt()
        flat = dt._to_wide_flat()
        for meta in ('concept', 'universe', 'survey', 'geoidfq'):
            assert meta not in flat.columns, f"Metadata column '{meta}' should not be in flat output"

    def test_to_wide_flat_estimate_label(self):
        dt = self._make_dt()
        flat = dt._to_wide_flat(value_mode='estimate')
        data_cols = [c for c in flat.columns if c not in dt.dims.columns]
        assert any('Estimate' in c for c in data_cols)

    def test_to_wide_flat_percent_label(self):
        dt = self._make_dt()
        flat = dt._to_wide_flat(value_mode='percent')
        data_cols = [c for c in flat.columns if c not in dt.dims.columns]
        assert any('Estimate' in c or 'Percent' in c for c in data_cols)

    def test_to_wide_flat_drops_all_null_columns(self):
        import numpy as _np
        dt = self._make_dt()
        wide = dt.wide()
        n_full = len(wide.columns)
        # Null out the first data column entirely
        wide.iloc[:, 0] = _np.nan
        dt.wide = lambda: wide
        flat = dt._to_wide_flat()
        data_cols = [c for c in flat.columns if c not in dt.dims.columns]
        # Should have one fewer data column than the unmodified wide output
        assert len(data_cols) == n_full - 1

    def test_create_schema_excludes_all_null_columns(self):
        import numpy as _np
        dt = self._make_dt()
        wide = dt.wide()
        wide.iloc[:, 0] = _np.nan
        dt.wide = lambda: wide
        flat = dt._to_wide_flat()
        schema = dt.create_schema()
        schema_field_names = [f.name for f in schema.fields]
        assert schema_field_names == list(flat.columns)

    # --- create_schema ---

    def test_create_schema_dim_fields_are_string(self):
        import frictionless
        dt = self._make_dt()
        schema = dt.create_schema()
        dim_names = list(dt.dims.columns)
        for field in schema.fields:
            if field.name in dim_names:
                assert field.type == 'string', f"Dim field '{field.name}' should be string"

    def test_create_schema_data_fields_are_number(self):
        import frictionless
        dt = self._make_dt()
        schema = dt.create_schema()
        dim_names = list(dt.dims.columns)
        for field in schema.fields:
            if field.name not in dim_names:
                assert field.type == 'number', f"Data field '{field.name}' should be number"

    def test_create_schema_primary_key_is_dim_names(self):
        import frictionless
        dt = self._make_dt()
        schema = dt.create_schema()
        assert list(schema.primary_key) == list(dt.dims.columns)

    def test_create_schema_returns_frictionless_schema(self):
        import frictionless
        dt = self._make_dt()
        schema = dt.create_schema()
        assert isinstance(schema, frictionless.Schema)

    # --- create_resource ---

    def test_create_resource_contains_concept(self):
        import frictionless
        dt = self._make_dt()
        dt._export_filename = 'test.csv'
        dt._export_schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(
            frictionless.Resource, 'from_descriptor',
            side_effect=lambda d: captured.update(d) or MagicMock()
        ):
            dt.create_resource('test')
        assert captured['_concept'] == dt.long['concept'].dropna().iloc[0]

    def test_create_resource_contains_universe(self):
        import frictionless
        dt = self._make_dt()
        dt._export_filename = 'test.csv'
        dt._export_schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(
            frictionless.Resource, 'from_descriptor',
            side_effect=lambda d: captured.update(d) or MagicMock()
        ):
            dt.create_resource('test')
        assert captured['_universe'] == dt.long['universe'].dropna().iloc[0]

    def test_create_resource_title_defaults_to_concept(self):
        import frictionless
        dt = self._make_dt()
        dt._export_filename = 'test.csv'
        dt._export_schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(
            frictionless.Resource, 'from_descriptor',
            side_effect=lambda d: captured.update(d) or MagicMock()
        ):
            dt.create_resource('test')
        assert captured['title'] == dt.long['concept'].dropna().iloc[0]

    def test_create_resource_custom_title(self):
        import frictionless
        dt = self._make_dt()
        dt._export_filename = 'test.csv'
        dt._export_schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(
            frictionless.Resource, 'from_descriptor',
            side_effect=lambda d: captured.update(d) or MagicMock()
        ):
            dt.create_resource('test', title='My Custom Title')
        assert captured['title'] == 'My Custom Title'

    def test_create_resource_contains_geographies(self):
        import frictionless
        dt = self._make_dt()
        dt._export_filename = 'test.csv'
        dt._export_schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(
            frictionless.Resource, 'from_descriptor',
            side_effect=lambda d: captured.update(d) or MagicMock()
        ):
            dt.create_resource('test')
        expected = sorted(dt.long['name'].dropna().unique().tolist())
        assert captured['_geographies'] == expected

    def test_create_resource_contains_vintages(self):
        import frictionless
        dt = self._make_dt()
        dt._export_filename = 'test.csv'
        dt._export_schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(
            frictionless.Resource, 'from_descriptor',
            side_effect=lambda d: captured.update(d) or MagicMock()
        ):
            dt.create_resource('test')
        expected = sorted(int(v) for v in dt.long['reference_period'].dropna().unique())
        assert captured['_vintages'] == expected

    def test_create_resource_description_includes_geo_and_year(self):
        import frictionless
        dt = self._make_dt()
        dt._export_filename = 'test.csv'
        dt._export_schema_filename = 'test.schema.yaml'
        captured = {}
        with patch.object(
            frictionless.Resource, 'from_descriptor',
            side_effect=lambda d: captured.update(d) or MagicMock()
        ):
            dt.create_resource('test')
        year = str(int(dt.long['reference_period'].dropna().iloc[0]))
        assert year in captured['description']

    # --- save ---

    def test_save_writes_csv(self, tmp_path):
        dt = self._make_dt()
        fake = MagicMock()
        fake.valid = True
        with patch('frictionless.Resource.validate', return_value=fake):
            dt.save(tmp_path, 'test-export')
        assert (tmp_path / 'test-export.csv').exists()

    def test_save_writes_schema(self, tmp_path):
        dt = self._make_dt()
        fake = MagicMock()
        fake.valid = True
        with patch('frictionless.Resource.validate', return_value=fake):
            dt.save(tmp_path, 'test-export')
        assert (tmp_path / 'test-export.schema.yaml').exists()

    def test_save_writes_resource(self, tmp_path):
        dt = self._make_dt()
        fake = MagicMock()
        fake.valid = True
        with patch('frictionless.Resource.validate', return_value=fake):
            dt.save(tmp_path, 'test-export')
        assert (tmp_path / 'test-export.resource.yaml').exists()

    def test_save_csv_has_correct_columns(self, tmp_path):
        import pandas as _pd
        dt = self._make_dt()
        fake = MagicMock()
        fake.valid = True
        with patch('frictionless.Resource.validate', return_value=fake):
            dt.save(tmp_path, 'test-export')
        written = _pd.read_csv(tmp_path / 'test-export.csv')
        for dim in dt.dims.columns:
            assert dim in written.columns
        for meta in ('concept', 'universe', 'survey', 'geoidfq'):
            assert meta not in written.columns

    def test_save_raises_on_invalid_resource(self, tmp_path):
        dt = self._make_dt()
        fake = MagicMock()
        fake.valid = False
        fake.stats = {}
        with patch('frictionless.Resource.validate', return_value=fake):
            with pytest.raises(RuntimeError, match='validation failed'):
                dt.save(tmp_path, 'test-export')
