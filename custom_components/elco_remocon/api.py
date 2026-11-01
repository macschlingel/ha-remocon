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
}

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

    def _get_flat_raw(self) -> dict[str, Any]:
        """Fetch the flat R2 data format, caching the protocol after success."""
        essentials = self._get_flat_item_call(
            {"notEssentials": False, "plant": True, "zone": True, "dhw": True}
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
            }))
        except (RemoconConnectionError, RemoconDataError) as err:
            # Status-only items are useful but optional. In particular, an
            # outage here must not prevent initial setup or essential polling.
            _LOGGER.debug("Could not fetch optional flat R2 status items: %s", err)
        self._log_api_variant("flat")
        return items

    def _get_flat_item_call(self, filter_data: dict[str, Any]) -> dict[str, Any]:
        """Fetch and index one group of flat R2 data items."""
        response = self._request(
            "POST",
            f"/R2/PlantHome/GetData/{self._gateway_id}",
            api_variant="flat",
            json={
                "useCache": True,
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
        return RemoconData(
            comfort_temp=number("ZoneComfortTemp"),
            comfort_temp_min=number("ZoneComfortTemp", 5.0, "min"),
            comfort_temp_max=number("ZoneComfortTemp", 35.0, "max"),
            comfort_temp_step=number("ZoneComfortTemp", 0.5, "step"),
            reduced_temp=number("ZoneEconomyTemp"),
            desired_temp=number("ZoneDesiredTemp"),
            room_temp=number("ZoneMeasuredTemp"),
            zone_mode=mode,
            zone_mode_texts=[str(x) for x in opts] if isinstance(opts, list) else [],
            heating_active=heating_active,
            cooling_active=cooling_active,
            heat_or_cool_request=_optional_bool(request_value),
            outside_temp=number("OutsideTemp"),
            dhw_temp=number("DhwStorageTemperature"),
            dhw_comfort_temp=_optional_temperature(value("DhwTimeProgComfortTemp")),
            dhw_reduced_temp=_optional_temperature(value("DhwTimeProgEconomyTemp")),
            dhw_mode=int(number("DhwMode")),
            dhw_enabled=bool(int(number("DhwMode"))),
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

    def reauth(self) -> None:
        """Force re-authentication."""
        self._session = None
        self.login()
