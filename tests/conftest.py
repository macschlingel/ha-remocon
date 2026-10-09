"""Stub out the `homeassistant` package for standalone unit tests.

These tests exercise custom_components/elco_remocon/api.py in isolation and
never touch Home Assistant itself, but importing that module still goes
through the package's __init__.py (and coordinator.py), which import real
`homeassistant` symbols. Installing the full `homeassistant` PyPI package
just to satisfy those imports would be a heavy, unnecessary dependency for
what these tests actually check, so provide minimal stand-in modules instead.
"""
import sys
import types


def _module(name: str) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


ha = _module("homeassistant")
ha.__path__ = []  # mark as a package

config_entries = _module("homeassistant.config_entries")
config_entries.ConfigEntry = type("ConfigEntry", (), {})

const = _module("homeassistant.const")
const.CONF_EMAIL = "email"
const.CONF_PASSWORD = "password"
const.Platform = types.SimpleNamespace(
    CLIMATE="climate", SENSOR="sensor", BINARY_SENSOR="binary_sensor"
)

core = _module("homeassistant.core")
core.HomeAssistant = type("HomeAssistant", (), {})

data_entry_flow = _module("homeassistant.data_entry_flow")
data_entry_flow.FlowResult = dict

helpers = _module("homeassistant.helpers")
helpers.__path__ = []
update_coordinator = _module("homeassistant.helpers.update_coordinator")
update_coordinator.DataUpdateCoordinator = type(
    "DataUpdateCoordinator",
    (),
    {"__class_getitem__": classmethod(lambda cls, item: cls)},
)
update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})

exceptions = _module("homeassistant.exceptions")
exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
