"""Reuse the gateway test harness for the contract tests.

The gateway conftest is loaded by path rather than imported as a module: the
repository root also has a `tests/` directory, and two packages called `tests`
on sys.path is a fight nobody wins.

Loading it this way means a contract test exercises the real gateway code
path - the same throwaway database, the same ASGI client - rather than a
parallel stub that could drift.
"""

import importlib.util
import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2] / "gateway"
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

_spec = importlib.util.spec_from_file_location(
    "gateway_test_harness", GATEWAY_ROOT / "tests" / "conftest.py"
)
_harness = importlib.util.module_from_spec(_spec)
sys.modules["gateway_test_harness"] = _harness
_spec.loader.exec_module(_harness)

# Re-exported so pytest discovers them as fixtures for this directory.
anyio_backend = _harness.anyio_backend
engine = _harness.engine
session = _harness.session
client = _harness.client
anon_client = _harness.anon_client
