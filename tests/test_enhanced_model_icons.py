"""Offline tests: stub the open_webui imports, build the loader fragment and
check it. Run with `pytest` from the repo root. Needs pydantic; the JS syntax
check is skipped when node is not installed."""

import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    for name in ("open_webui", "open_webui.utils", "open_webui.utils.json_codec"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["open_webui.utils.json_codec"].JSONCodec = types.SimpleNamespace(
        dumps=json.dumps, loads=json.loads
    )
    spec = importlib.util.spec_from_file_location(
        "function_enhanced_model_icons", ROOT / "enhanced_model_icons.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return load_module()


def fragment(mod, **valves):
    mod.Event._fragment_cache = None
    plugin = mod.Event()
    for key, value in valves.items():
        setattr(plugin.valves, key, value)
    return plugin._loader_fragment()


def test_placeholders_replaced(mod):
    js = fragment(mod)
    assert "__CONFIG__" not in js and "__CSS__" not in js
    assert js.startswith(mod.Event.LOADER_BLOCK_START)
    assert js.endswith(mod.Event.LOADER_BLOCK_END)


def test_default_valves_leave_stock_look(mod):
    plugin = mod.Event()
    assert plugin.valves.default_size == 1.0
    assert plugin.valves.default_shape == "default"
    assert plugin.valves.default_fit == "default"


@pytest.mark.parametrize(
    "raw,expected",
    [(1, 1.0), (0.1, 0.5), (9, 3.0), ("2.2", 2.2), ("abc", 1.0), (float("nan"), 1.0)],
)
def test_clamp_size(mod, raw, expected):
    assert mod.clamp_size(raw) == expected


def test_valve_size_is_clamped_in_config(mod):
    js = fragment(mod, default_size=50)
    assert '"size": 3.0' in js


def test_every_shape_has_a_name(mod):
    assert "default" in mod.SHAPES
    assert all("name" in v for v in mod.SHAPES.values())


def test_asset_key_is_module_constant(mod):
    assert mod.Event.ASSET_KEY == mod.ASSET_KEY == "enhanced-model-icons"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_loader_js_parses(mod, tmp_path):
    out = tmp_path / "loader.js"
    out.write_text(fragment(mod), encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(out)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
