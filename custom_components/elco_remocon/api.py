"""API client for the Elco Remocon-Net cloud service."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any, Literal, Optional
from urllib.parse import quote

import requests

from .const import (
    MODE_AUTOMATIC,
    MODE_COMFORT,
    MODE_PROTECTION,
    MODE_REDUCTION,
)

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.remocon-net.remotethermo.com"

# Seconds; connections and existing requests keep the default timeout.
DEFAULT_REQUEST_TIMEOUT = 15
BSB_BROWSER_READ_TIMEOUT = 30
SLOW_REQUEST_THRESHOLD = 10

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

# Feature profile observed for Aerotop Split gateways. Used only by the flat
# R2 fallback, whose response depends on the installation feature description.
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

_PlantHomeVariant = Literal["planthome_si", "bsb_si", "bsb_browser"]


def _optional_bool(value: Any) -> bool | None:
    """Parse a reported state without treating missing data as off."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    elif isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def _optional_temperature(value: Any) -> float | None:
    """Parse an optional setpoint without inventing a zero temperature."""
    if isinstance(value, bool):
        return None
    try:
        temperature = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return temperature if isfinite(temperature) else None


class RemoconApiError(Exception):
    """Base exception for API errors."""


class RemoconAuthError(RemoconApiError):
    """Authentication failed."""


class RemoconConnectionError(RemoconApiError):
    """Connection error."""


class RemoconDataError(RemoconApiError):
    """Data error."""


@dataclass
class RemoconData:
    """All data from the heating system."""

    # Zone
    comfort_temp: float = 0.0
    comfort_temp_min: float = 5.0
    comfort_temp_max: float = 35.0
    comfort_temp_step: float = 0.5
    reduced_temp: float = 0.0
    desired_temp: float = 0.0
    room_temp: float = 0.0
    zone_mode: int = MODE_AUTOMATIC
    zone_mode_texts: list[str] = field(default_factory=list)
    heating_active: bool | None = False
    cooling_active: bool | None = False
    heat_or_cool_request: bool | None = None
    # Plant
    outside_temp: float = 0.0
    dhw_temp: float = 0.0
    dhw_comfort_temp: float | None = None
    dhw_reduced_temp: float | None = None
    dhw_mode: int = 0
    dhw_enabled: bool = False
    dhw_available: bool = False
    dhw_boost: bool = False
    dhw_boost_available: bool = False
    dhw_comfort_temp_min: float = 35.0
    dhw_comfort_temp_max: float = 65.0
    dhw_comfort_temp_step: float = 1.0
    dhw_reduced_temp_min: float = 30.0
    dhw_reduced_temp_max: float = 55.0
    dhw_reduced_temp_step: float = 1.0
    dhw_mode_text: str = ""
    plant_mode: int = -1
    plant_mode_text: str = ""
    quiet_mode: bool = False
    zone_mode_text: str = ""
    heat_pump_on: bool | None = False
    flame_sensor: bool | None = None
    # System (from v2 API)
    system_pressure: Optional[float] = None
    flow_temperature: Optional[float] = None
    # Meta
    has_room_sensor: bool = False


