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
    get_concept_dims_from_long,
)


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

    def test_dims_column_count_equals_max_subtotal_plus_max_leaf_depth(self):
        # _make_long: all subtotals (Male:, Female: end with ':'), 0 leaves → 2 cols
        dims = DimensionTable(_make_long()).dims
        assert dims.shape[1] == 2

    def test_dims_total_in_first_column(self):
        dims = DimensionTable(_make_long()).dims
        assert dims.iloc[:, 0].eq('Total:').all()

    def test_dims_subtotals_left_aligned(self):
        # Male: and Female: are subtotals → should be in second column, not third
        dims = DimensionTable(_make_long()).dims
        assert dims.loc['B01_002', dims.columns[1]] == 'Male:'
        assert dims.loc['B01_003', dims.columns[1]] == 'Female:'

    def test_dims_leaf_aligned_to_last_column(self):
        # Cross-dim fixture: Male/Female are leaves → always in the last column
        dims = DimensionTable(_make_long_cross()).dims
        last_col = dims.columns[-1]
        sex_rows = ['B05_002', 'B05_003', 'B05_005', 'B05_006',
                    'B05_008', 'B05_009', 'B05_011', 'B05_012']
        assert all(dims.loc[v, last_col] in ('Male', 'Female') for v in sex_rows)

    def test_dims_subtotals_not_in_leaf_column(self):
        # Nativity subtotals (Native:, Foreign-born:, Naturalized:) should NOT be in last col
        dims = DimensionTable(_make_long_cross()).dims
        last_col = dims.columns[-1]
        assert not dims.loc[:, last_col].str.endswith(':').any()

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
        # _make_long returns B01_001 (Total:), B01_002 (Male:), B01_003 (Female:)
        # The second column categories should appear in that order: Total:, Male:, Female:
        dims = DimensionTable(_make_long()).dims
        second_col = dims.iloc[:, 1]
        cats = list(second_col.cat.categories)
        # Male: comes before Female: in the fixture variable order
        assert cats.index('Male:') < cats.index('Female:')

    def test_dims_categorical_order_preserved_across_cross_vintage(self):
        # Cross-vintage fixture: 2018 labels lack ':', but after normalization
        # Male: and Female: should still appear in variable order
        dims = DimensionTable(_make_long_timeseries()).dims
        second_col = dims.iloc[:, 1]
        cats = [c for c in second_col.cat.categories if c != '']
        assert cats.index('Male:') < cats.index('Female:')


# ---------------------------------------------------------------------------
# TestDimensionTableCrossVintage
# ---------------------------------------------------------------------------

class TestDimensionTableCrossVintage:
    """_parse_dims must handle cross-vintage concatenations where older vintages
    omit the trailing ':' from subtotal segments."""

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

    def test_subtotals_normalized_with_colon(self):
        # 'Total' (no ':') and 'Male' / 'Female' (no ':') should become
        # 'Total:' and 'Male:' / 'Female:' after tree-structure normalization
        dims = DimensionTable(_make_long_timeseries()).dims
        assert dims.iloc[:, 0].eq('Total:').all()
        assert dims.loc['B01_002', dims.columns[1]] == 'Male:'
        assert dims.loc['B01_004', dims.columns[1]] == 'Female:'

    def test_leaf_column_has_no_colon(self):
        dims = DimensionTable(_make_long_timeseries()).dims
        last_col = dims.columns[-1]
        non_empty = dims[last_col].loc[dims[last_col] != '']
        assert not non_empty.str.endswith(':').any()


# ---------------------------------------------------------------------------
# TestDimensionTableDrop
# ---------------------------------------------------------------------------

class TestDimensionTableDrop:
    def test_drop_returns_dimension_table(self):
        dt = DimensionTable(_make_long())
        result = dt.drop('dim_1')
        assert isinstance(result, DimensionTable)

    def test_drop_summarize_keeps_aggregate_rows(self):
        # _make_long: dim_1 is 'Male:' / 'Female:' / ''. Summarize keeps '' rows.
        dt = DimensionTable(_make_long())
        result = dt.drop('dim_1', method='summarize')
        assert set(result.long['variable']) == {'B01_001'}

    def test_drop_summarize_removes_leaf_rows(self):
        # Cross-dim: dropping the sex (leaf) column keeps nativity totals only
        dt = DimensionTable(_make_long_cross())
        last_col = dt.dims.columns[-1]
        result = dt.drop(last_col, method='summarize')
        # No rows with Male or Female in the sex column should remain
        assert result.dims[result.dims.columns[-1]].isin(['Male', 'Female']).sum() == 0

    def test_drop_reduces_dim_count_by_one(self):
        dt = DimensionTable(_make_long())
        result = dt.drop('dim_1', method='summarize')
        assert len(result.dims.columns) == len(dt.dims.columns) - 1

    def test_drop_invalid_dim_raises(self):
        import pytest
        dt = DimensionTable(_make_long())
        with pytest.raises(ValueError, match="not in"):
            dt.drop('nonexistent')

    def test_drop_invalid_method_raises(self):
        import pytest
        dt = DimensionTable(_make_long())
        with pytest.raises(ValueError, match="method must be"):
            dt.drop('dim_1', method='invalid')

    def test_drop_aggregate_sums_estimates(self):
        # Drop sex from cross-dim fixture; Native total should be sum of Native+Male + Native+Female
        dt = DimensionTable(_make_long_cross())
        last_col = dt.dims.columns[-1]
        result = dt.drop(last_col, method='aggregate')
        # Find the Native: row
        native_var = result.dims.loc[result.dims['dim_1'] == 'Native:'].index[0]
        native_est = result.long.loc[result.long['variable'] == native_var, 'estimate'].iloc[0]
        assert native_est == 200  # 100 + 100

    def test_drop_aggregate_propagates_moe(self):
        import numpy as np
        dt = DimensionTable(_make_long_cross())
        last_col = dt.dims.columns[-1]
        result = dt.drop(last_col, method='aggregate')
        native_var = result.dims.loc[result.dims['dim_1'] == 'Native:'].index[0]
        native_moe = result.long.loc[result.long['variable'] == native_var, 'moe'].iloc[0]
        expected_moe = np.sqrt(5**2 + 5**2)
        assert abs(native_moe - expected_moe) < 1e-9

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


