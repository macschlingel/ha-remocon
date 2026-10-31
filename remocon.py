#!/usr/bin/env python3
"""
Elco Remocon-Net CLI — Read and control your Elco heat pump from the terminal.

Supports reading temperatures, operation modes, and system status, plus
setting zone temperatures, operation modes, and DHW settings via the
unofficial Remocon-Net cloud API.

Based on cschnidr/remocon-net-cli (read-only) and reverse-engineered
Ariston/Elco remotethermo.com API endpoints.
"""

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import requests

# ============================================================================
# Constants
# ============================================================================

DEFAULT_BASE_URL = "https://www.remocon-net.remotethermo.com"

ZONE_MODES = {
    0: "Protection",
    1: "Automatic",
    2: "Reduction",
    3: "Comfort",
}

ZONE_MODE_MAP = {v.lower(): k for k, v in ZONE_MODES.items()}

BSB_ZONE_MODES = {
    0: "OFF",
    1: "TIME_PROGRAM",
    2: "MANUAL_NIGHT",
    3: "MANUAL",
}

DHW_MODES = {
    0: "OFF",
    1: "ON",
}

# Flat-R2 (newer gateways, e.g. Aerotop MONO / GALEVO family) mode maps.
FLAT_ZONE_MODES = {
    0: "off",
    2: "manual",
    3: "time_program",
}
FLAT_ZONE_MODE_MAP = {v: k for k, v in FLAT_ZONE_MODES.items()}

FLAT_DHW_MODES = {
    0: "disabled",
    1: "timed",
    2: "continuous",
}
FLAT_DHW_MODE_MAP = {v: k for k, v in FLAT_DHW_MODES.items()}

PLANT_MODES = {
    0: "summer",
    1: "winter",
    2: "heating",
    3: "cooling",
    5: "off",
}
PLANT_MODE_MAP = {v: k for k, v in PLANT_MODES.items()}

FLAT_RELEVANT_ITEM_IDS = {
    "ZoneComfortTemp", "ZoneEconomyTemp", "ZoneDesiredTemp", "ZoneMeasuredTemp",
    "ZoneMode", "ZoneHeatRequest", "OutsideTemp", "DhwStorageTemperature",
    "DhwTimeProgComfortTemp", "DhwTimeProgEconomyTemp", "DhwMode",
    "IsHeatingPumpOn", "HeatingCircuitPressure", "ChFlowSetpointTemp",
    "IsDhwBoost", "IsQuite", "PlantMode",
}

DHW_WRITE_REQUIRED_ITEM_IDS = (
    "DhwTimeProgComfortTemp",
    "DhwTimeProgEconomyTemp",
    "DhwMode",
    "DhwStorageTemperature",
)
DHW_WRITE_OPTIONAL_ITEM_IDS = (
    "IsDhwBoost",
)
DHW_WRITE_ITEM_IDS = DHW_WRITE_REQUIRED_ITEM_IDS + DHW_WRITE_OPTIONAL_ITEM_IDS

# AJAX headers the R2 web app sends with every postJson/getJson call.
AJAX_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Ajax-Request": "json",
    "X-Requested-With": "XMLHttpRequest",
}

# Advanced-settings group written together when toggling quiet mode.
QUIET_MODE_ITEM_IDS = ("IsQuite", "QuietModeStart", "QuietModeEnd")

# Features payload used for v2 API calls — matches what the web UI sends.
# This may need adjustment depending on your specific heat pump model.
FEATURES_PAYLOAD = {
    "zones": [{"num": 1, "name": "", "roomSens": False, "geofenceDeroga": False,
               "virtInfo": None, "isHidden": False}],
    "solar": False, "convBoiler": False, "commBoiler": False, "hpSys": False,
    "hybridSys": False, "cascadeSys": False, "dhwProgSupported": True,
    "virtualZones": False, "hasVmc": False, "extendedTimeProg": False,
    "hasBoiler": True, "pilotSupported": False, "isVmcR2": False,
    "isEvo2": False, "dhwHidden": False, "dhwBoilerPresent": True,
    "dhwModeChangeable": True, "hvInputOff": False, "autoThermoReg": False,
    "hasMetering": False, "hasFireplace": False, "hasSlp": False,
    "hasEm20": False, "hasEm30": False, "systemServices": 0,
    "hasTwoCoolingTemp": False, "bmsActive": False, "hpCascadeSys": False,
    "hpCascadeConfig": -1, "bufferTimeProgAvailable": False,
    "distinctHeatCoolSetpoints": False, "hasZoneNames": False,
    "zoneManagerStandAlone": False, "hydraulicScheme": None,
    "preHeatingSupported": False, "hasGahp": False, "zigbeeActive": False,
    "hasSlpAloneOnBus": False, "isSlpCascade": False,
    "hasZeroColdWaterProg": False, "weatherProvider": 0,
    "hasDhwTimeProgTemperatures": 2, "isGSWHCommercialAloneOnBus": False,
}

# Feature profile observed for Aerotop Split (flat R2) gateways. Used only by
# the flat R2 fallback, whose response depends on the installation features.
FLAT_FEATURES_PAYLOAD = {
    **FEATURES_PAYLOAD,
    "solar": False, "convBoiler": False, "commBoiler": False, "hpSys": True,
    "hybridSys": False, "cascadeSys": False, "dhwProgSupported": True,
    "virtualZones": False, "hasVmc": False, "extendedTimeProg": False,
    "hasBoiler": False, "pilotSupported": True, "isVmcR2": False,
    "isEvo2": False, "dhwHidden": False, "dhwBoilerPresent": True,
    "dhwModeChangeable": True, "hvInputOff": False, "autoThermoReg": True,
    "hasMetering": True, "hasFireplace": False, "hasSlp": False,
    "hasEm20": True, "hasEm30": False, "systemServices": None,
    "hasTwoCoolingTemp": True, "bmsActive": False, "hpCascadeSys": False,
    "hpCascadeSysPcm5": False, "hpCascadeConfig": -1,
    "bufferTimeProgAvailable": True, "distinctHeatCoolSetpoints": True,
    "hasZoneNames": True, "hydraulicScheme": 5, "preHeatingSupported": False,
    "hasGahp": False, "zigbeeActive": False, "hasSlpAloneOnBus": False,
    "isSlpCascade": False, "hasZeroColdWaterProg": False,
    "weatherProvider": 1, "hasDhwTimeProgTemperatures": 1,
    "isGSWHCommercialAloneOnBus": False,
}

# ============================================================================
# Data Models
# ============================================================================

@dataclass
class Config:
    email: str
    password: str
    gateway_id: str
    zone_id: str = "1"
    base_url: str = DEFAULT_BASE_URL
    # MQTT
    mqtt_enabled: bool = False
    mqtt_host: Optional[str] = None
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_topic_prefix: str = "remocon/heating"
    mqtt_qos: int = 0
    mqtt_retain: bool = False


