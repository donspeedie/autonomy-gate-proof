"""Load gate.py from .github/autonomy as the module `gate`."""

import importlib.util
import pathlib
import sys

_GATE_PATH = pathlib.Path(__file__).resolve().parent.parent / ".github" / "autonomy" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate"] = gate
_spec.loader.exec_module(gate)