# ---------------------------------------------------------------------------
# TestDimensionTableConceptDims
# ---------------------------------------------------------------------------

def _make_long_by_concept():
    """Long DataFrame with a 'Sex by Age' concept and two parsed dimensions."""
    return pd.DataFrame({
        'variable': ['B99_001', 'B99_002', 'B99_003', 'B99_004', 'B99_005', 'B99_006'],
        'variable_label': [
            'Total:',
            'Total:!!Male',
            'Total:!!Female',
            'Total:!!Male!!Under 18',
            'Total:!!Male!!18 and over',
            'Total:!!Female!!Under 18',
        ],
        'geoidfq': ['0500000US39049'] * 6,
        'name': ['Franklin County, Ohio'] * 6,
        'concept': ['Sex by Age'] * 6,
        'universe': ['Population'] * 6,
        'survey': ['acs/acs5'] * 6,
        'reference_period': [2023] * 6,
        'estimate': [100, 50, 50, 25, 25, 25],
        'moe': [5, 3, 3, 2, 2, 2],
    })


class TestDimensionTableConceptDims:
    def test_concept_dims_is_dict(self):
        dt = DimensionTable(_make_long())
        assert isinstance(dt.concept_dims, dict)

    def test_concept_dims_empty_for_simple_concept(self):
        # "Test concept" has no " by " → conservative fallback → empty dict → dim_0, dim_1
        dt = DimensionTable(_make_long())
        assert dt.concept_dims == {}

    def test_columns_are_dim_n_for_simple_concept(self):
        dt = DimensionTable(_make_long())
        assert dt.dims.columns[0] == 'dim_0'
        assert dt.dims.columns[1] == 'dim_1'

    def test_auto_infers_names_from_by_concept(self):
        # "Sex by Age" has " by " → inference runs → columns should be named
        dt = DimensionTable(_make_long_by_concept())
        cols = list(dt.dims.columns)
        assert 'dim_0' not in cols or 'dim_1' not in cols  # at least some names resolved

    def test_concept_dims_matches_get_concept_dims_from_long(self):
        long = _make_long_by_concept()
        dt = DimensionTable(long)
        assert dt.concept_dims == get_concept_dims_from_long(long)

    def test_concept_dims_matches_for_simple_concept_too(self):
        long = _make_long()
        dt = DimensionTable(long)
        assert dt.concept_dims == get_concept_dims_from_long(long)

    def test_explicit_list_override_wins(self):
        dt = DimensionTable(_make_long_by_concept(), dim_names=['MyTotal', 'MySex', 'MyAge'])
        assert list(dt.dims.columns) == ['MyTotal', 'MySex', 'MyAge']

    def test_explicit_dict_override_wins(self):
        dt = DimensionTable(_make_long(), dim_names={'dim_0': 'Pop', 'dim_1': 'Group'})
        assert list(dt.dims.columns) == ['Pop', 'Group']

    def test_concept_dims_survives_drop(self):
        dt = DimensionTable(_make_long())
        result = dt.drop('dim_1')
        assert hasattr(result, 'concept_dims')
        assert isinstance(result.concept_dims, dict)

    def test_concept_dims_unchanged_after_remap(self):
        long = _make_long_by_concept()
        dt = DimensionTable(long)
        original = dict(dt.concept_dims)
        dt.remap({'Male': 'Men'})
        assert dt.concept_dims == original

    def test_get_concept_dims_from_long_empty_df(self):
        empty = _make_long().iloc[:0]
        assert get_concept_dims_from_long(empty) == {}


# ---------------------------------------------------------------------------
# TestDimensionTableWideCensusOrder
# ---------------------------------------------------------------------------