@dataclass
class HeatingData:
    current_temperature: float = 0.0
    desired_temperature: float = 0.0
    reduced_temperature: float = 0.0
    comfort_temperature: float = 0.0
    cool_comfort_temperature: Optional[float] = None
    cool_reduced_temperature: Optional[float] = None
    outside_temperature: float = 0.0
    operation_mode: str = "Unknown"
    operation_mode_value: int = 0
    heating_active: bool = False
    cooling_active: bool = False
    heat_or_cool_request: bool = False
    dhw_temperature: float = 0.0
    dhw_comfort_temp: Optional[float] = None
    dhw_reduced_temp: Optional[float] = None
    dhw_mode: str = "Unknown"
    dhw_enabled: bool = False
    boiler_status: str = "Unknown"
    heat_pump_on: bool = False
    system_pressure: Optional[float] = None
    flow_temperature: Optional[float] = None
    zone_id: str = "1"
    # Flat-R2 only (None on legacy gateways -> omitted from output)
    protocol: Optional[str] = None  # "legacy" or "flat"
    plant_mode: Optional[str] = None
    quiet_mode: Optional[bool] = None
    dhw_boost: Optional[bool] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        result = {
            "current_temperature": self.current_temperature,
            "desired_temperature": self.desired_temperature,
            "comfort_temperature": self.comfort_temperature,
            "reduced_temperature": self.reduced_temperature,
            "outside_temperature": self.outside_temperature,
            "operation_mode": self.operation_mode,
            "operation_mode_value": self.operation_mode_value,
            "heating_active": self.heating_active,
            "cooling_active": self.cooling_active,
            "dhw_temperature": self.dhw_temperature,
            "dhw_mode": self.dhw_mode,
            "dhw_enabled": self.dhw_enabled,
            "heat_pump_on": self.heat_pump_on,
            "system_pressure": self.system_pressure,
            "flow_temperature": self.flow_temperature,
            "zone_id": self.zone_id,
            "timestamp": self.timestamp.isoformat(),
        }
        # Flat-R2 extras: only present when the gateway uses the flat format so
        # legacy JSON consumers see no change.
        if self.protocol == "flat":
            result["protocol"] = self.protocol
            result["plant_mode"] = self.plant_mode
            result["quiet_mode"] = self.quiet_mode
            result["dhw_boost"] = self.dhw_boost
        return result


# ============================================================================
# Exceptions
# ============================================================================

class RemoconError(Exception):
    exit_code: int = 1
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

class ConfigError(RemoconError):
    exit_code: int = 4

class AuthError(RemoconError):
    exit_code: int = 3

class NetworkError(RemoconError):
    exit_code: int = 1

class DataError(RemoconError):
    exit_code: int = 2

class SessionExpiredError(RemoconError):
    exit_code: int = 3


# ============================================================================
# Configuration
# ============================================================================

