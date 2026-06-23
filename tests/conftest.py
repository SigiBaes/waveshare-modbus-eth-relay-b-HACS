"""Make the pure helpers importable in the bare logic-test venv.

The integration's package ``__init__.py`` imports Home Assistant and pymodbus,
neither of which is installed for the logic-only test suite. ``helpers.py`` is
pure (it imports nothing third-party), so we register the parent packages as
namespace packages whose ``__init__.py`` is NOT executed. Importing
``custom_components.waveshare_relay_b.helpers`` then loads only ``helpers.py``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_COMPONENT_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "waveshare_relay_b"

for _name, _path in (
    ("custom_components", _COMPONENT_DIR.parent),
    ("custom_components.waveshare_relay_b", _COMPONENT_DIR),
):
    if _name not in sys.modules:
        _module = types.ModuleType(_name)
        _module.__path__ = [str(_path)]  # mark as a package so submodules import
        sys.modules[_name] = _module