class RemoconClient:
    """Synchronous API client for Elco Remocon-Net."""

    def __init__(self, email: str, password: str, gateway_id: str, zone: str = "1") -> None:
        self._email = email
        self._password = password
        self._gateway_id = gateway_id
        self._zone = zone
        self._session: Optional[requests.Session] = None
        self._planthome_variant: _PlantHomeVariant | None = None
        self._flat_api = False
        self._last_logged_variant: _PlantHomeVariant | Literal["flat"] | None = None

    @property
    def is_flat_api(self) -> bool:
        """Whether this gateway uses the flat R2 data format."""
        return self._flat_api

    def login(self) -> None:
        """Authenticate and store session cookie."""
        s = requests.Session()
        url = f"{BASE_URL}/R2/Account/Login?returnUrl=HTTP/2"
        payload = (
            f"Email={quote(self._email, safe='')}"
            f"&Password={quote(self._password, safe='')}"
            f"&RememberMe=false"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": "browserUtcOffset=-120",
        }
        try:
            resp = s.post(url, headers=headers, data=payload, timeout=DEFAULT_REQUEST_TIMEOUT)
        except requests.RequestException as err:
            raise RemoconConnectionError(str(err)) from err

        if resp.status_code in (401, 403):
            raise RemoconAuthError("Invalid credentials")
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError as err:
            raise RemoconAuthError("Could not parse login response") from err

        if not data.get("ok"):
            raise RemoconAuthError(data.get("message", "Login failed"))

        self._session = s

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self.login()
        return self._session  # type: ignore[return-value]

    def _request(
        self,
        method: str,
        path: str,
        *,
        api_variant: _PlantHomeVariant | Literal["flat"] | None = None,
        **kwargs: Any,
    ) -> Any:
        s = self._get_session()
        url = f"{BASE_URL}{path}"
        timeout = (
            (DEFAULT_REQUEST_TIMEOUT, BSB_BROWSER_READ_TIMEOUT)
            if api_variant == "bsb_browser"
            else DEFAULT_REQUEST_TIMEOUT
        )
        kwargs.setdefault("timeout", timeout)
        started = time.monotonic() if api_variant is not None else None
        outcome = "failed"
        try:
            try:
                resp = s.request(method, url, **kwargs)
                if resp.status_code in (401, 403):
                    raise RemoconAuthError("Session expired")
                resp.raise_for_status()
            except requests.RequestException as err:
                if isinstance(err, requests.Timeout):
                    outcome = "timed out"
                err_msg = str(err)
                if getattr(err, "response", None) is not None:
                    err_msg += f" - Response: {err.response.text}"
                _LOGGER.error("API Request failed: %s", err_msg)
                raise RemoconConnectionError(err_msg) from err

            try:
                data = resp.json()
            except ValueError as err:
                _LOGGER.error("Invalid JSON response from API: %s", resp.text)
                raise RemoconDataError("Could not parse API response") from err
            outcome = (
                "failed"
                if isinstance(data, dict) and not data.get("ok", True)
                else "completed"
            )
            return data
        finally:
            if api_variant is not None and started is not None:
                self._log_request_duration(api_variant, time.monotonic() - started, outcome)

    def _log_request_duration(
        self, variant: _PlantHomeVariant | Literal["flat"], duration: float, outcome: str
    ) -> None:
        """Report only the variant, outcome and elapsed request time."""
        if outcome == "timed out":
            _LOGGER.info("REMOCON API %s: request timed out after %.1fs", variant, duration)
            return
        slow = duration >= SLOW_REQUEST_THRESHOLD
        log = _LOGGER.info if slow else _LOGGER.debug
        log(
            "REMOCON API %s: %s %s in %.1fs",
            variant, "slow request" if slow else "request", outcome, duration,
        )

    def _request_planthome_variant(self, variant: _PlantHomeVariant) -> Any:
        """Read one endpoint using its matching query and request body."""
        prefix = "/R2/PlantHome" if variant == "planthome_si" else "/R2/PlantHomeBsb"
        path = f"{prefix}/GetData/{self._gateway_id}"
        payload: dict[str, Any] = {
            "useCache": True,
            "zone": int(self._zone),
        }
        if variant == "bsb_browser":
            payload["filter"] = {"progIds": None, "plant": True, "zone": True}
        else:
            path += "?umsys=si"
            payload["filter"] = {
                "notEssentials": False, "plant": True, "zone": True, "dhw": True,
            }
            payload["features"] = FEATURES_PAYLOAD
        return self._request("POST", path, api_variant=variant, json=payload)

    def _log_api_variant(self, variant: _PlantHomeVariant | Literal["flat"]) -> None:
        """Log successful variant changes independently of the request cache."""
        if variant != self._last_logged_variant:
            _LOGGER.info("Selected REMOCON API variant: %s", variant)
            self._last_logged_variant = variant

    def _get_raw(self) -> dict:
        if self._flat_api:
            return self._get_flat_raw()

        # Endpoint and payload compatibility depends on the gateway/controller.
        # Keep the existing SI requests first, then try the browser BSB request.
        # Cache the complete variant because both BSB requests share a prefix.
        variants: list[_PlantHomeVariant] = ["planthome_si", "bsb_si", "bsb_browser"]
        if self._planthome_variant:
            variants.remove(self._planthome_variant)
            variants.insert(0, self._planthome_variant)
        skip_si_variants = False
        for variant in variants:
            if skip_si_variants and variant != "bsb_browser":
                continue
            try:
                data = self._request_planthome_variant(variant)
            except RemoconConnectionError as err:
                if self._planthome_variant == variant:
                    cause = err.__cause__
                    if variant == "bsb_browser" and (
                        isinstance(cause, requests.Timeout)
                        or (
                            isinstance(cause, requests.HTTPError)
                            and cause.response is not None
                            and 500 <= cause.response.status_code < 600
                        )
                    ):
                        # A known browser endpoint need not be rediscovered after
                        # a temporary outage. Keep it for the next regular poll.
                        raise
                    # Preserve rediscovery for the previously supported variants
                    # and for other browser endpoint connection errors.
                    self._planthome_variant = None
                continue
            if (
                variant == "bsb_browser"
                and self._planthome_variant == "bsb_browser"
                and isinstance(data, dict)
                and data.get("ok") is False
            ):
                # A rejected update does not invalidate a confirmed protocol.
                if (
                    isinstance(data.get("message"), str)
                    and data["message"].strip().casefold() == "kommunikationsfehler"
                    and isinstance(data.get("debugMessage"), str)
                    and data["debugMessage"].strip().casefold().startswith("m2m platform error")
                ):
                    raise RemoconConnectionError("REMOCON M2M communication error")
                raise RemoconDataError("REMOCON API rejected data request")
            has_flat_items = (
                isinstance(data, dict)
                and isinstance(data.get("data"), dict)
                and isinstance(data["data"].get("items"), list)
            )
            if has_flat_items:
                # Some gateways return flat items with HTTP 200 from the
                # legacy URL. Continue to the no-umsys fallback so it receives
                # the required flat feature profile.
                self._planthome_variant = None
                return self._get_flat_raw()
            if not isinstance(data, dict) or not data.get("ok", True):
                # Preserve the old break between SI variants, but still allow
                # the browser request before falling back to the flat API.
                skip_si_variants = True
                continue
            legacy = data.get("data", data)
            if isinstance(legacy, dict) and (
                "plantData" in legacy or "zoneData" in legacy
            ):
                self._planthome_variant = variant
                self._flat_api = False
                self._log_api_variant(variant)
                return legacy
            skip_si_variants = True

        # The current R2 endpoint omits ?umsys=si and returns data.items.
        # This path is deliberately a read-only compatibility fallback.
        self._planthome_variant = None
        return self._get_flat_raw()

    @staticmethod
    def _flat_items(items: list[Any]) -> dict[str, Any]:
        mapped = {
            str(item["id"]): item
            for item in items
            if isinstance(item, dict) and item.get("id") is not None
        }
        if not mapped:
            raise RemoconDataError("Flat R2 response contains no valid data items")
        return mapped

    def _get_flat_raw(self, *, use_cache: bool = True) -> dict[str, Any]:
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
            raise RemoconDataError(
                "Flat R2 response contains no items used by this integration"
            )

        self._planthome_variant = None
        self._flat_api = True
        items = dict(essentials)
        try:
            items.update(self._get_flat_item_call({
                "dhw": False,
                "notEssentials": True,
                "plant": False,
                "progId": 9,
                "zone": False,
            }, use_cache=use_cache))
        except (RemoconConnectionError, RemoconDataError) as err:
            # Status-only items are useful but optional. In particular, an
            # outage here must not prevent initial setup or essential polling.
            _LOGGER.debug("Could not fetch optional flat R2 status items: %s", err)
        self._log_api_variant("flat")
        return items

    def _get_flat_item_call(
        self, filter_data: dict[str, Any], *, use_cache: bool = True
    ) -> dict[str, Any]:
        """Fetch and index one group of flat R2 data items."""
        response = self._request(
            "POST",
            f"/R2/PlantHome/GetData/{self._gateway_id}",
            api_variant="flat",
            json={
                "useCache": use_cache,
                "zone": int(self._zone),
                "filter": filter_data,
                "features": {
                    **FLAT_FEATURES_PAYLOAD,
                    "gatewayId": self._gateway_id,
                    "zones": [{
                        "num": int(self._zone), "name": "Zone", "roomSens": False,
                        "geofenceDeroga": True, "virtInfo": None, "isHidden": False,
                    }],
                },
            },
        )
        if not isinstance(response, dict) or not response.get("ok", True):
            message = response.get("message") if isinstance(response, dict) else None
            raise RemoconDataError(message or "Flat R2 request failed")
        data = response.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise RemoconDataError("Flat R2 response contains no data items")
        return self._flat_items(items)

    def _get_system_items(self, item_ids: list[dict]) -> dict[str, Any]:
        path = f"/api/v2/remote/dataItems/{self._gateway_id}/get?umsys=si"
        payload = {
            "useCache": False,
            "items": item_ids,
            "features": FEATURES_PAYLOAD,
            "culture": "de",
        }
        data = self._request("POST", path, json=payload)
        return {item["id"]: item.get("value") for item in data.get("items", [])} if isinstance(data, dict) else {}

    def get_data(self) -> RemoconData:
        """Fetch all data and return a RemoconData object."""
        raw = self._get_raw()
        if not isinstance(raw, dict):
            raise RemoconDataError(f"Unexpected data format from API: {type(raw)}")
            
        if self._flat_api:
            return self._flat_data(raw)
        plant = raw.get("plantData") or {}
        zone = raw.get("zoneData") or {}

        sys_items: dict[str, Any] = {}
        try:
            sys_items = self._get_system_items([
                {"id": "HeatingCircuitPressure", "zn": 0},
                {"id": "ChFlowTemp", "zn": 0},
            ])
        except Exception as err:
            _LOGGER.debug("Could not fetch system items from v2 API: %s", err)

        ch_comfort = zone.get("chComfortTemp") or {}
        ch_reduced = zone.get("chReducedTemp") or {}
        mode_info = zone.get("mode") or {}

        pressure = sys_items.get("HeatingCircuitPressure")
        flow = sys_items.get("ChFlowTemp")

        dhw_comfort = plant.get("dhwComfortTemp") or {}
        dhw_reduced = plant.get("dhwReducedTemp") or {}
        dhw_mode_info = plant.get("dhwMode") or {}

        return RemoconData(
            comfort_temp=float(ch_comfort.get("value", 0)),
            comfort_temp_min=float(ch_comfort.get("min", 5)),
            comfort_temp_max=float(ch_comfort.get("max", 35)),
            comfort_temp_step=float(ch_comfort.get("step", 0.5)),
            reduced_temp=float(ch_reduced.get("value", 0)),
            desired_temp=float(zone.get("desiredRoomTemp", 0)),
            room_temp=float(zone.get("roomTemp", 0)),
            zone_mode=mode_info.get("value", MODE_AUTOMATIC),
            zone_mode_texts=mode_info.get("allowedOptionTexts", []),
            heating_active=bool(zone.get("isHeatingActive", 0)),
            cooling_active=bool(zone.get("isCoolingActive", 0)),
            heat_or_cool_request=_optional_bool(zone.get("heatOrCoolRequest")),
            outside_temp=float(plant.get("outsideTemp", 0)),
            dhw_temp=float(plant.get("dhwStorageTemp", 0)),
            dhw_comfort_temp=_optional_temperature(
                dhw_comfort.get("value") if isinstance(dhw_comfort, dict) else None
            ),
            dhw_reduced_temp=_optional_temperature(
                dhw_reduced.get("value") if isinstance(dhw_reduced, dict) else None
            ),
            dhw_mode=dhw_mode_info.get("value", 0),
            dhw_enabled=bool(plant.get("dhwEnabled", 0)),
            dhw_available="dhwMode" in plant,
            heat_pump_on=bool(plant.get("heatPumpOn", 0)),
            flame_sensor=_optional_bool(plant.get("flameSensor")),
            system_pressure=float(pressure) if pressure is not None else None,
            flow_temperature=float(flow) if flow is not None else None,
            has_room_sensor=bool(zone.get("hasRoomSensor", 0)),
        )

    @staticmethod
    def _flat_data(items: dict[str, Any]) -> RemoconData:
        def value(key: str, attr: str = "value") -> Any:
            item = items.get(key, {})
            return item.get(attr) if isinstance(item, dict) else None

        def number(key: str, default: float = 0.0, attr: str = "value") -> float:
            try:
                value_ = value(key, attr)
                return default if value_ is None or value_ == "" else float(value_)
            except (TypeError, ValueError):
                return default

        def boolean(key: str) -> bool:
            v = value(key)
            if isinstance(v, str):
                return v.strip().lower() in {"1", "true", "yes", "on"}
            return bool(v)

        mode = value("ZoneMode")
        try:
            mode = int(mode)
        except (TypeError, ValueError):
            mode = MODE_AUTOMATIC
        mode_item = items.get("ZoneMode", {})
        opts = mode_item.get("optTexts", []) if isinstance(mode_item, dict) else []
        request = boolean("ZoneHeatRequest")
        request_value = value("ZoneHeatRequest")
        pump_value = value("IsHeatingPumpOn")
        try:
            plant_mode_value = value("PlantMode")
            plant_mode = int(plant_mode_value)
        except (TypeError, ValueError):
            plant_mode_value = None
            plant_mode = -1
        if request_value is None or plant_mode_value is None:
            heating_active = None
            cooling_active = None
        else:
            heating_active = request and plant_mode != 3
            cooling_active = request and plant_mode == 3
        # DHW mode texts
        dhw_mode_val = int(number("DhwMode"))
        dhw_mode_map = {0: "disabled", 1: "timed", 2: "continuous"}
        dhw_mode_text = dhw_mode_map.get(dhw_mode_val, "")

        # DHW boost flag
        dhw_boost = boolean("IsDhwBoost")

        # Quiet mode flag
        quiet_mode = boolean("IsQuite")

        # DHW temp bounds from items
        dhw_comf_min = number("DhwTimeProgComfortTemp", 35.0, "min")
        dhw_comf_max = number("DhwTimeProgComfortTemp", 65.0, "max")
        dhw_comf_step = number("DhwTimeProgComfortTemp", 1.0, "step")
        dhw_red_min = number("DhwTimeProgEconomyTemp", 30.0, "min")
        dhw_red_max = number("DhwTimeProgEconomyTemp", 55.0, "max")
        dhw_red_step = number("DhwTimeProgEconomyTemp", 1.0, "step")

        # Zone mode text
        zone_mode_texts = [str(x) for x in opts] if isinstance(opts, list) else []
        zone_mode_text = (
            zone_mode_texts[mode]
            if isinstance(mode, int) and 0 <= mode < len(zone_mode_texts)
            else ""
        )

        # Plant mode text
        plant_mode_map = {
            0: "summer", 1: "winter", 2: "heating_only",
            3: "cooling", 5: "off",
        }
        plant_mode_text = plant_mode_map.get(plant_mode, "")

        return RemoconData(
            comfort_temp=number("ZoneComfortTemp"),
            comfort_temp_min=number("ZoneComfortTemp", 5.0, "min"),
            comfort_temp_max=number("ZoneComfortTemp", 35.0, "max"),
            comfort_temp_step=number("ZoneComfortTemp", 0.5, "step"),
            reduced_temp=number("ZoneEconomyTemp"),
            desired_temp=number("ZoneDesiredTemp"),
            room_temp=number("ZoneMeasuredTemp"),
            zone_mode=mode,
            zone_mode_texts=zone_mode_texts,
            zone_mode_text=zone_mode_text,
            heating_active=heating_active,
            cooling_active=cooling_active,
            heat_or_cool_request=_optional_bool(request_value),
            outside_temp=number("OutsideTemp"),
            dhw_temp=number("DhwStorageTemperature"),
            dhw_comfort_temp=_optional_temperature(value("DhwTimeProgComfortTemp")),
            dhw_reduced_temp=_optional_temperature(value("DhwTimeProgEconomyTemp")),
            dhw_mode=dhw_mode_val,
            dhw_enabled=dhw_mode_val > 0,
            dhw_available="DhwMode" in items,
            dhw_boost=dhw_boost,
            dhw_boost_available="IsDhwBoost" in items,
            dhw_comfort_temp_min=dhw_comf_min,
            dhw_comfort_temp_max=dhw_comf_max,
            dhw_comfort_temp_step=dhw_comf_step,
            dhw_reduced_temp_min=dhw_red_min,
            dhw_reduced_temp_max=dhw_red_max,
            dhw_reduced_temp_step=dhw_red_step,
            dhw_mode_text=dhw_mode_text,
            plant_mode=plant_mode,
            plant_mode_text=plant_mode_text,
            quiet_mode=quiet_mode,
            heat_pump_on=(
                boolean("IsHeatingPumpOn") if pump_value is not None else None
            ),
            # No verified burner-state item is available for flat R2 gateways.
            flame_sensor=None,
            system_pressure=(
                number("HeatingCircuitPressure")
                if value("HeatingCircuitPressure") is not None
                else None
            ),
            flow_temperature=(
                number("ChFlowSetpointTemp")
                if value("ChFlowSetpointTemp") is not None
                else None
            ),
            has_room_sensor=value("ZoneMeasuredTemp") is not None,
        )

    @staticmethod
    def _current_temperature(raw: Any, section: str, key: str) -> float:
        """Require a finite current setpoint before building a write payload."""
        section_data = raw.get(section) if isinstance(raw, dict) else None
        item = section_data.get(key) if isinstance(section_data, dict) else None
        value = item.get("value") if isinstance(item, dict) else None
        message = f"Missing or invalid current temperature: {section}.{key}.value"
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise RemoconDataError(message)
        try:
            temperature = float(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise RemoconDataError(message) from err
        if not isfinite(temperature):
            raise RemoconDataError(message)
        return temperature

    def set_zone_temperatures(
        self, comfort: float | None = None, reduced: float | None = None
    ) -> None:
        """Set comfort and/or reduced temperature."""
        raw = self._get_raw()
        self._require_legacy_write_api()
        # Both current setpoints are required by the API's "old" payload,
        # even when both new temperatures were supplied by the caller.
        old_comf = self._current_temperature(raw, "zoneData", "chComfortTemp")
        old_econ = self._current_temperature(raw, "zoneData", "chReducedTemp")

        new_comf = comfort if comfort is not None else old_comf
        new_econ = reduced if reduced is not None else old_econ

        path = (
            f"/api/v2/remote/bsbZones/{self._gateway_id}"
            f"/{self._zone}/temperatures?isCooling=false"
        )
        self._request("POST", path, json={
            "new": {"comf": new_comf, "econ": new_econ},
            "old": {"comf": old_comf, "econ": old_econ},
        })

    def set_zone_mode(self, mode: int) -> None:
        """Set zone operation mode."""
        raw = self._get_raw()
        self._require_legacy_write_api()
        if not isinstance(raw, dict):
            raw = {}
        zone = raw.get("zoneData") or {}
        mode_info = zone.get("mode") or {}
        old_mode = mode_info.get("value", MODE_AUTOMATIC)

        path = (
            f"/api/v2/remote/bsbZones/{self._gateway_id}"
            f"/{self._zone}/mode?isCooling=false"
        )
        self._request("POST", path, json={"new": mode, "old": old_mode})

    def set_dhw_temperature(
        self, comfort: float | None = None, reduced: float | None = None
    ) -> None:
        """Set DHW temperatures."""
        raw = self._get_raw()
        self._require_legacy_write_api()
        old_comf = self._current_temperature(raw, "plantData", "dhwComfortTemp")
        old_econ = self._current_temperature(raw, "plantData", "dhwReducedTemp")

        new_comf = comfort if comfort is not None else old_comf
        new_econ = reduced if reduced is not None else old_econ

        path = f"/api/v2/remote/bsbPlantData/{self._gateway_id}/dhwTemp"
        self._request("POST", path, json={
            "new": {"comf": new_comf, "econ": new_econ},
            "old": {"comf": old_comf, "econ": old_econ},
        })

    def set_dhw_mode(self, mode: int) -> None:
        """Set DHW mode: 0=off, 1=on."""
        # No verified write mapping exists for the flat R2 API yet.
        self._get_raw()
        self._require_legacy_write_api()
        path = f"/api/v2/remote/bsbPlantData/{self._gateway_id}/dhwMode"
        self._request("POST", path, json={"new": mode})

    def _require_legacy_write_api(self) -> None:
        """Prevent legacy writes when the gateway only exposes flat R2 data."""
        if self._flat_api:
            raise RemoconDataError(
                "Control is disabled because this gateway uses the unverified flat R2 API"
            )

    # ------------------------------------------------------------------
    # Flat R2 write methods (Aerotop MONO and similar gateways)
    # Based on PR #11 (markuszytek) and PR #13 (Mapfi891) patterns.
    # ------------------------------------------------------------------

    def _get_flat_items_cached(self) -> dict[str, Any]:
        """Fetch current flat R2 items for building write payloads."""
        if not self._flat_api:
            # Ensure we know the protocol before writing
            self._get_raw()
        if not self._flat_api:
            raise RemoconDataError(
                "Flat R2 write requires a flat R2 gateway"
            )
        return self._get_flat_raw(use_cache=False)

    def _flat_features(self) -> dict[str, Any]:
        """Build the gateway-specific feature profile used by R2 writes."""
        return {
            **FLAT_FEATURES_PAYLOAD,
            "gatewayId": self._gateway_id,
            "zones": [{
                "num": int(self._zone),
                "name": "Zone",
                "roomSens": False,
                "geofenceDeroga": True,
                "virtInfo": None,
                "isHidden": False,
            }],
        }

    def _v2_features(self) -> dict[str, Any]:
        """Feature profile required by the v2 dataItems get/set API."""
        zone = int(self._zone)
        return {
            **FEATURES_PAYLOAD,
            "gatewayId": self._gateway_id,
            "zones": [{
                "num": zone,
                "name": "",
                "roomSens": False,
                "geofenceDeroga": False,
                "virtInfo": None,
                "isHidden": False,
            }],
        }

    def _get_v2_data_items(self, item_ids: list[str], zone: int) -> dict[str, Any]:
        """Read current v2 data items uncached (get uses `zn` per item)."""
        path = f"/api/v2/remote/dataItems/{self._gateway_id}/get?umsys=si"
        payload = {
            "useCache": False,
            "items": [{"id": item_id, "zn": zone} for item_id in item_ids],
            "features": self._v2_features(),
            "culture": "de",
        }
        data = self._request("POST", path, json=payload)
        if not isinstance(data, dict):
            raise RemoconDataError("Invalid v2 dataItems response")
        return {
            str(item["id"]): item
            for item in data.get("items", [])
            if isinstance(item, dict) and item.get("id") is not None
        }

    def _set_v2_data_items(self, writes: list[dict[str, Any]]) -> None:
        """Write v2 data items (set uses `zone` per item and needs prevValue)."""
        path = f"/api/v2/remote/dataItems/{self._gateway_id}/set?umsys=si"
        response = self._request(
            "POST",
            path,
            json={"items": writes, "features": self._v2_features()},
        )
        if isinstance(response, dict) and response.get("success") is False:
            raise RemoconDataError("Remocon-Net rejected the write request")

    def _set_flat_data_item(
        self,
        item_id: str,
        value: Any,
        zone: int,
        *,
        temperature_name: str | None = None,
    ) -> None:
        """Write one item on flat-R2 gateways via the validated v2 dataItems API.

        /R2/PlantZone/Save and /R2/PlantMenu/Save do not exist for GALEVO
        family gateways (404); the v2 dataItems set endpoint is the
        real-device-validated write path (upstream issue #22).
        """
        current = self._get_v2_data_items([item_id], zone)
        item = current.get(item_id)
        if not isinstance(item, dict):
            raise RemoconDataError(
                f"Item {item_id} is not available on this gateway"
            )
        if temperature_name is not None:
            value = self._validate_temperature(
                value,
                float(item.get("min") or 0),
                float(item.get("max") or 99),
                float(item.get("step") or 0.5),
                temperature_name,
            )
        self._set_v2_data_items([{
            "id": item_id,
            "prevValue": item.get("value"),
            "value": value,
            "zone": zone,
        }])
        self._confirm_v2_write(item_id, value, zone)

    def _confirm_v2_write(
        self, item_id: str, expected_value: Any, zone: int, max_retries: int = 3
    ) -> None:
        """Confirm a v2 write with an uncached v2 re-read."""
        import time

        _LOGGER.debug("Confirming v2 write %s=%s (zone %s)", item_id, expected_value, zone)
        for attempt in range(max_retries):
            try:
                current = self._get_v2_data_items([item_id], zone)
                actual = (current.get(item_id) or {}).get("value")
                _LOGGER.debug(
                    "V2 confirmation attempt %d: %s present=%s actual=%s expected=%s",
                    attempt + 1, item_id, item_id in current, actual, expected_value,
                )
                try:
                    if float(str(actual)) == float(str(expected_value)):
                        _LOGGER.debug("Write confirmed: %s = %s", item_id, actual)
                        return
                except (TypeError, ValueError):
                    if str(actual) == str(expected_value):
                        _LOGGER.debug("Write confirmed: %s = %s", item_id, actual)
                        return
                _LOGGER.warning(
                    "Write not yet confirmed (attempt %d): %s = %s, expected %s",
                    attempt + 1, item_id, actual, expected_value,
                )
            except (RemoconConnectionError, RemoconDataError) as err:
                _LOGGER.debug(
                    "Confirmation read failed (attempt %d): %s", attempt + 1, err
                )
            time.sleep(1)
        raise RemoconDataError(
            f"Write to {item_id} could not be confirmed after {max_retries} attempts "
            f"(expected {expected_value})"
        )

    def _build_dhw_save_payload(
        self, items: dict[str, Any], item_id: str, new_value: Any
    ) -> dict[str, Any]:
        """Build the verified PlantDhw Save contract used by the web app."""
        missing = [
            required_id
            for required_id in DHW_WRITE_REQUIRED_ITEM_IDS
            if required_id not in items
        ]
        if missing:
            raise RemoconDataError(
                f"DHW write prerequisites are missing: {', '.join(missing)}"
            )
        if item_id not in items:
            raise RemoconDataError(
                f"DHW item is not available on this gateway: {item_id}"
            )

        write_item_ids = tuple(
            current_id for current_id in DHW_WRITE_ITEM_IDS if current_id in items
        )

        previous_items: list[dict[str, Any]] = []
        request_items: list[dict[str, Any]] = []
        for current_id in write_item_ids:
            source = items[current_id]
            previous_items.append({
                "id": current_id,
                "gatewayId": self._gateway_id,
                "value": source.get("value"),
                "zone": source.get("zone", 0),
                "kind": source.get("kind"),
                "unit": source.get("unit"),
                "min": source.get("min"),
                "max": source.get("max"),
                "step": source.get("step"),
                "decimals": source.get("decimals"),
                "options": source.get("options") or [],
                "optTexts": source.get("optTexts") or [],
                "readOnly": bool(source.get("readOnly")),
                "error": bool(source.get("error")),
                "invalid": bool(source.get("invalid")),
                "expiresOn": source.get("expiresOn"),
            })
            request_items.append({
                "itemId": current_id,
                "value": new_value if current_id == item_id else source.get("value"),
            })

        return {
            "requestItems": request_items,
            "prevDataItems": previous_items,
            "features": self._flat_features(),
        }

    @staticmethod
    def _build_save_payload(
        items: dict[str, Any],
        item_id: str,
        new_value: Any,
        write_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a PlantDhw/PlantZone Save payload preserving sibling items.

        Constructs requestItems with the changed item and prevDataItems with
        all sibling items from the same group, as the R2 web app does.
        """
        target = items.get(item_id, {})
        if not isinstance(target, dict):
            target = {}

        # Build requestItems: the item being changed
        request_item = dict(target)
        request_item["value"] = new_value
        if write_id is not None:
            request_item["writeId"] = write_id

        # Build prevDataItems: all sibling items in the same group
        prev_items = []
        for iid, item in items.items():
            if not isinstance(item, dict):
                continue
            item_copy = dict(item)
            item_copy["value"] = item.get("value")
            prev_items.append(item_copy)

        return {
            "requestItems": [request_item],
            "prevDataItems": prev_items,
        }

    def _flat_save(
        self,
        section: str,
        items: dict[str, Any],
        item_id: str,
        new_value: Any,
        write_id: str | None = None,
    ) -> None:
        """POST to /R2/{section}/Save/{gw} with requestItems/prevDataItems."""
        if section == "PlantDhw":
            payload = self._build_dhw_save_payload(items, item_id, new_value)
        else:
            payload = self._build_save_payload(items, item_id, new_value, write_id)
        path = f"/R2/{section}/Save/{self._gateway_id}"
        response = self._request("POST", path, json=payload)
        if isinstance(response, dict) and not response.get("ok", True):
            raise RemoconDataError("Remocon-Net rejected the write request")

    def _validate_temperature(
        self, value: float, min_temp: float, max_temp: float, step: float, name: str
    ) -> float:
        """Validate a temperature value against min/max/step constraints."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RemoconDataError(f"{name} must be a number, got {type(value).__name__}")
        value = float(value)
        if value < min_temp or value > max_temp:
            raise RemoconDataError(
                f"{name} {value} out of range [{min_temp}, {max_temp}]"
            )
        steps = round((value - min_temp) / step)
        if abs(value - (min_temp + steps * step)) > 1e-9:
            raise RemoconDataError(f"{name} must use increments of {step}")
        return round(value, 2)

    def set_dhw_comfort_temperature(self, value: float) -> None:
        """Set DHW comfort temperature (35-65°C, whole degrees)."""
        if not self._flat_api:
            self.set_dhw_temperature(comfort=value)
            return
        items = self._get_flat_items_cached()
        item = items.get("DhwTimeProgComfortTemp", {})
        min_t = float(item.get("min", 35)) if isinstance(item, dict) else 35.0
        max_t = float(item.get("max", 65)) if isinstance(item, dict) else 65.0
        step_t = float(item.get("step", 1)) if isinstance(item, dict) else 1.0

        validated = self._validate_temperature(value, min_t, max_t, step_t, "DHW comfort temperature")
        self._flat_save("PlantDhw", items, "DhwTimeProgComfortTemp", validated)
        # Confirm with uncached re-read
        self._confirm_write("DhwTimeProgComfortTemp", validated)

    def set_dhw_reduced_temperature(self, value: float) -> None:
        """Set DHW reduced (economy) temperature (30-55°C, device-reported step)."""
        if not self._flat_api:
            self.set_dhw_temperature(reduced=value)
            return
        items = self._get_flat_items_cached()
        item = items.get("DhwTimeProgEconomyTemp", {})
        min_t = float(item.get("min", 30)) if isinstance(item, dict) else 30.0
        max_t = float(item.get("max", 55)) if isinstance(item, dict) else 55.0
        step_t = float(item.get("step", 1)) if isinstance(item, dict) else 1.0

        validated = self._validate_temperature(value, min_t, max_t, step_t, "DHW reduced temperature")
        self._flat_save("PlantDhw", items, "DhwTimeProgEconomyTemp", validated)
        self._confirm_write("DhwTimeProgEconomyTemp", validated)

    def set_dhw_mode(self, mode: int) -> None:
        """Set DHW mode: 0=disabled, 1=timed, 2=continuous."""
        if mode not in (0, 1, 2):
            raise RemoconDataError(f"Invalid DHW mode: {mode}. Must be 0, 1, or 2.")
        if not self._flat_api:
            self._get_raw()
            self._require_legacy_write_api()
            path = f"/api/v2/remote/bsbPlantData/{self._gateway_id}/dhwMode"
            self._request("POST", path, json={"new": mode})
            return
        items = self._get_flat_items_cached()
        self._flat_save("PlantDhw", items, "DhwMode", mode)
        self._confirm_write("DhwMode", mode)

    def set_dhw_boost(self, enable: bool) -> None:
        """Enable or disable DHW boost (rapid heating)."""
        items = self._get_flat_items_cached()
        value = 1 if enable else 0
        self._flat_save("PlantDhw", items, "IsDhwBoost", value)
        self._confirm_write("IsDhwBoost", value)

    def set_zone_comfort_temperature(self, value: float) -> None:
        """Set zone comfort temperature."""
        if not self._flat_api:
            self.set_zone_temperatures(comfort=value)
            return
        self._set_flat_data_item(
            "ZoneComfortTemp",
            value,
            int(self._zone),
            temperature_name="Zone comfort temperature",
        )

    def set_zone_reduced_temperature(self, value: float) -> None:
        """Set zone reduced (economy) temperature."""
        if not self._flat_api:
            self.set_zone_temperatures(reduced=value)
            return
        zone = int(self._zone)
        current = self._get_v2_data_items(
            ["ZoneComfortTemp", "ZoneEconomyTemp"], zone
        )
        new_reduced = self._validate_temperature(
            value,
            float((current.get("ZoneEconomyTemp") or {}).get("min") or 5),
            float((current.get("ZoneEconomyTemp") or {}).get("max") or 25),
            float((current.get("ZoneEconomyTemp") or {}).get("step") or 0.5),
            "Zone reduced temperature",
        )
        comfort = (current.get("ZoneComfortTemp") or {}).get("value")
        if comfort is not None:
            try:
                if float(new_reduced) > float(comfort):
                    raise RemoconDataError(
                        f"Reduced ({new_reduced}) must not exceed comfort ({comfort})"
                    )
            except (TypeError, ValueError):
                pass
        item = current.get("ZoneEconomyTemp")
        if not isinstance(item, dict):
            raise RemoconDataError("ZoneEconomyTemp is not available on this gateway")
        self._set_v2_data_items([{
            "id": "ZoneEconomyTemp",
            "prevValue": item.get("value"),
            "value": new_reduced,
            "zone": zone,
        }])
        self._confirm_v2_write("ZoneEconomyTemp", new_reduced, zone)

    def set_zone_mode(self, mode: int) -> None:
        """Set zone mode: 0=off, 2=manual, 3=schedule (time program)."""
        if self._flat_api:
            if mode not in (0, 2, 3):
                raise RemoconDataError(f"Invalid zone mode for flat R2: {mode}. Must be 0, 2, or 3.")
            self._set_flat_data_item("ZoneMode", mode, int(self._zone))
            return
        # Legacy API path
        raw = self._get_raw()
        if not isinstance(raw, dict):
            raw = {}
        zone = raw.get("zoneData") or {}
        mode_info = zone.get("mode") or {}
        old_mode = mode_info.get("value", MODE_AUTOMATIC)

        path = (
            f"/api/v2/remote/bsbZones/{self._gateway_id}"
            f"/{self._zone}/mode?isCooling=false"
        )
        self._request("POST", path, json={"new": mode, "old": old_mode})

    def set_plant_mode(self, mode: int) -> None:
        """Set plant mode: 0=summer, 1=winter, 2=heating, 3=cooling, 5=off."""
        if mode not in (0, 1, 2, 3, 5):
            raise RemoconDataError(
                f"Invalid plant mode: {mode}. Must be 0, 1, 2, 3, or 5."
            )
        if not self._flat_api:
            # Legacy path: plant-level items also go through v2 dataItems.
            self._set_flat_data_item("PlantMode", mode, 0)
            return
        self._set_flat_data_item("PlantMode", mode, 0)

    def _get_advanced_settings(self) -> dict[str, Any]:
        """Read the advanced-settings items (IsQuite, QuietModeStart/End)."""
        path = f"/R2/PlantAdvancedSettings/Refresh/{self._gateway_id}"
        response = self._request(
            "POST",
            path,
            headers=AJAX_HEADERS,
            json={"features": self._flat_features()},
        )
        if not isinstance(response, dict) or not response.get("ok", True):
            raise RemoconDataError("Advanced settings endpoint returned invalid data")
        data = response.get("data", {}) if isinstance(response, dict) else {}
        items = data.get("dataItems", []) if isinstance(data, dict) else []
        return {
            str(item["id"]): item
            for item in items
            if isinstance(item, dict) and item.get("id") is not None
        }

    def set_quiet_mode(self, enable: bool) -> None:
        """Enable or disable quiet mode via PlantAdvancedSettings/Save.

        Matches the captured web-app request exactly: read the advanced
        settings, then POST features + requestItems + prevDataItems with
        AJAX headers. IsQuite, QuietModeStart and QuietModeEnd are always
        sent together.
        """
        value = 1 if enable else 0
        current = self._get_advanced_settings()
        missing = [item_id for item_id in QUIET_MODE_ITEM_IDS if item_id not in current]
        if missing:
            raise RemoconDataError(
                f"Quiet-mode prerequisites are missing: {', '.join(missing)}"
            )

        request_items = []
        prev_items = []
        for item_id in QUIET_MODE_ITEM_IDS:
            source = current[item_id]
            prev_items.append({
                "gatewayId": self._gateway_id,
                "zone": source.get("zone", 0),
                "id": item_id,
                "kind": source.get("kind"),
                "min": source.get("min"),
                "max": source.get("max"),
                "step": source.get("step"),
                "value": source.get("value"),
                "decimals": source.get("decimals"),
                "options": source.get("options") or [],
                "optTexts": source.get("optTexts") or [],
                "readOnly": bool(source.get("readOnly")),
                "error": bool(source.get("error")),
                "invalid": bool(source.get("invalid")),
                "expiresOn": source.get("expiresOn"),
                "unit": source.get("unit"),
            })
            request_items.append({
                "itemId": item_id,
                "value": value if item_id == "IsQuite" else source.get("value"),
            })

        path = f"/R2/PlantAdvancedSettings/Save/{self._gateway_id}"
        response = self._request(
            "POST",
            path,
            headers=AJAX_HEADERS,
            json={
                "features": self._flat_features(),
                "requestItems": request_items,
                "prevDataItems": prev_items,
            },
        )
        if isinstance(response, dict) and not response.get("ok", True):
            raise RemoconDataError("Remocon-Net rejected the quiet-mode write")
        self._confirm_write("IsQuite", value)

    def _confirm_write(self, item_id: str, expected_value: Any, max_retries: int = 3) -> None:
        """Confirm a write by re-reading the item with cache disabled.

        Uses useCache=False to bypass the server-side cache and get the
        actual current value from the gateway.
        """
        for attempt in range(max_retries):
            try:
                # Force uncached read
                response = self._request(
                    "POST",
                    f"/R2/PlantHome/GetData/{self._gateway_id}",
                    json={
                        "useCache": False,
                        "zone": int(self._zone),
                        "filter": {
                            "notEssentials": True,
                            "plant": True,
                            "zone": True,
                            "dhw": True,
                            "progId": 9,
                        },
                        "features": {
                            **FLAT_FEATURES_PAYLOAD,
                            "gatewayId": self._gateway_id,
                            "zones": [{
                                "num": int(self._zone), "name": "Zone",
                                "roomSens": False, "geofenceDeroga": True,
                                "virtInfo": None, "isHidden": False,
                            }],
                        },
                    },
                )
                if isinstance(response, dict):
                    data = response.get("data", {})
                    items_list = data.get("items", []) if isinstance(data, dict) else []
                    for item in items_list:
                        if isinstance(item, dict) and item.get("id") == item_id:
                            actual = item.get("value")
                            _LOGGER.debug(
                                "Flat confirmation attempt %d: %s actual=%s expected=%s",
                                attempt + 1, item_id, actual, expected_value,
                            )
                            # Compare loosely (int vs float, str vs int)
                            try:
                                if float(str(actual)) == float(str(expected_value)):
                                    _LOGGER.debug(
                                        "Write confirmed: %s = %s", item_id, actual
                                    )
                                    return
                            except (TypeError, ValueError):
                                if str(actual) == str(expected_value):
                                    _LOGGER.debug(
                                        "Write confirmed: %s = %s", item_id, actual
                                    )
                                    return
                            _LOGGER.warning(
                                "Write not yet confirmed (attempt %d): %s = %s, expected %s",
                                attempt + 1, item_id, actual, expected_value,
                            )
                            break
            except (RemoconConnectionError, RemoconDataError) as err:
                _LOGGER.debug("Confirmation read failed (attempt %d): %s", attempt + 1, err)
            # Small delay before retry
            import time
            time.sleep(1)

        raise RemoconDataError(
            f"Write to {item_id} could not be confirmed after {max_retries} attempts "
            f"(expected {expected_value})"
        )

    def reauth(self) -> None:
        """Force re-authentication."""
        self._session = None
        self.login()