def load_config_file(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config file: {e}")


def build_config(args: argparse.Namespace) -> Config:
    file_data = {}
    config_file = getattr(args, "config_file", None)
    if config_file:
        file_data = load_config_file(config_file)

    def _val(cli_attr, file_key, env_key):
        v = getattr(args, cli_attr, None)
        if v is not None:
            return v
        v = os.environ.get(env_key)
        if v is not None:
            return v
        v = file_data.get(file_key)
        return v

    email = _val("email", "email", "REMOCON_EMAIL")
    password = _val("password", "password", "REMOCON_PASSWORD")
    gateway_id = _val("gateway", "gateway_id", "REMOCON_GATEWAY")
    zone_id = _val("zone", "zone_id", "REMOCON_ZONE")

    missing = [f for f, v in [("email", email), ("password", password), ("gateway", gateway_id)] if not v]
    if missing:
        raise ConfigError(f"Missing required config: {', '.join(missing)}")

    return Config(
        email=email,
        password=password,
        gateway_id=gateway_id,
        zone_id=str(zone_id or "1"),
        base_url=file_data.get("base_url", DEFAULT_BASE_URL),
        mqtt_enabled=_val("mqtt_enabled", "mqtt_enabled", "REMOCON_MQTT_ENABLED") or False,
        mqtt_host=_val("mqtt_host", "mqtt_host", "REMOCON_MQTT_HOST"),
        mqtt_port=int(_val("mqtt_port", "mqtt_port", "REMOCON_MQTT_PORT") or 1883),
        mqtt_username=_val("mqtt_username", "mqtt_username", "REMOCON_MQTT_USER"),
        mqtt_password=_val("mqtt_password", "mqtt_password", "REMOCON_MQTT_PASS"),
        mqtt_topic_prefix=_val("mqtt_topic_prefix", "mqtt_topic_prefix", "REMOCON_MQTT_TOPIC") or "remocon/heating",
        mqtt_qos=int(_val("mqtt_qos", "mqtt_qos", "REMOCON_MQTT_QOS") or 0),
        mqtt_retain=bool(_val("mqtt_retain", "mqtt_retain", "REMOCON_MQTT_RETAIN")),
    )


# ============================================================================
# API Client
# ============================================================================

class RemoconClient:
    """Low-level API client for the Elco Remocon-Net cloud service."""

    def __init__(self, config: Config):
        self.config = config
        self.session: Optional[requests.Session] = None
        # Detected protocol: None = not yet probed, "legacy" (plantData/zoneData
        # via PlantHome/PlantHomeBsb) or "flat" (data.items via PlantHome).
        self._protocol: Optional[str] = None
        self._planthome_variant: Optional[str] = None  # planthome_si | bsb_si | bsb_browser

    @property
    def is_flat_api(self) -> bool:
        """Whether this gateway uses the flat R2 data format."""
        return self._protocol == "flat"

    def login(self) -> None:
        """Authenticate via the R2 web login (cookie-based session)."""
        s = requests.Session()
        url = f"{self.config.base_url}/R2/Account/Login?returnUrl=HTTP/2"
        payload = (
            f"Email={quote(self.config.email, safe='')}"
            f"&Password={quote(self.config.password, safe='')}"
            f"&RememberMe=false"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": "browserUtcOffset=-120",
        }
        try:
            resp = s.post(url, headers=headers, data=payload, timeout=15)
        except requests.RequestException as e:
            raise NetworkError(f"Connection failed: {e}")

        if resp.status_code in (401, 403):
            raise AuthError("Invalid email or password")
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError:
            raise AuthError("Could not parse login response")

        if not data.get("ok"):
            raise AuthError(data.get("message", "Login failed"))

        self.session = s

    def _ensure_session(self) -> requests.Session:
        if self.session is None:
            self.login()
        return self.session  # type: ignore

    def _request(self, method: str, path: str, **kwargs) -> Any:
        s = self._ensure_session()
        url = f"{self.config.base_url}{path}"
        kwargs.setdefault("timeout", 15)
        try:
            resp = s.request(method, url, **kwargs)
        except requests.RequestException as e:
            raise NetworkError(f"Request failed: {e}")

        if resp.status_code in (401, 403):
            raise SessionExpiredError("Session expired, re-login needed")
        resp.raise_for_status()
        return resp.json()

    # ---- Read operations ----

    def _request_variant(self, variant: str) -> Any:
        """Read one PlantHome endpoint variant using its matching request body."""
        prefix = "/R2/PlantHome" if variant == "planthome_si" else "/R2/PlantHomeBsb"
        path = f"{prefix}/GetData/{self.config.gateway_id}"
        payload: dict[str, Any] = {
            "useCache": True,
            "zone": int(self.config.zone_id),
        }
        if variant == "bsb_browser":
            # Historically the CLI used this browser-style BSB request; keep it
            # byte-identical so existing BSB gateways behave exactly as before.
            payload["filter"] = {"progIds": None, "plant": True, "zone": True}
        else:
            path += "?umsys=si"
            payload["filter"] = {
                "notEssentials": False, "plant": True, "zone": True, "dhw": True,
            }
            payload["features"] = FEATURES_PAYLOAD
        return self._request("POST", path, json=payload)

    def _get_raw(self) -> Any:
        """Fetch raw plant data, detecting the gateway's protocol on first call.

        Returns the legacy plantData/zoneData dict, or a dict of flat items
        keyed by item id (self._protocol == "flat").
        """
        if self._protocol == "flat":
            return self._get_flat_raw()

        variants = ["planthome_si", "bsb_si", "bsb_browser"]
        if self._planthome_variant:
            variants.remove(self._planthome_variant)
            variants.insert(0, self._planthome_variant)
        skip_si_variants = False
        for variant in variants:
            if skip_si_variants and variant != "bsb_browser":
                continue
            try:
                data = self._request_variant(variant)
            except NetworkError:
                # Cached variant started failing: drop it so the next call
                # re-probes instead of staying pinned to a bad endpoint.
                if self._planthome_variant == variant:
                    self._planthome_variant = None
                continue
            has_flat_items = (
                isinstance(data, dict)
                and isinstance(data.get("data"), dict)
                and isinstance(data["data"].get("items"), list)
            )
            if has_flat_items:
                self._planthome_variant = None
                return self._get_flat_raw()
            if not isinstance(data, dict) or not data.get("ok", True):
                skip_si_variants = True
                continue
            legacy = data.get("data", data)
            if isinstance(legacy, dict) and (
                "plantData" in legacy or "zoneData" in legacy
            ):
                self._planthome_variant = variant
                self._protocol = "legacy"
                return legacy
            skip_si_variants = True

        # The current R2 endpoint omits ?umsys=si and returns data.items.
        self._planthome_variant = None
        return self._get_flat_raw()

    @staticmethod
    def _flat_items(items: list) -> dict:
        mapped = {
            str(item["id"]): item
            for item in items
            if isinstance(item, dict) and item.get("id") is not None
        }
        if not mapped:
            raise DataError("Flat R2 response contains no valid data items")
        return mapped

    def _flat_features(self) -> dict:
        """Build the gateway-specific feature profile used by flat R2 calls."""
        return {
            **FLAT_FEATURES_PAYLOAD,
            "gatewayId": self.config.gateway_id,
            "zones": [{
                "num": int(self.config.zone_id), "name": "Zone", "roomSens": False,
                "geofenceDeroga": True, "virtInfo": None, "isHidden": False,
            }],
        }

    def _get_flat_item_call(self, filter_data: dict, *, use_cache: bool = True) -> dict:
        """Fetch and index one group of flat R2 data items."""
        response = self._request(
            "POST",
            f"/R2/PlantHome/GetData/{self.config.gateway_id}",
            json={
                "useCache": use_cache,
                "zone": int(self.config.zone_id),
                "filter": filter_data,
                "features": self._flat_features(),
            },
        )
        if not isinstance(response, dict) or not response.get("ok", True):
            message = response.get("message") if isinstance(response, dict) else None
            raise DataError(message or "Flat R2 request failed")
        data = response.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise DataError("Flat R2 response contains no data items")
        return self._flat_items(items)

    def _get_flat_raw(self, *, use_cache: bool = True) -> dict:
        """Fetch the flat R2 data format, caching the protocol after success."""
        essentials = self._get_flat_item_call(
            {"notEssentials": False, "plant": True, "zone": True, "dhw": True},
            use_cache=use_cache,
        )
        has_relevant_value = any(
            item_id in essentials
            and essentials[item_id].get("value") not in (None, "")
            for item_id in FLAT_RELEVANT_ITEM_IDS
        )
        if not has_relevant_value:
            raise DataError(
                "Flat R2 response contains no items used by this integration"
            )

        self._planthome_variant = None
        self._protocol = "flat"
        items = dict(essentials)
        try:
            items.update(self._get_flat_item_call({
                "dhw": False,
                "notEssentials": True,
                "plant": False,
                "progId": 9,
                "zone": False,
            }, use_cache=use_cache))
        except (NetworkError, DataError):
            # Status-only items are useful but optional; an outage here must not
            # prevent status reads.
            pass
        return items

    def get_plant_data(self) -> dict:
        """Fetch plant + zone data via R2 API (protocol auto-detected)."""
        return self._get_raw()

    def get_system_items(self, item_ids: list[dict]) -> dict:
        """Fetch specific data items via the v2 API."""
        path = f"/api/v2/remote/dataItems/{self.config.gateway_id}/get?umsys=si"
        payload = {
            "useCache": False,
            "items": item_ids,
            "features": FEATURES_PAYLOAD,
            "culture": "de",
        }
        data = self._request("POST", path, json=payload)
        result = {}
        for item in data.get("items", []):
            result[item["id"]] = item.get("value")
        return result

    @staticmethod
    def _flat_number(items: dict, key: str, default: float = 0.0, attr: str = "value") -> float:
        item = items.get(key, {})
        v = item.get(attr) if isinstance(item, dict) else None
        if v is None or v == "":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _flat_bool(items: dict, key: str) -> bool:
        item = items.get(key, {})
        v = item.get("value") if isinstance(item, dict) else None
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        return bool(v)

    def _get_full_data_flat(self, items: dict) -> HeatingData:
        """Map flat R2 items into HeatingData (flat gateways)."""
        def value(key: str, attr: str = "value"):
            item = items.get(key, {})
            return item.get(attr) if isinstance(item, dict) else None

        try:
            mode_val = int(value("ZoneMode"))
        except (TypeError, ValueError):
            mode_val = 1
        mode_item = items.get("ZoneMode", {})
        opts = mode_item.get("optTexts", []) if isinstance(mode_item, dict) else []
        mode_texts = [str(x) for x in opts] if isinstance(opts, list) else []
        mode_str = (
            mode_texts[mode_val]
            if isinstance(mode_val, int) and 0 <= mode_val < len(mode_texts)
            else FLAT_ZONE_MODES.get(mode_val, f"Mode {mode_val}")
        )

        request = self._flat_bool(items, "ZoneHeatRequest")
        request_value = value("ZoneHeatRequest")
        pump_value = value("IsHeatingPumpOn")
        try:
            plant_mode_val = int(value("PlantMode"))
        except (TypeError, ValueError):
            plant_mode_val = None
        if request_value is None or plant_mode_val is None:
            heating_active = False
            cooling_active = False
        else:
            heating_active = request and plant_mode_val != 3
            cooling_active = request and plant_mode_val == 3

        dhw_mode_val = int(self._flat_number(items, "DhwMode"))
        dhw_mode_str = FLAT_DHW_MODES.get(dhw_mode_val, "Unknown")

        return HeatingData(
            current_temperature=self._flat_number(items, "ZoneMeasuredTemp"),
            desired_temperature=self._flat_number(items, "ZoneDesiredTemp"),
            reduced_temperature=self._flat_number(items, "ZoneEconomyTemp"),
            comfort_temperature=self._flat_number(items, "ZoneComfortTemp"),
            outside_temperature=self._flat_number(items, "OutsideTemp"),
            operation_mode=mode_str,
            operation_mode_value=mode_val,
            heating_active=heating_active,
            cooling_active=cooling_active,
            heat_or_cool_request=request,
            dhw_temperature=self._flat_number(items, "DhwStorageTemperature"),
            dhw_comfort_temp=_float_or_none(value("DhwTimeProgComfortTemp")),
            dhw_reduced_temp=_float_or_none(value("DhwTimeProgEconomyTemp")),
            dhw_mode=dhw_mode_str,
            dhw_enabled=dhw_mode_val > 0,
            boiler_status="Unknown",
            heat_pump_on=(
                self._flat_bool(items, "IsHeatingPumpOn")
                if pump_value is not None else False
            ),
            system_pressure=(
                self._flat_number(items, "HeatingCircuitPressure")
                if value("HeatingCircuitPressure") is not None else None
            ),
            flow_temperature=(
                self._flat_number(items, "ChFlowSetpointTemp")
                if value("ChFlowSetpointTemp") is not None else None
            ),
            zone_id=self.config.zone_id,
            protocol="flat",
            plant_mode=PLANT_MODES.get(plant_mode_val) if plant_mode_val is not None else None,
            quiet_mode=self._flat_bool(items, "IsQuite") if "IsQuite" in items else None,
            dhw_boost=self._flat_bool(items, "IsDhwBoost") if "IsDhwBoost" in items else None,
            timestamp=datetime.now(),
        )

    def get_full_data(self) -> HeatingData:
        """Retrieve all available data and return a HeatingData object."""
        raw = self.get_plant_data()
        if self._protocol == "flat":
            return self._get_full_data_flat(raw)
        plant = raw.get("plantData", {})
        zone = raw.get("zoneData", {})

        # Try to get system metrics via v2 API
        sys_items = {}
        try:
            sys_items = self.get_system_items([
                {"id": "HeatingCircuitPressure", "zn": 0},
                {"id": "ChFlowTemp", "zn": 0},
            ])
        except Exception:
            pass

        mode_info = zone.get("mode", {})
        mode_val = mode_info.get("value", 0)
        mode_texts = mode_info.get("allowedOptionTexts", list(ZONE_MODES.values()))
        if mode_val < len(mode_texts):
            mode_str = mode_texts[mode_val]
        else:
            mode_str = ZONE_MODES.get(mode_val, f"Mode {mode_val}")

        dhw_mode_info = plant.get("dhwMode", {})
        dhw_mode_val = dhw_mode_info.get("value", 0)
        dhw_mode_str = "Unknown"
        for opt in dhw_mode_info.get("options", []):
            if opt.get("value") == dhw_mode_val:
                dhw_mode_str = opt.get("text", "Unknown")

        pressure = sys_items.get("HeatingCircuitPressure")
        flow_temp = sys_items.get("ChFlowTemp")

        return HeatingData(
            current_temperature=float(zone.get("roomTemp", 0)),
            desired_temperature=float(zone.get("desiredRoomTemp", 0)),
            reduced_temperature=float(zone.get("chReducedTemp", {}).get("value", 0)),
            comfort_temperature=float(zone.get("chComfortTemp", {}).get("value", 0)),
            cool_comfort_temperature=_float_or_none(zone.get("coolComfortTemp", {}).get("value")),
            cool_reduced_temperature=_float_or_none(zone.get("coolReducedTemp", {}).get("value")),
            outside_temperature=float(plant.get("outsideTemp", 0)),
            operation_mode=mode_str,
            operation_mode_value=mode_val,
            heating_active=bool(zone.get("isHeatingActive", 0)),
            cooling_active=bool(zone.get("isCoolingActive", 0)),
            heat_or_cool_request=bool(zone.get("heatOrCoolRequest", 0)),
            dhw_temperature=float(plant.get("dhwStorageTemp", 0)),
            dhw_comfort_temp=_float_or_none(plant.get("dhwComfortTemp", {}).get("value")),
            dhw_reduced_temp=_float_or_none(plant.get("dhwReducedTemp", {}).get("value")),
            dhw_mode=dhw_mode_str,
            dhw_enabled=bool(plant.get("dhwEnabled", 0)),
            boiler_status="Running" if plant.get("flameSensor") else "Standby",
            heat_pump_on=bool(plant.get("heatPumpOn", 0)),
            system_pressure=float(pressure) if pressure is not None else None,
            flow_temperature=float(flow_temp) if flow_temp is not None else None,
            zone_id=self.config.zone_id,
            protocol="legacy",
            timestamp=datetime.now(),
        )

    # ---- Write operations (via v2 REST API) ----

    def set_zone_temperatures(
        self,
        comfort: Optional[float] = None,
        reduced: Optional[float] = None,
        is_cooling: bool = False,
    ) -> bool:
        """Set comfort and/or reduced temperature for a zone."""
        # Flat-R2 gateways write via the v2 dataItems set endpoint.
        raw = self.get_plant_data()
        if self.is_flat_api:
            if is_cooling:
                raise DataError("Cooling setpoint writes are not supported on flat R2 gateways")
            if comfort is not None:
                self._set_flat_data_item(
                    "ZoneComfortTemp", comfort, int(self.config.zone_id),
                    temperature_name="Zone comfort temperature",
                )
            if reduced is not None:
                self._set_zone_reduced_temperature(reduced)
            return True
        # First, read current values
        zone = raw.get("zoneData", {})

        if is_cooling:
            old_comf = float(zone.get("coolComfortTemp", {}).get("value", 0))
            old_econ = float(zone.get("coolReducedTemp", {}).get("value", 0))
        else:
            old_comf = float(zone.get("chComfortTemp", {}).get("value", 0))
            old_econ = float(zone.get("chReducedTemp", {}).get("value", 0))

        new_comf = comfort if comfort is not None else old_comf
        new_econ = reduced if reduced is not None else old_econ

        path = (
            f"/api/v2/remote/bsbZones/{self.config.gateway_id}"
            f"/{self.config.zone_id}/temperatures?isCooling={'true' if is_cooling else 'false'}"
        )
        payload = {
            "new": {"comf": new_comf, "econ": new_econ},
            "old": {"comf": old_comf, "econ": old_econ},
        }
        self._request("POST", path, json=payload)
        return True

    def _set_zone_reduced_temperature(self, value: float) -> None:
        zone = int(self.config.zone_id)
        current = self._get_v2_data_items(["ZoneComfortTemp", "ZoneEconomyTemp"], zone)
        reduced_item = current.get("ZoneEconomyTemp")
        if not isinstance(reduced_item, dict):
            raise DataError("ZoneEconomyTemp is not available on this gateway")
        value = self._validate_temperature(
            value,
            float(reduced_item.get("min") or 5),
            float(reduced_item.get("max") or 25),
            float(reduced_item.get("step") or 0.5),
            "Zone reduced temperature",
        )
        comfort = (current.get("ZoneComfortTemp") or {}).get("value")
        if comfort is not None:
            try:
                if value > float(comfort):
                    raise DataError(f"Reduced ({value}) must not exceed comfort ({comfort})")
            except (TypeError, ValueError):
                pass
        self._set_v2_data_items([{
            "id": "ZoneEconomyTemp", "prevValue": reduced_item.get("value"),
            "value": value, "zone": zone,
        }])
        self._confirm_v2_write("ZoneEconomyTemp", value, zone)

    def set_zone_mode(self, mode: str, is_cooling: bool = False) -> bool:
        """Set the zone operation mode.

        Legacy names: protection, automatic, reduction, comfort.
        Flat-R2 names: off, manual, time_program.
        """
        mode_lower = mode.lower()
        if mode_lower in FLAT_ZONE_MODE_MAP:
            new_mode = FLAT_ZONE_MODE_MAP[mode_lower]
            if not self._protocol:
                self.get_plant_data()
            if self.is_flat_api:
                if is_cooling:
                    raise DataError("Cooling mode writes are not supported on flat R2 gateways")
                if new_mode not in (0, 2, 3):
                    raise DataError(
                        f"Invalid mode '{mode}' for flat R2. Valid: off, manual, time_program"
                    )
                self._set_flat_data_item("ZoneMode", new_mode, int(self.config.zone_id))
                return True
            raise DataError(
                f"Mode '{mode}' is only supported on flat R2 gateways. "
                f"Valid modes here: {', '.join(ZONE_MODE_MAP.keys())}"
            )
        if mode_lower not in ZONE_MODE_MAP:
            raise DataError(
                f"Invalid mode '{mode}'. Valid modes: "
                f"{', '.join(ZONE_MODE_MAP.keys())} (legacy), "
                f"{', '.join(FLAT_ZONE_MODE_MAP.keys())} (flat R2)"
            )
        new_mode = ZONE_MODE_MAP[mode_lower]
        if not self._protocol:
            self.get_plant_data()
        if self.is_flat_api:
            raise DataError(
                f"Mode '{mode}' is a legacy mode. On flat R2 gateways use: "
                f"{', '.join(FLAT_ZONE_MODE_MAP.keys())}"
            )

        # Read current mode
        raw = self.get_plant_data()
        old_mode = raw.get("zoneData", {}).get("mode", {}).get("value", 1)

        path = (
            f"/api/v2/remote/bsbZones/{self.config.gateway_id}"
            f"/{self.config.zone_id}/mode?isCooling={'true' if is_cooling else 'false'}"
        )
        payload = {"new": new_mode, "old": old_mode}
        self._request("POST", path, json=payload)
        return True

    def set_dhw_temperature(
        self,
        comfort: Optional[float] = None,
        reduced: Optional[float] = None,
    ) -> bool:
        """Set domestic hot water comfort/reduced temperature."""
        if not self._protocol:
            self.get_plant_data()
        if self.is_flat_api:
            if comfort is not None:
                items = self._get_flat_raw(use_cache=False)
                item = items.get("DhwTimeProgComfortTemp", {})
                if not isinstance(item, dict):
                    raise DataError("DhwTimeProgComfortTemp is not available on this gateway")
                validated = self._validate_temperature(
                    comfort,
                    float(item.get("min") or 35),
                    float(item.get("max") or 65),
                    float(item.get("step") or 1),
                    "DHW comfort temperature",
                )
                self._flat_save("PlantDhw", items, "DhwTimeProgComfortTemp", int(validated))
                self._confirm_write("DhwTimeProgComfortTemp", int(validated))
            if reduced is not None:
                items = self._get_flat_raw(use_cache=False)
                item = items.get("DhwTimeProgEconomyTemp", {})
                if not isinstance(item, dict):
                    raise DataError("DhwTimeProgEconomyTemp is not available on this gateway")
                validated = self._validate_temperature(
                    reduced,
                    float(item.get("min") or 30),
                    float(item.get("max") or 55),
                    float(item.get("step") or 1),
                    "DHW reduced temperature",
                )
                self._flat_save("PlantDhw", items, "DhwTimeProgEconomyTemp", int(validated))
                self._confirm_write("DhwTimeProgEconomyTemp", int(validated))
            return True
        raw = self.get_plant_data()
        plant = raw.get("plantData", {})

        old_comf = float(plant.get("dhwComfortTemp", {}).get("value", 45))
        old_econ = float(plant.get("dhwReducedTemp", {}).get("value", 40))

        new_comf = comfort if comfort is not None else old_comf
        new_econ = reduced if reduced is not None else old_econ

        path = f"/api/v2/remote/bsbPlantData/{self.config.gateway_id}/dhwTemp"
        payload = {
            "new": {"comf": new_comf, "econ": new_econ},
            "old": {"comf": old_comf, "econ": old_econ},
        }
        self._request("POST", path, json=payload)
        return True

    def set_dhw_mode(self, mode: str) -> bool:
        """Set DHW mode: 'on'/'off' (legacy) or 'disabled'/'timed'/'continuous' (flat R2)."""
        mode_lower = mode.lower()
        if not self._protocol:
            self.get_plant_data()
        if self.is_flat_api:
            flat_mode = {"off": "disabled", "on": "timed"}.get(mode_lower, mode_lower)
            if flat_mode not in FLAT_DHW_MODE_MAP:
                raise DataError(
                    f"Invalid DHW mode '{mode}' for flat R2. "
                    f"Valid: on/off or {', '.join(FLAT_DHW_MODE_MAP.keys())}"
                )
            new_val = FLAT_DHW_MODE_MAP[flat_mode]
            items = self._get_flat_raw(use_cache=False)
            self._flat_save("PlantDhw", items, "DhwMode", new_val)
            self._confirm_write("DhwMode", new_val)
            return True
        # Legacy gateways keep the historical on/off mapping (1/0).
        if mode_lower == "continuous":
            raise DataError(
                "DHW mode 'continuous' is only supported on flat R2 gateways. "
                "Valid modes here: on, off"
            )
        if mode_lower not in ("on", "off", "disabled", "timed"):
            raise DataError("DHW mode must be 'on' or 'off'")
        new_val = 1 if mode_lower in ("on", "timed") else 0

        path = f"/api/v2/remote/bsbPlantData/{self.config.gateway_id}/dhwMode"
        payload = {"new": new_val}
        self._request("POST", path, json=payload)
        return True

    def set_data_item(self, item_id: str, value: Any, zone: int = 0) -> bool:
        """Set a generic data item via the v2 API."""
        if not self._protocol:
            self.get_plant_data()
        if self.is_flat_api:
            self._set_flat_data_item(item_id, value, zone)
            return True
        # Read current value first
        current = self.get_system_items([{"id": item_id, "zn": zone}])
        old_val = current.get(item_id, 0)

        path = f"/api/v2/remote/dataItems/{self.config.gateway_id}/set?umsys=si"
        payload = {
            "items": [{"id": item_id, "prevValue": old_val, "value": value, "zone": zone}],
            "features": FEATURES_PAYLOAD,
        }
        self._request("POST", path, json=payload)
        return True

    def _v2_features(self) -> dict:
        zone = int(self.config.zone_id)
        return {
            **FEATURES_PAYLOAD,
            "gatewayId": self.config.gateway_id,
            "zones": [{
                "num": zone, "name": "", "roomSens": False,
                "geofenceDeroga": False, "virtInfo": None, "isHidden": False,
            }],
        }

    def _get_v2_data_items(self, item_ids: list[str], zone: int) -> dict:
        path = f"/api/v2/remote/dataItems/{self.config.gateway_id}/get?umsys=si"
        response = self._request("POST", path, json={
            "useCache": False,
            "items": [{"id": item_id, "zn": zone} for item_id in item_ids],
            "features": self._v2_features(),
            "culture": "de",
        })
        if not isinstance(response, dict):
            raise DataError("Invalid v2 dataItems response")
        return {
            str(item["id"]): item
            for item in response.get("items", [])
            if isinstance(item, dict) and item.get("id") is not None
        }

    def _set_v2_data_items(self, writes: list[dict]) -> None:
        path = f"/api/v2/remote/dataItems/{self.config.gateway_id}/set?umsys=si"
        response = self._request("POST", path, json={
            "items": writes,
            "features": self._v2_features(),
        })
        if isinstance(response, dict) and response.get("success") is False:
            raise DataError("Remocon-Net rejected the write request")

    def _validate_temperature(
        self, value: float, min_temp: float, max_temp: float, step: float, name: str
    ) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DataError(f"{name} must be a number, got {type(value).__name__}")
        value = float(value)
        if not math.isfinite(value) or step <= 0:
            raise DataError(f"{name} must be finite and use a positive step")
        if value < min_temp or value > max_temp:
            raise DataError(f"{name} {value} out of range [{min_temp}, {max_temp}]")
        steps = round((value - min_temp) / step)
        if abs(value - (min_temp + steps * step)) > 1e-9:
            raise DataError(f"{name} must use increments of {step}")
        return round(value, 2)

    def _set_flat_data_item(
        self, item_id: str, value: Any, zone: int, *, temperature_name: Optional[str] = None
    ) -> None:
        current = self._get_v2_data_items([item_id], zone)
        item = current.get(item_id)
        if not isinstance(item, dict):
            raise DataError(f"Item {item_id} is not available on this gateway")
        if temperature_name is not None:
            value = self._validate_temperature(
                value, float(item.get("min") or 0), float(item.get("max") or 99),
                float(item.get("step") or 0.5), temperature_name,
            )
        self._set_v2_data_items([{
            "id": item_id, "prevValue": item.get("value"),
            "value": value, "zone": zone,
        }])
        self._confirm_v2_write(item_id, value, zone)

    def _confirm_v2_write(
        self, item_id: str, expected_value: Any, zone: int, max_retries: int = 3
    ) -> None:
        for attempt in range(max_retries):
            try:
                item = self._get_v2_data_items([item_id], zone).get(item_id, {})
                actual = item.get("value") if isinstance(item, dict) else None
                try:
                    if float(str(actual)) == float(str(expected_value)):
                        return
                except (TypeError, ValueError):
                    if str(actual) == str(expected_value):
                        return
            except (NetworkError, DataError) as exc:
                print(f"Warning: write confirmation read failed: {exc}", file=sys.stderr)
            if attempt + 1 < max_retries:
                time.sleep(1)
        print(f"Warning: could not confirm {item_id}={expected_value}", file=sys.stderr)

    def _build_dhw_save_payload(self, items: dict, item_id: str, new_value: Any) -> dict:
        missing = [key for key in DHW_WRITE_REQUIRED_ITEM_IDS if key not in items]
        if missing:
            raise DataError(f"DHW write prerequisites are missing: {', '.join(missing)}")
        if item_id not in items:
            raise DataError(f"DHW item is not available on this gateway: {item_id}")
        write_ids = [key for key in DHW_WRITE_ITEM_IDS if key in items]
        prev_items = []
        request_items = []
        for current_id in write_ids:
            source = items[current_id]
            prev_items.append({
                "id": current_id, "gatewayId": self.config.gateway_id,
                "value": source.get("value"), "zone": source.get("zone", 0),
                "kind": source.get("kind"), "unit": source.get("unit"),
                "min": source.get("min"), "max": source.get("max"),
                "step": source.get("step"), "decimals": source.get("decimals"),
                "options": source.get("options") or [],
                "optTexts": source.get("optTexts") or [],
                "readOnly": bool(source.get("readOnly")),
                "error": bool(source.get("error")), "invalid": bool(source.get("invalid")),
                "expiresOn": source.get("expiresOn"),
            })
            request_items.append({
                "itemId": current_id,
                "value": new_value if current_id == item_id else source.get("value"),
            })
        return {
            "requestItems": request_items,
            "prevDataItems": prev_items,
            "features": self._flat_features(),
        }

    def _flat_save(self, section: str, items: dict, item_id: str, value: Any) -> None:
        if section != "PlantDhw":
            raise DataError(f"Unsupported flat save section: {section}")
        payload = self._build_dhw_save_payload(items, item_id, value)
        response = self._request(
            "POST", f"/R2/{section}/Save/{self.config.gateway_id}", json=payload
        )
        if isinstance(response, dict) and not response.get("ok", True):
            raise DataError("Remocon-Net rejected the write request")

    def _confirm_write(self, item_id: str, expected_value: Any, max_retries: int = 3) -> None:
        for attempt in range(max_retries):
            try:
                items = self._get_flat_raw(use_cache=False)
                actual = (items.get(item_id) or {}).get("value")
                try:
                    if float(str(actual)) == float(str(expected_value)):
                        return
                except (TypeError, ValueError):
                    if str(actual) == str(expected_value):
                        return
            except (NetworkError, DataError) as exc:
                print(f"Warning: write confirmation read failed: {exc}", file=sys.stderr)
            if attempt + 1 < max_retries:
                time.sleep(1)
        print(f"Warning: could not confirm {item_id}={expected_value}", file=sys.stderr)

    def set_dhw_boost(self, enable: bool) -> bool:
        if not self._protocol:
            self.get_plant_data()
        if not self.is_flat_api:
            raise DataError("DHW boost is only supported on flat R2 gateways")
        items = self._get_flat_raw(use_cache=False)
        value = 1 if enable else 0
        self._flat_save("PlantDhw", items, "IsDhwBoost", value)
        self._confirm_write("IsDhwBoost", value)
        return True

    def set_plant_mode(self, mode: str) -> bool:
        mode_lower = mode.lower()
        if mode_lower not in PLANT_MODE_MAP:
            raise DataError(f"Invalid plant mode. Valid: {', '.join(PLANT_MODE_MAP)}")
        if not self._protocol:
            self.get_plant_data()
        if not self.is_flat_api:
            raise DataError("Plant mode control is only supported on flat R2 gateways")
        self._set_flat_data_item("PlantMode", PLANT_MODE_MAP[mode_lower], 0)
        return True

    def set_quiet_mode(self, enable: bool) -> bool:
        if not self._protocol:
            self.get_plant_data()
        if not self.is_flat_api:
            raise DataError("Quiet mode is only supported on flat R2 gateways")
        response = self._request(
            "POST", f"/R2/PlantAdvancedSettings/Refresh/{self.config.gateway_id}",
            headers=AJAX_HEADERS, json={"features": self._flat_features()},
        )
        data = response.get("data", {}) if isinstance(response, dict) else {}
        current = {
            str(item["id"]): item for item in data.get("dataItems", [])
            if isinstance(item, dict) and item.get("id") is not None
        }
        missing = [item_id for item_id in QUIET_MODE_ITEM_IDS if item_id not in current]
        if missing:
            raise DataError(f"Quiet-mode prerequisites are missing: {', '.join(missing)}")
        request_items = []
        prev_items = []
        for item_id in QUIET_MODE_ITEM_IDS:
            source = current[item_id]
            prev_items.append({
                "gatewayId": self.config.gateway_id, "zone": source.get("zone", 0),
                "id": item_id, "kind": source.get("kind"),
                "min": source.get("min"), "max": source.get("max"),
                "step": source.get("step"), "value": source.get("value"),
                "decimals": source.get("decimals"),
                "options": source.get("options") or [],
                "optTexts": source.get("optTexts") or [],
                "readOnly": bool(source.get("readOnly")),
                "error": bool(source.get("error")), "invalid": bool(source.get("invalid")),
                "expiresOn": source.get("expiresOn"), "unit": source.get("unit"),
            })
            request_items.append({
                "itemId": item_id,
                "value": (1 if enable else 0) if item_id == "IsQuite" else source.get("value"),
            })
        response = self._request(
            "POST", f"/R2/PlantAdvancedSettings/Save/{self.config.gateway_id}",
            headers=AJAX_HEADERS,
            json={"features": self._flat_features(), "requestItems": request_items,
                  "prevDataItems": prev_items},
        )
        if isinstance(response, dict) and not response.get("ok", True):
            raise DataError("Remocon-Net rejected the quiet-mode write")
        self._confirm_write("IsQuite", 1 if enable else 0)
        return True


def _float_or_none(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ============================================================================
# MQTT Publisher
# ============================================================================

def publish_mqtt(config: Config, data: HeatingData) -> None:
    """Publish heating data to MQTT broker."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("Warning: paho-mqtt not installed, skipping MQTT", file=sys.stderr)
        return

    client = mqtt.Client()
    if config.mqtt_username:
        client.username_pw_set(config.mqtt_username, config.mqtt_password)
    try:
        client.connect(config.mqtt_host, config.mqtt_port, keepalive=10)
        client.loop_start()
        time.sleep(0.3)
    except Exception as e:
        print(f"MQTT connection failed: {e}", file=sys.stderr)
        return

    prefix = config.mqtt_topic_prefix
    qos = config.mqtt_qos
    retain = config.mqtt_retain

    topics = {
        f"{prefix}/temperature/current": f"{data.current_temperature:.1f}",
        f"{prefix}/temperature/desired": f"{data.desired_temperature:.1f}",
        f"{prefix}/temperature/comfort": f"{data.comfort_temperature:.1f}",
        f"{prefix}/temperature/reduced": f"{data.reduced_temperature:.1f}",
        f"{prefix}/temperature/outside": f"{data.outside_temperature:.1f}",
        f"{prefix}/heating/mode": data.operation_mode,
        f"{prefix}/heating/active": "true" if data.heating_active else "false",
        f"{prefix}/heating/cooling_active": "true" if data.cooling_active else "false",
        f"{prefix}/heatpump/on": "true" if data.heat_pump_on else "false",
        f"{prefix}/dhw/temperature": f"{data.dhw_temperature:.1f}",
        f"{prefix}/dhw/mode": data.dhw_mode,
        f"{prefix}/dhw/enabled": "true" if data.dhw_enabled else "false",
        f"{prefix}/zone_id": data.zone_id,
        f"{prefix}/timestamp": data.timestamp.isoformat(),
    }
    if data.system_pressure is not None:
        topics[f"{prefix}/system/pressure"] = f"{data.system_pressure:.1f}"
    if data.flow_temperature is not None:
        topics[f"{prefix}/system/flow_temperature"] = f"{data.flow_temperature:.1f}"
    if data.dhw_comfort_temp is not None:
        topics[f"{prefix}/dhw/comfort_temp"] = f"{data.dhw_comfort_temp:.1f}"

    # Also publish full JSON payload
    topics[f"{prefix}/data"] = json.dumps(data.to_dict())

    for topic, payload in topics.items():
        client.publish(topic, payload, qos=qos, retain=retain)

    client.loop_stop()
    client.disconnect()
    print(f"Published {len(topics)} MQTT topics to {prefix}", file=sys.stderr)


# ============================================================================
# Display
# ============================================================================

def display_status(data: HeatingData) -> None:
    print("=" * 50)
    print("  Elco Heating System Status")
    print("=" * 50)
    print(f"  Retrieved:  {data.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Zone:       {data.zone_id}")
    print()

    print("  Room Temperatures")
    print("  " + "-" * 46)
    print(f"  Current:        {data.current_temperature:.1f} °C")
    print(f"  Desired:        {data.desired_temperature:.1f} °C")
    print(f"  Comfort Set:    {data.comfort_temperature:.1f} °C")
    print(f"  Reduced Set:    {data.reduced_temperature:.1f} °C")
    if data.cool_comfort_temperature is not None:
        print(f"  Cool Comfort:   {data.cool_comfort_temperature:.1f} °C")
    if data.cool_reduced_temperature is not None:
        print(f"  Cool Reduced:   {data.cool_reduced_temperature:.1f} °C")
    print()

    print("  Outside")
    print("  " + "-" * 46)
    print(f"  Temperature:    {data.outside_temperature:.1f} °C")
    print()

    print("  Heating / Cooling")
    print("  " + "-" * 46)
    print(f"  Mode:           {data.operation_mode} ({data.operation_mode_value})")
    print(f"  Heating Active: {'Yes' if data.heating_active else 'No'}")
    print(f"  Cooling Active: {'Yes' if data.cooling_active else 'No'}")
    print(f"  Heat/Cool Req:  {'Yes' if data.heat_or_cool_request else 'No'}")
    print(f"  Heat Pump:      {'On' if data.heat_pump_on else 'Off'}")
    print()

    print("  Domestic Hot Water")
    print("  " + "-" * 46)
    print(f"  Temperature:    {data.dhw_temperature:.1f} °C")
    if data.dhw_comfort_temp is not None:
        print(f"  Comfort Set:    {data.dhw_comfort_temp:.1f} °C")
    if data.dhw_reduced_temp is not None:
        print(f"  Reduced Set:    {data.dhw_reduced_temp:.1f} °C")
    print(f"  Mode:           {data.dhw_mode}")
    print(f"  Enabled:        {'Yes' if data.dhw_enabled else 'No'}")
    if data.dhw_boost is not None:
        print(f"  Boost:          {'On' if data.dhw_boost else 'Off'}")
    print()

    print("  System")
    print("  " + "-" * 46)
    print(f"  Boiler:         {data.boiler_status}")
    if data.system_pressure is not None:
        print(f"  Pressure:       {data.system_pressure:.1f} bar")
    if data.flow_temperature is not None:
        print(f"  Flow Temp:      {data.flow_temperature:.1f} °C")
    if data.plant_mode is not None:
        print(f"  Plant Mode:     {data.plant_mode}")
    if data.quiet_mode is not None:
        print(f"  Quiet Mode:     {'On' if data.quiet_mode else 'Off'}")
    print()


# ============================================================================
# CLI Commands
# ============================================================================

def cmd_status(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    """Read and display current status."""
    data = client.get_full_data()
    if args.json:
        print(json.dumps(data.to_dict(), indent=2))
    else:
        display_status(data)

    if config.mqtt_enabled:
        publish_mqtt(config, data)
    return 0


def cmd_set_temp(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    """Set zone temperature setpoints."""
    if args.comfort is None and args.reduced is None:
        print("Error: specify --comfort and/or --reduced temperature", file=sys.stderr)
        return 1

    result = client.set_zone_temperatures(
        comfort=args.comfort,
        reduced=args.reduced,
        is_cooling=args.cooling,
    )
    print(f"Zone {config.zone_id} temperatures updated "
          f"(comfort={args.comfort}, reduced={args.reduced}, "
          f"cooling={args.cooling})")
    return 0


def cmd_set_mode(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    """Set zone operation mode."""
    result = client.set_zone_mode(args.mode, is_cooling=args.cooling)
    print(f"Zone {config.zone_id} mode set to: {args.mode}")
    return 0


def cmd_set_dhw_temp(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    """Set DHW temperature setpoints."""
    if args.comfort is None and args.reduced is None:
        print("Error: specify --comfort and/or --reduced temperature", file=sys.stderr)
        return 1

    result = client.set_dhw_temperature(comfort=args.comfort, reduced=args.reduced)
    print(f"DHW temperatures updated (comfort={args.comfort}, reduced={args.reduced})")
    return 0


def cmd_set_dhw_mode(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    """Set DHW mode (on/off)."""
    result = client.set_dhw_mode(args.mode)
    print(f"DHW mode set to: {args.mode}")
    return 0


def cmd_raw_get(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    """Fetch and display raw plant data (for debugging/exploration)."""
    raw = client.get_plant_data()
    print(json.dumps(raw, indent=2))
    return 0


def cmd_set_plant_mode(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    client.set_plant_mode(args.mode)
    print(f"Plant mode set to: {args.mode}")
    return 0


def cmd_set_quiet_mode(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    client.set_quiet_mode(args.mode == "on")
    print(f"Quiet mode set to: {args.mode}")
    return 0


def cmd_set_dhw_boost(client: RemoconClient, config: Config, args: argparse.Namespace) -> int:
    client.set_dhw_boost(args.mode == "on")
    print(f"DHW boost set to: {args.mode}")
    return 0


# ============================================================================
# Argument Parser
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remocon",
        description="Control your Elco heat pump via the Remocon-Net cloud API",
    )
    # Global config options
    _add_config_args(parser)

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # status
    p_status = sub.add_parser("status", help="Show current heating system status")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")
    _add_config_args(p_status)

    # set-temp
    p_temp = sub.add_parser("set-temp", help="Set zone temperature setpoints")
    p_temp.add_argument("--comfort", type=float, help="Comfort temperature (°C)")
    p_temp.add_argument("--reduced", type=float, help="Reduced temperature (°C)")
    p_temp.add_argument("--cooling", action="store_true", help="Set cooling temperatures")
    _add_config_args(p_temp)

    # set-mode
    p_mode = sub.add_parser("set-mode", help="Set zone operation mode")
    p_mode.add_argument("mode", choices=[*ZONE_MODE_MAP.keys(), *FLAT_ZONE_MODE_MAP.keys()],
                        help="Operation mode")
    p_mode.add_argument("--cooling", action="store_true", help="Set cooling mode")
    _add_config_args(p_mode)

    # set-dhw-temp
    p_dhw_temp = sub.add_parser("set-dhw-temp", help="Set DHW temperatures")
    p_dhw_temp.add_argument("--comfort", type=float, help="DHW comfort temperature (°C)")
    p_dhw_temp.add_argument("--reduced", type=float, help="DHW reduced temperature (°C)")
    _add_config_args(p_dhw_temp)

    # set-dhw-mode
    p_dhw_mode = sub.add_parser("set-dhw-mode", help="Set DHW mode")
    p_dhw_mode.add_argument(
        "mode", choices=["on", "off", "disabled", "timed", "continuous"],
        help="DHW mode (legacy: on/off; flat R2: disabled/timed/continuous)",
    )
    _add_config_args(p_dhw_mode)

    p_plant = sub.add_parser("set-plant-mode", help="Set flat-R2 plant mode")
    p_plant.add_argument("mode", choices=list(PLANT_MODE_MAP), help="Plant mode")
    _add_config_args(p_plant)

    p_quiet = sub.add_parser("set-quiet-mode", help="Set flat-R2 quiet mode")
    p_quiet.add_argument("mode", choices=["on", "off"], help="Quiet mode")
    _add_config_args(p_quiet)

    p_boost = sub.add_parser("set-dhw-boost", help="Set flat-R2 DHW boost")
    p_boost.add_argument("mode", choices=["on", "off"], help="DHW boost")
    _add_config_args(p_boost)

    # raw-get
    p_raw = sub.add_parser("raw-get", help="Fetch raw API response (debug)")
    _add_config_args(p_raw)

    return parser


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    """Add configuration arguments to a parser or subparser.

    Uses argparse.SUPPRESS as default so that args provided on the main
    parser are not overwritten by None from a subparser.
    """
    SUP = argparse.SUPPRESS
    parser.add_argument("--email", type=str, help="Remocon-Net email", default=SUP)
    parser.add_argument("--password", type=str, help="Remocon-Net password", default=SUP)
    parser.add_argument("--gateway", type=str, help="Gateway ID", default=SUP)
    parser.add_argument("--zone", type=str, help="Zone ID (default: 1)", default=SUP)
    parser.add_argument("--config-file", type=str, help="Path to config JSON file", default=SUP)
    parser.add_argument("--mqtt-enabled", action="store_true", dest="mqtt_enabled", default=SUP)
    parser.add_argument("--mqtt-host", type=str, dest="mqtt_host", default=SUP)
    parser.add_argument("--mqtt-port", type=int, dest="mqtt_port", default=SUP)
    parser.add_argument("--mqtt-topic", type=str, dest="mqtt_topic_prefix", default=SUP)
    parser.add_argument("--mqtt-retain", action="store_true", dest="mqtt_retain", default=SUP)


# ============================================================================
# Main
# ============================================================================

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    try:
        config = build_config(args)
        client = RemoconClient(config)

        # Auto-retry once on expired session
        try:
            cmd_func = {
                "status": cmd_status,
                "set-temp": cmd_set_temp,
                "set-mode": cmd_set_mode,
                "set-dhw-temp": cmd_set_dhw_temp,
                "set-dhw-mode": cmd_set_dhw_mode,
                "set-plant-mode": cmd_set_plant_mode,
                "set-quiet-mode": cmd_set_quiet_mode,
                "set-dhw-boost": cmd_set_dhw_boost,
                "raw-get": cmd_raw_get,
            }[args.command]
            return cmd_func(client, config, args)
        except SessionExpiredError:
            client.login()
            return cmd_func(client, config, args)

    except ConfigError as e:
        print(f"Config error: {e.message}", file=sys.stderr)
        return e.exit_code
    except AuthError as e:
        print(f"Auth error: {e.message}", file=sys.stderr)
        return e.exit_code
    except NetworkError as e:
        print(f"Network error: {e.message}", file=sys.stderr)
        return e.exit_code
    except DataError as e:
        print(f"Data error: {e.message}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
