"""
Import-only smoke tests — verify the package and its submodules load without error.
No network access required.
"""

import morpc_census


def test_package_imports():
    assert isinstance(morpc_census.__version__, str)
    assert len(morpc_census.__version__) > 0


def test_api_module_imports():
    from morpc_census import api
    assert hasattr(api, "CensusAPI")
    assert hasattr(api, "DimensionTable")
    assert hasattr(api, "IMPLEMENTED_ENDPOINTS")


def test_tigerweb_module_imports():
    from morpc_census import tigerweb
    assert hasattr(tigerweb, "get_layer_url")
    assert hasattr(tigerweb, "get_tigerweb_layers_map")


def test_geos_module_imports():
    from morpc_census import geos
    assert hasattr(geos, "SCOPES")
    assert hasattr(geos, "PSEUDOS")
    assert hasattr(geos, "GeoIDFQ")
    assert hasattr(geos, "SumLevel")
    assert hasattr(geos, "Scope")


def test_scopes_is_not_accessed_at_import():
    """SCOPES dict must not be populated until first access — no network at import time."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-c",
         "from morpc_census.geos import SCOPES; print(SCOPES._loaded)"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", f"SCOPES was loaded at import time: {result.stdout.strip()}"