def _make_long_age_groups():
    """Long DataFrame with age groups that sort differently alphabetically vs Census order.

    Census order: Under 5 years → 5 to 9 years → ... → 85 years and over
    Alphabetical order would put '5 to 9 years' before '85 years and over', but
    '85 years and over' before 'Under 5 years' (digits before letters).
    We use a short slice: '5 to 9 years' comes before '85 years and over' in
    Census order — alphabetically '85 years and over' < '5 to 9 years'.
    """
    rows = [
        # Census-defined order: total → 5 to 9 → 85 and over
        ('B01_001', 'Total:',                       1000, 20),
        ('B01_002', 'Total:!!5 to 9 years',          100,  5),
        ('B01_003', 'Total:!!85 years and over',      50,  3),
    ]
    variables, labels, estimates, moes = zip(*rows)
    n = len(rows)
    return pd.DataFrame({
        'variable':         list(variables),
        'variable_label':   list(labels),
        'geoidfq':          ['0500000US39049'] * n,
        'name':             ['Franklin County, Ohio'] * n,
        'concept':          ['Test'] * n,
        'universe':         ['Population'] * n,
        'survey':           ['acs/acs5'] * n,
        'reference_period': [2023] * n,
        'estimate':         list(estimates),
        'moe':              list(moes),
    })


def _make_long_equal_subtotals():
    """Long DataFrame where Male and Female subtotals have identical estimate values.

    This tests that wide() does NOT silently drop one of these rows via
    value-based deduplication.
    """
    rows = [
        ('B01_001', 'Total:',          1000, 20),
        ('B01_002', 'Total:!!Male:',    500, 10),
        ('B01_003', 'Total:!!Female:',  500, 10),
    ]
    variables, labels, estimates, moes = zip(*rows)
    n = len(rows)
    return pd.DataFrame({
        'variable':         list(variables),
        'variable_label':   list(labels),
        'geoidfq':          ['0500000US39049'] * n,
        'name':             ['Franklin County, Ohio'] * n,
        'concept':          ['Test'] * n,
        'universe':         ['Population'] * n,
        'survey':           ['acs/acs5'] * n,
        'reference_period': [2023] * n,
        'estimate':         list(estimates),
        'moe':              list(moes),
    })


class TestDimensionTableWideRowOrder:
    """wide() must use Census-defined (categorical) row order, not alphabetical."""

    def test_wide_preserves_census_row_order(self):
        """Rows in wide() must appear in Census variable order, not alphabetically.

        '5 to 9 years' comes before '85 years and over' in Census order.
        Alphabetically, '8...' < '5...' so an alphabetical sort would reverse them.
        """
        dt = DimensionTable(_make_long_age_groups())
        wide = dt.wide()
        # The leaf dim column contains the age group values (stripped of ':')
        leaf_col = wide.index.names[-1]
        # Extract non-empty leaf values from the MultiIndex in row order
        leaf_values = [idx[-1] for idx in wide.index if idx[-1] != '']
        assert len(leaf_values) >= 2, "Expected at least two age-group rows"
        pos_5_to_9 = next(
            i for i, v in enumerate(leaf_values) if '5 to 9' in v
        )
        pos_85_over = next(
            i for i, v in enumerate(leaf_values) if '85' in v
        )
        assert pos_5_to_9 < pos_85_over, (
            f"'5 to 9 years' (pos {pos_5_to_9}) should come before "
            f"'85 years and over' (pos {pos_85_over}) in Census order"
        )

    def test_wide_row_index_uses_ordered_categorical(self):
        """Each level of the wide() row MultiIndex should be an ordered Categorical."""
        dt = DimensionTable(_make_long_age_groups())
        wide = dt.wide()
        for level_name in wide.index.names:
            level = wide.index.get_level_values(level_name)
            assert hasattr(level, 'dtype') and str(level.dtype) == 'category', (
                f"Index level '{level_name}' should be categorical, got {level.dtype}"
            )
            assert level.dtype.ordered, (
                f"Index level '{level_name}' should be ordered categorical"
            )


class TestDimensionTableWideDeduplication:
    """wide() must deduplicate by index only, not by data values."""

    def test_wide_no_value_based_deduplication(self):
        """Both Male and Female rows must survive even when their estimates are equal."""
        dt = DimensionTable(_make_long_equal_subtotals())
        wide = dt.wide()
        # The second dim column holds 'Male' and 'Female' (colon stripped)
        second_dim = wide.index.names[1]
        second_level_values = wide.index.get_level_values(second_dim)
        assert 'Male' in second_level_values, "Male row was dropped by value-based dedup"
        assert 'Female' in second_level_values, "Female row was dropped by value-based dedup"

    def test_wide_row_count_matches_unique_variables(self):
        """wide() must have exactly as many rows as unique variables in long."""
        dt = DimensionTable(_make_long_equal_subtotals())
        wide = dt.wide()
        expected = dt.long['variable'].nunique()
        assert len(wide) == expected, (
            f"Expected {expected} rows, got {len(wide)} — "
            "value-based deduplication may have silently dropped rows"
        )
