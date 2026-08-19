"""API client for the Elco Remocon-Net cloud service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional
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

FEATURES_PAYLOAD = {
    "gatewayId": "",
    "zones": [{"num": 1, "name": "", "roomSens": False, "geofenceDeroga": False,
               "virtInfo": None, "isHidden": False}],
    "solar": False, "convBoiler": False, "commBoiler": False, "hpSys": True,
    "hybridSys": False, "cascadeSys": False, "dhwProgSupported": True,
    "virtualZones": False, "hasVmc": False, "extendedTimeProg": False,
    "hasBoiler": False, "pilotSupported": True, "isVmcR2": False,
    "isEvo2": False, "dhwHidden": False, "dhwBoilerPresent": True,
    "dhwModeChangeable": True, "hvInputOff": False, "autoThermoReg": True,
    "hasMetering": True, "hasFireplace": False, "hasSlp": False,
    "hasEm20": True, "hasEm30": False, "systemServices": None,
    "hasTwoCoolingTemp": True, "bmsActive": False, "hpCascadeSys": False,
    "hpCascadeSysPcm5": False, "hpCascadeConfig": -1, "bufferTimeProgAvailable": True,
    "distinctHeatCoolSetpoints": True, "hasZoneNames": True,
    "zoneManagerStandAlone": False, "hydraulicScheme": 5,
    "preHeatingSupported": False, "hasGahp": False, "zigbeeActive": False,
    "hasSlpAloneOnBus": False, "isSlpCascade": False,
    "hasZeroColdWaterProg": False, "weatherProvider": 0,
    "hasDhwTimeProgTemperatures": 1, "isGSWHCommercialAloneOnBus": False,
}


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
    cooling_comfort_temp: float = 0.0
    cooling_reduced_temp: float = 0.0
    desired_temp: float = 0.0
    room_temp: float = 0.0
    zone_mode: int = MODE_AUTOMATIC
    zone_mode_texts: list[str] = field(default_factory=list)
    heating_active: bool = False
    cooling_active: bool = False
    heat_or_cool_request: bool = False
    zone_deroga: float = 0.0
    # Plant
    outside_temp: float = 0.0
    plant_mode: int = 0
    plant_mode_text: str = "Unknown"
    automatic_thermoregulation: bool = False
    holiday: bool = False
    dhw_temp: float = 0.0
    dhw_target_temp: float = 0.0
    dhw_comfort_temp: float = 0.0
    dhw_reduced_temp: float = 0.0
    dhw_mode: int = 0
    dhw_enabled: bool = False
    heat_pump_on: bool = False
    flame_sensor: bool = False
    # System
    system_pressure: Optional[float] = None
    flow_setpoint_temperature: Optional[float] = None
    zone_pilot_on: bool = False
    # Meta
    has_room_sensor: bool = False
    plant_address: str | None = None
    appliance_model: str | None = None
    gateway_online: bool = False
    gateway_status: str | None = None
    quiet_mode_start: str | None = None
    quiet_mode_end: str | None = None
    quiet_mode_active: bool = False


class RemoconClient:
    """Synchronous API client for Elco Remocon-Net."""

    def __init__(self, email: str, password: str, gateway_id: str, zone: str = "1") -> None:
        self._email = email
        self._password = password
        self._gateway_id = gateway_id
        self._zone = zone
        self._session: Optional[requests.Session] = None

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
            resp = s.post(url, headers=headers, data=payload, timeout=15)
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

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        s = self._get_session()
        url = f"{BASE_URL}{path}"
        kwargs.setdefault("timeout", 15)
        try:
            resp = s.request(method, url, **kwargs)
            if resp.status_code in (401, 403):
                raise RemoconAuthError("Session expired")
            resp.raise_for_status()
        except requests.RequestException as err:
            err_msg = str(err)
            if getattr(err, "response", None) is not None:
                err_msg += f" - Response: {err.response.text}"
            _LOGGER.error("API Request failed: %s", err_msg)
            raise RemoconConnectionError(err_msg) from err
        
        try:
            return resp.json()
        except ValueError as err:
            _LOGGER.error("Invalid JSON response from API: %s", resp.text)
            raise RemoconDataError("Could not parse API response") from err

    def _features(self) -> dict[str, Any]:
        """Return a gateway-specific copy of the web UI feature profile."""
        features = dict(FEATURES_PAYLOAD)
        features["gatewayId"] = self._gateway_id
        return features

    def _get_raw(self) -> dict:
        path = f"/R2/PlantHome/GetData/{self._gateway_id}?umsys=si"
        payload = {
            "useCache": True,
            "zone": int(self._zone),
            "filter": {"notEssentials": False, "plant": True, "zone": True, "dhw": True},
            "features": self._features(),
        }
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Ajax-Request": "json",
            "X-Requested-With": "XMLHttpRequest",
        }
        data = self._request("POST", path, headers=headers, json=payload)
        if not data:
            raise RemoconDataError("Empty data received from API")
        if isinstance(data, dict) and not data.get("ok", True):
            _LOGGER.error("API returned error: %s", data)
            raise RemoconDataError(data.get("message", "API returned error"))
        raw = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(raw, dict):
            raise RemoconDataError(f"Unexpected data format from API: {type(raw)}")

        _LOGGER.debug(
            "Plant data response for gateway %s: top-level keys=%s, data keys=%s",
            self._gateway_id,
            sorted(data) if isinstance(data, dict) else type(data).__name__,
            sorted(raw),
        )
        if "items" not in raw and (
            "plantData" not in raw or "zoneData" not in raw
        ):
            raise RemoconDataError(
                "API response does not contain items or plantData/zoneData "
                f"(keys: {sorted(raw)})"
            )
        return raw

    def get_data(self) -> RemoconData:
        """Fetch all data and return a RemoconData object."""
        raw = self._get_raw()
        try:
            header = self._get_header()
        except RemoconApiError as err:
            _LOGGER.debug("Could not fetch plant header: %s", err)
            header = {}
        try:
            advanced = self._get_advanced_settings(raw.get("features", FEATURES_PAYLOAD))
        except RemoconApiError as err:
            _LOGGER.debug("Could not fetch advanced settings: %s", err)
            advanced = {}
        if "items" in raw:
            data = self._parse_items(raw["items"])
            data = self._add_header_data(data, header)
            return self._add_advanced_data(data, advanced)

        plant = raw.get("plantData") or {}
        zone = raw.get("zoneData") or {}

        ch_comfort = zone.get("chComfortTemp") or {}
        ch_reduced = zone.get("chReducedTemp") or {}
        mode_info = zone.get("mode") or {}

        dhw_comfort = plant.get("dhwComfortTemp") or {}
        dhw_reduced = plant.get("dhwReducedTemp") or {}
        dhw_mode_info = plant.get("dhwMode") or {}

        data = RemoconData(
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
            heat_or_cool_request=bool(zone.get("heatOrCoolRequest", 0)),
            outside_temp=float(plant.get("outsideTemp", 0)),
            dhw_temp=float(plant.get("dhwStorageTemp", 0)),
            dhw_comfort_temp=float(dhw_comfort.get("value", 0)),
            dhw_reduced_temp=float(dhw_reduced.get("value", 0)),
            dhw_mode=dhw_mode_info.get("value", 0),
            dhw_enabled=bool(plant.get("dhwEnabled", 0)),
            heat_pump_on=bool(plant.get("heatPumpOn", 0)),
            flame_sensor=bool(plant.get("flameSensor", 0)),
            system_pressure=None,
            has_room_sensor=bool(zone.get("hasRoomSensor", 0)),
        )
        data = self._add_header_data(data, header)
        return self._add_advanced_data(data, advanced)

    def _get_header(self) -> dict[str, Any]:
        """Fetch plant identity and gateway status shown in the web UI."""
        path = f"/R2/Plant/PlantHeader/{self._gateway_id}?rnd=0"
        data = self._request("GET", path)
        return data.get("data", {}) if isinstance(data, dict) else {}

    def _get_advanced_settings(self, features: dict[str, Any]) -> dict[str, Any]:
        """Fetch advanced settings using the same AJAX contract as the web UI."""
        path = f"/R2/PlantAdvancedSettings/Refresh/{self._gateway_id}"
        advanced_features = dict(features)
        advanced_features["gatewayId"] = self._gateway_id
        response = self._request(
            "POST",
            path,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Ajax-Request": "json",
                "X-Requested-With": "XMLHttpRequest",
            },
            json={"features": advanced_features},
        )
        data = response.get("data", {}) if isinstance(response, dict) else {}
        return {item["id"]: item for item in data.get("dataItems", []) if item.get("id")}

    @staticmethod
    def _add_header_data(data: RemoconData, header: dict[str, Any]) -> RemoconData:
        """Add web UI plant-header data to the common data model."""
        data.plant_address = header.get("plantAddress")
        data.appliance_model = header.get("applianceModel")
        data.gateway_online = bool(header.get("gwOnline", False))
        data.gateway_status = header.get("errorText")
        return data

    @staticmethod
    def _add_advanced_data(
        data: RemoconData, advanced: dict[str, Any]
    ) -> RemoconData:
        """Add advanced settings returned by the Plant web UI."""
        start = advanced.get("QuietModeStart", {}).get("value")
        end = advanced.get("QuietModeEnd", {}).get("value")
        data.quiet_mode_start = RemoconClient._format_time_value(start)
        data.quiet_mode_end = RemoconClient._format_time_value(end)
        data.quiet_mode_active = bool(advanced.get("IsQuite", {}).get("value", 0))
        return data

    @staticmethod
    def _format_time_value(value: Any) -> str | None:
        """Convert the API's fixed-point time value to HH:MM."""
        if value is None:
            return None
        try:
            total_minutes = round(float(value) / 256 * 60)
            hours, minutes = divmod(total_minutes, 60)
            return f"{hours % 24:02d}:{minutes:02d}"
        except (TypeError, ValueError):
            return None

    def _parse_items(
        self, items: list[dict[str, Any]]
    ) -> RemoconData:
        """Convert the current flat R2 data-item response to RemoconData."""
        values = self._item_values(items)

        def item_value(item_id: str, default: Any = 0) -> Any:
            return values.get(item_id, {}).get("value", default)

        def item_float(item_id: str, default: float = 0.0) -> float:
            try:
                return float(item_value(item_id, default))
            except (TypeError, ValueError):
                return default

        mode_item = values.get("ZoneMode", {})
        mode_texts = mode_item.get("optTexts") or []
        plant_mode_item = values.get("PlantMode", {})
        plant_mode = int(item_float("PlantMode"))
        plant_options = plant_mode_item.get("options") or []
        plant_texts = plant_mode_item.get("optTexts") or []
        plant_mode_text = "Unknown"
        if plant_mode in plant_options:
            plant_mode_text = str(plant_texts[plant_options.index(plant_mode)])
        raw_zone_mode = int(item_float("ZoneMode"))
        zone_mode = {
            0: MODE_PROTECTION,
            2: MODE_COMFORT,
            3: MODE_AUTOMATIC,
        }.get(raw_zone_mode, MODE_AUTOMATIC)
        zone_mode_texts = [str(text) for text in mode_texts]

        return RemoconData(
            comfort_temp=item_float("ZoneComfortTemp"),
            comfort_temp_min=item_float("ZoneComfortTemp", 5.0)
            if "ZoneComfortTemp" not in values
            else float(values["ZoneComfortTemp"].get("min", 5.0)),
            comfort_temp_max=item_float("ZoneComfortTemp", 35.0)
            if "ZoneComfortTemp" not in values
            else float(values["ZoneComfortTemp"].get("max", 35.0)),
            comfort_temp_step=item_float("ZoneComfortTemp", 0.5)
            if "ZoneComfortTemp" not in values
            else float(values["ZoneComfortTemp"].get("step", 0.5)),
            reduced_temp=item_float("ZoneEconomyTemp"),
            cooling_comfort_temp=item_float("ZoneComfortCoolingTemp"),
            cooling_reduced_temp=item_float("ZoneEconomyCoolingTemp"),
            desired_temp=item_float("ZoneDesiredTemp"),
            room_temp=item_float("ZoneMeasuredTemp"),
            zone_mode=zone_mode,
            zone_mode_texts=zone_mode_texts,
            heating_active=False,
            cooling_active=int(item_float("PlantMode")) == 3,
            heat_or_cool_request=False,
            zone_deroga=item_float("ZoneDeroga"),
            outside_temp=item_float("OutsideTemp"),
            plant_mode=plant_mode,
            plant_mode_text=plant_mode_text,
            automatic_thermoregulation=bool(
                item_value("AutomaticThermoregulation", 0)
            ),
            holiday=bool(item_float("Holiday")),
            dhw_temp=item_float("DhwStorageTemperature"),
            dhw_target_temp=item_float("DhwTemp"),
            dhw_comfort_temp=item_float("DhwTimeProgComfortTemp"),
            dhw_reduced_temp=item_float("DhwTimeProgEconomyTemp"),
            dhw_mode=int(item_float("DhwMode")),
            dhw_enabled=int(item_float("DhwMode")) != 0,
            heat_pump_on=int(item_float("PlantMode")) in (1, 2, 3),
            system_pressure=(
                item_float("HeatingCircuitPressure")
                if "HeatingCircuitPressure" in values
                else None
            ),
            flow_setpoint_temperature=item_float("ChFlowSetpointTemp")
            if "ChFlowSetpointTemp" in values
            else None,
            zone_pilot_on=bool(item_value("IsZonePilotOn", 0)),
            has_room_sensor="ZoneMeasuredTemp" in values,
        )

    @staticmethod
    def _item_values(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Index data items by ID."""
        return {item["id"]: item for item in items if item.get("id")}

    @classmethod
    def _raw_item_values(cls, raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Return indexed items when the current response schema is used."""
        items = raw.get("items")
        return cls._item_values(items) if isinstance(items, list) else {}

    def set_zone_temperatures(
        self, comfort: float | None = None, reduced: float | None = None
    ) -> None:
        """Set comfort and/or reduced temperature."""
        raw = self._get_raw()
        items = self._raw_item_values(raw)
        if not isinstance(raw, dict):
            raw = {}
        zone = raw.get("zoneData") or {}
        ch_comf = zone.get("chComfortTemp") or {}
        ch_red = zone.get("chReducedTemp") or {}
        old_comf = float(items.get("ZoneComfortTemp", ch_comf).get("value", 0))
        old_econ = float(items.get("ZoneEconomyTemp", ch_red).get("value", 0))

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
        items = self._raw_item_values(raw)
        if not isinstance(raw, dict):
            raw = {}
        zone = raw.get("zoneData") or {}
        mode_info = zone.get("mode") or {}
        old_mode = mode_info.get("value", MODE_AUTOMATIC)
        if "ZoneMode" in items:
            raw_mode = int(items["ZoneMode"].get("value", 3))
            old_mode = {0: MODE_PROTECTION, 2: MODE_COMFORT, 3: MODE_AUTOMATIC}.get(
                raw_mode, MODE_AUTOMATIC
            )

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
        items = self._raw_item_values(raw)
        if not isinstance(raw, dict):
            raw = {}
        plant = raw.get("plantData") or {}
        dhw_comf = plant.get("dhwComfortTemp") or {}
        dhw_red = plant.get("dhwReducedTemp") or {}
        old_comf = float(
            items.get("DhwTimeProgComfortTemp", dhw_comf).get("value", 0)
        )
        old_econ = float(
            items.get("DhwTimeProgEconomyTemp", dhw_red).get("value", 0)
        )

        new_comf = comfort if comfort is not None else old_comf
        new_econ = reduced if reduced is not None else old_econ

        path = f"/api/v2/remote/bsbPlantData/{self._gateway_id}/dhwTemp"
        self._request("POST", path, json={
            "new": {"comf": new_comf, "econ": new_econ},
            "old": {"comf": old_comf, "econ": old_econ},
        })

    def set_dhw_mode(self, mode: int) -> None:
        """Set DHW mode: 0=off, 1=on."""
        path = f"/api/v2/remote/bsbPlantData/{self._gateway_id}/dhwMode"
        self._request("POST", path, json={"new": mode})

    def reauth(self) -> None:
        """Force re-authentication."""
        self._session = None
        self.login()
