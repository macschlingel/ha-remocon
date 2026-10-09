"""API client for the Elco Remocon-Net cloud service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
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
    heating_active: bool = False
    cooling_active: bool = False
    heat_or_cool_request: bool = False
    # Plant
    outside_temp: float = 0.0
    dhw_temp: float = 0.0
    dhw_comfort_temp: float = 0.0
    dhw_reduced_temp: float = 0.0
    dhw_mode: int = 0
    dhw_enabled: bool = False
    heat_pump_on: bool = False
    flame_sensor: bool = False
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
        self._planthome_prefix: Optional[str] = None

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

    def _get_raw(self) -> dict:
        # Remocon-Net splits systems onto two endpoint families depending on whether
        # they're BSB-bus (e.g. Aerotop/Aquatop heat pumps) or not (e.g. older
        # boilers), and each 500s for the other family's plants. Probe the
        # historically-default non-BSB path first so existing non-BSB setups see no
        # behavior change, fall back to the BSB path on failure, and cache whichever
        # one worked so later calls skip straight to it.
        payload = {
            "useCache": True,
            "zone": int(self._zone),
            "filter": {"notEssentials": False, "plant": True, "zone": True, "dhw": True},
            "features": FEATURES_PAYLOAD,
        }
        prefixes = [self._planthome_prefix] if self._planthome_prefix else \
            ["/R2/PlantHome", "/R2/PlantHomeBsb"]
        first_err: Optional[RemoconConnectionError] = None
        data = None
        for prefix in prefixes:
            path = f"{prefix}/GetData/{self._gateway_id}?umsys=si"
            try:
                data = self._request("POST", path, json=payload)
            except RemoconConnectionError as err:
                if first_err is None:
                    first_err = err
                if self._planthome_prefix == prefix:
                    # The cached prefix started failing; drop it so the next call
                    # re-probes instead of staying pinned to a bad endpoint.
                    self._planthome_prefix = None
                continue
            self._planthome_prefix = prefix
            break
        else:
            raise first_err

        if not data:
            raise RemoconDataError("Empty data received from API")
        if isinstance(data, dict) and not data.get("ok", True):
            _LOGGER.error("API returned error: %s", data)
            raise RemoconDataError(data.get("message", "API returned error"))
        return data.get("data", data) if isinstance(data, dict) else data

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
            heat_or_cool_request=bool(zone.get("heatOrCoolRequest", 0)),
            outside_temp=float(plant.get("outsideTemp", 0)),
            dhw_temp=float(plant.get("dhwStorageTemp", 0)),
            dhw_comfort_temp=float(dhw_comfort.get("value", 0)),
            dhw_reduced_temp=float(dhw_reduced.get("value", 0)),
            dhw_mode=dhw_mode_info.get("value", 0),
            dhw_enabled=bool(plant.get("dhwEnabled", 0)),
            heat_pump_on=bool(plant.get("heatPumpOn", 0)),
            flame_sensor=bool(plant.get("flameSensor", 0)),
            system_pressure=float(pressure) if pressure is not None else None,
            flow_temperature=float(flow) if flow is not None else None,
            has_room_sensor=bool(zone.get("hasRoomSensor", 0)),
        )

    def set_zone_temperatures(
        self, comfort: float | None = None, reduced: float | None = None
    ) -> None:
        """Set comfort and/or reduced temperature."""
        raw = self._get_raw()
        if not isinstance(raw, dict):
            raw = {}
        zone = raw.get("zoneData") or {}
        ch_comf = zone.get("chComfortTemp") or {}
        ch_red = zone.get("chReducedTemp") or {}
        old_comf = float(ch_comf.get("value", 0))
        old_econ = float(ch_red.get("value", 0))

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
        if not isinstance(raw, dict):
            raw = {}
        plant = raw.get("plantData") or {}
        dhw_comf = plant.get("dhwComfortTemp") or {}
        dhw_red = plant.get("dhwReducedTemp") or {}
        old_comf = float(dhw_comf.get("value", 0))
        old_econ = float(dhw_red.get("value", 0))

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
