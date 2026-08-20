"""API client for the Elco Remocon-Net cloud service."""

from __future__ import annotations

import logging
import time
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

# Feature flags observed for the Aerotop Split 12.2 M-RX. The R2 endpoint uses
# these flags to decide which data items to return. Gateway and zone are added
# dynamically so installation-specific identifiers never live in source code.
FEATURES_PAYLOAD = {
    "solar": False,
    "convBoiler": False,
    "commBoiler": False,
    "hpSys": True,
    "hybridSys": False,
    "cascadeSys": False,
    "dhwProgSupported": True,
    "virtualZones": False,
    "hasVmc": False,
    "extendedTimeProg": False,
    "hasBoiler": False,
    "pilotSupported": True,
    "isVmcR2": False,
    "isEvo2": False,
    "dhwHidden": False,
    "dhwBoilerPresent": True,
    "dhwModeChangeable": True,
    "hvInputOff": False,
    "autoThermoReg": True,
    "hasMetering": True,
    "hasFireplace": False,
    "hasSlp": False,
    "hasEm20": True,
    "hasEm30": False,
    "systemServices": None,
    "hasTwoCoolingTemp": True,
    "bmsActive": False,
    "hpCascadeSys": False,
    "hpCascadeSysPcm5": False,
    "hpCascadeConfig": -1,
    "bufferTimeProgAvailable": True,
    "distinctHeatCoolSetpoints": True,
    "hasZoneNames": True,
    "hydraulicScheme": 5,
    "preHeatingSupported": False,
    "hasGahp": False,
    "zigbeeActive": False,
    "hasSlpAloneOnBus": False,
    "isSlpCascade": False,
    "hasZeroColdWaterProg": False,
    "weatherProvider": 1,
    "hasDhwTimeProgTemperatures": 1,
    "isGSWHCommercialAloneOnBus": False,
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
    comfort_temp: float | None = None
    comfort_temp_min: float = 5.0
    comfort_temp_max: float = 35.0
    comfort_temp_step: float = 0.5
    reduced_temp: float | None = None
    cooling_comfort_temp: float | None = None
    cooling_reduced_temp: float | None = None
    desired_temp: float | None = None
    room_temp: float | None = None
    zone_mode: int = MODE_AUTOMATIC
    zone_mode_text: str | None = None
    zone_mode_texts: list[str] = field(default_factory=list)
    heating_active: bool = False
    cooling_active: bool = False
    heat_or_cool_request: bool = False
    automatic_thermoregulation: bool = False
    zone_pilot_on: bool = False
    holiday_active: bool = False
    # Plant
    outside_temp: float | None = None
    dhw_temp: float | None = None
    dhw_comfort_temp: float | None = None
    dhw_reduced_temp: float | None = None
    dhw_mode: int = 0
    dhw_mode_text: str | None = None
    dhw_enabled: bool = False
    dhw_target_temp: float | None = None
    heat_pump_on: bool = False
    quiet_mode: bool = False
    dhw_boost: bool = False
    resistor_on: bool = False
    flame_sensor: bool = False
    plant_mode: int | None = None
    plant_mode_text: str | None = None
    # System (from v2 API)
    system_pressure: Optional[float] = None
    flow_temperature: Optional[float] = None
    # Meta
    has_room_sensor: bool = False
    # Slower-changing data, refreshed independently from the main status.
    energy: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    time_programs: dict[str, Any] = field(default_factory=dict)


class RemoconClient:
    """Synchronous API client for Elco Remocon-Net."""

    def __init__(self, email: str, password: str, gateway_id: str, zone: str = "1") -> None:
        self._email = email
        self._password = password
        self._gateway_id = gateway_id
        self._zone = zone
        self._session: Optional[requests.Session] = None
        self._energy_cache: dict[str, Any] = {}
        self._settings_cache: dict[str, Any] = {}
        self._time_program_cache: dict[str, Any] = {}
        self._energy_updated = 0.0
        self._settings_updated = 0.0
        self._time_programs_updated = 0.0

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

    def _request(
        self, method: str, path: str, *, retry_auth: bool = True, **kwargs: Any
    ) -> Any:
        s = self._get_session()
        url = f"{BASE_URL}{path}"
        kwargs.setdefault("timeout", 15)
        try:
            resp = s.request(method, url, **kwargs)
            if resp.status_code in (401, 403):
                if retry_auth:
                    self._session = None
                    self.login()
                    return self._request(
                        method, path, retry_auth=False, **kwargs
                    )
                raise RemoconAuthError("Session expired")
            resp.raise_for_status()
        except RemoconAuthError:
            raise
        except requests.RequestException as err:
            # Response bodies can contain account or installation data. Never log them.
            status = getattr(getattr(err, "response", None), "status_code", None)
            message = f"HTTP {status}" if status is not None else str(err)
            _LOGGER.debug("Remocon-Net request failed: %s", message)
            raise RemoconConnectionError(message) from err
        
        try:
            return resp.json()
        except ValueError as err:
            _LOGGER.debug("Remocon-Net returned invalid JSON")
            raise RemoconDataError("Could not parse API response") from err

    def _features(self) -> dict[str, Any]:
        """Build the feature payload required by the R2 data endpoint."""
        zone = int(self._zone)
        return {
            **FEATURES_PAYLOAD,
            "gatewayId": self._gateway_id,
            "zones": [
                {
                    "num": zone,
                    "name": "Zone",
                    "roomSens": False,
                    "geofenceDeroga": True,
                    "virtInfo": None,
                    "isHidden": False,
                }
            ],
        }

    def _get_item_call(
        self, filter_data: dict[str, Any], *, use_cache: bool = True
    ) -> list[dict[str, Any]]:
        """Fetch one group of flat R2 data items."""
        path = f"/R2/PlantHome/GetData/{self._gateway_id}"
        payload = {
            "useCache": use_cache,
            "zone": int(self._zone),
            "filter": filter_data,
            "features": self._features(),
        }
        response = self._request("POST", path, json=payload)
        if not isinstance(response, dict) or not response.get("ok", True):
            message = response.get("message") if isinstance(response, dict) else None
            raise RemoconDataError(message or "API returned invalid data")
        data = response.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise RemoconDataError("API response contains no data items")
        return [item for item in data["items"] if isinstance(item, dict)]

    def _get_raw(self, *, use_cache: bool = True) -> dict[str, dict[str, Any]]:
        """Fetch essential and status data observed in the Remocon-Net web app."""
        essential = self._get_item_call(
            {"dhw": True, "notEssentials": False, "plant": True, "zone": True},
            use_cache=use_cache,
        )
        status = self._get_item_call(
            {
                "dhw": False,
                "notEssentials": True,
                "plant": False,
                "progId": 9,
                "zone": False,
            },
            use_cache=use_cache,
        )
        items = essential + status
        if not items:
            raise RemoconDataError("Empty data received from API")
        return {
            str(item["id"]): item
            for item in items
            if item.get("id") is not None
        }

    @staticmethod
    def _float(value: Any) -> float | None:
        """Convert an API value without turning missing data into zero."""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _get_energy_data(self) -> dict[str, Any]:
        """Fetch consumption and produced-energy series from metering."""
        path = f"/R2/PlantMetering/GetData/{self._gateway_id}"
        response = self._request(
            "POST",
            path,
            json={"features": self._features(), "hasCooling": True},
        )
        if not isinstance(response, dict) or not response.get("ok", True):
            raise RemoconDataError("Metering endpoint returned invalid data")
        data = response.get("data", {}) if isinstance(response, dict) else {}
        raw = data.get("asKwhRaw", {}) if isinstance(data, dict) else {}
        histogram = raw.get("histogramData", []) if isinstance(raw, dict) else []
        donut = raw.get("donutData", []) if isinstance(raw, dict) else []
        result: dict[str, Any] = {}

        for row in histogram:
            if not isinstance(row, dict):
                continue
            period = row.get("period")
            tab = row.get("tab")
            series = row.get("series")
            if period not in {"CurrentMonth", "CurrentYear"}:
                continue
            if tab not in {"ConsumedElectricity", "ProducedEnergy"}:
                continue
            if series not in {"Heating", "Cooling", "Dhw"}:
                continue
            values = [
                self._float(point.get("y")) or 0.0
                for point in row.get("items", [])
                if isinstance(point, dict)
            ]
            prefix = "consumed" if tab == "ConsumedElectricity" else "produced"
            period_key = "current_month" if period == "CurrentMonth" else "current_year"
            series_key = str(series).lower()
            result[f"{prefix}_{period_key}_{series_key}"] = round(sum(values), 3)
            if period == "CurrentYear":
                result[f"{prefix}_monthly_{series_key}"] = values

        # The donut is the web app's authoritative current-month consumption.
        for row in donut:
            if not isinstance(row, dict) or row.get("period") != "CurrentMonth":
                continue
            if row.get("tab") != "ConsumedElectricity":
                continue
            series = row.get("series")
            if series in {"Heating", "Cooling", "Dhw"}:
                value = self._float(row.get("value"))
                if value is not None:
                    result[f"consumed_current_month_{str(series).lower()}"] = value

        for prefix in ("consumed", "produced"):
            for period in ("current_month", "current_year"):
                parts = [
                    result.get(f"{prefix}_{period}_{series}")
                    for series in ("heating", "cooling", "dhw")
                ]
                if any(value is not None for value in parts):
                    result[f"{prefix}_{period}_total"] = round(
                        sum(value or 0.0 for value in parts), 3
                    )
        return result

    def _get_settings_data(self) -> dict[str, Any]:
        """Fetch a curated set of useful advanced-menu values in one call."""
        ids = (
            "U6_3_3,U6_3_5_0_0,U6_3_5_0_1,U6_3_5_0_2,"
            "U6_9_5_0,U6_9_5_1,U6_10_0,U6_10_1,U6_10_2,U6_10_3,"
            "U6_10_4,U6_10_5,U6_13_0,U6_13_1,U6_13_2,U6_15_0_1,"
            "U6_15_2,U6_16_5,U6_16_6,U6_16_7"
        )
        response = self._request(
            "GET",
            "/R2/PlantMenu/Refresh",
            params={
                "id": self._gateway_id,
                "paramIds": ids,
                "caller": "{2}",
            },
        )
        if not isinstance(response, dict) or not response.get("ok", True):
            raise RemoconDataError("Advanced settings endpoint returned invalid data")
        data = response.get("data", []) if isinstance(response, dict) else []
        return {
            str(item.get("fullIdentifier") or item.get("id")): item.get("value")
            for item in data
            if isinstance(item, dict) and (item.get("fullIdentifier") or item.get("id"))
        }

    def _get_time_programs(self) -> dict[str, Any]:
        """Fetch zone, DHW and buffer weekly plans."""
        result: dict[str, Any] = {}
        programs = {1: "zone", 7: "dhw", 15: "buffer"}
        for program_id, name in programs.items():
            response = self._request(
                "POST",
                f"/R2/PlantTimeProg/GetData/{self._gateway_id}?umsys=si",
                json={
                    "useCache": True,
                    "progId": program_id,
                    "filters": {
                        "automaticThermoregulation": False,
                        "temperatures": True,
                        "timeProg": True,
                    },
                    "features": self._features(),
                },
            )
            if not isinstance(response, dict) or not response.get("ok", True):
                raise RemoconDataError(
                    f"Time-program endpoint returned invalid data for {program_id}"
                )
            data = response.get("data", {}) if isinstance(response, dict) else {}
            time_program = data.get("timeProg", {}) if isinstance(data, dict) else {}
            weekly_plan = (
                time_program.get("weeklyPlan", {})
                if isinstance(time_program, dict)
                else {}
            )
            plans = weekly_plan.get("plans", []) if isinstance(weekly_plan, dict) else []
            result[name] = self._normalize_weekly_plans(plans)
        return result

    @staticmethod
    def _normalize_weekly_plans(plans: Any) -> dict[str, list[dict[str, Any]]]:
        """Convert numeric weekdays and minute offsets to readable attributes."""
        day_names = [
            "sunday",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
        ]
        result: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(plans, list):
            return result
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            slices = []
            for item in plan.get("slices", []):
                if not isinstance(item, dict):
                    continue
                minute = int(item.get("from", 0))
                slices.append(
                    {
                        "time": f"{minute // 60:02d}:{minute % 60:02d}",
                        "mode": "comfort" if int(item.get("temp", 0)) == 1 else "reduced",
                    }
                )
            for day in plan.get("days", []):
                try:
                    result[day_names[int(day)]] = list(slices)
                except (IndexError, TypeError, ValueError):
                    continue
        return result

    def _refresh_extended_data(self) -> None:
        """Refresh slow endpoints at conservative independent intervals."""
        now = time.monotonic()
        refreshes = (
            ("energy", "_energy_cache", 1800.0, self._get_energy_data),
            ("settings", "_settings_cache", 3600.0, self._get_settings_data),
            (
                "time_programs",
                "_time_program_cache",
                1800.0,
                self._get_time_programs,
            ),
        )
        for name, cache_attr, interval, fetch in refreshes:
            updated_attr = f"_{name}_updated"
            last_updated = getattr(self, updated_attr)
            if last_updated > 0 and now - last_updated < interval:
                continue
            try:
                setattr(self, cache_attr, fetch())
                setattr(self, updated_attr, now)
            except RemoconAuthError:
                raise
            except RemoconApiError as err:
                _LOGGER.debug("Could not refresh %s data: %s", name, err)

    def get_data(self, include_extended: bool = False) -> RemoconData:
        """Fetch all data and return a RemoconData object."""
        items = self._get_raw()
        if include_extended:
            self._refresh_extended_data()

        def as_float(value: Any) -> float | None:
            """Convert an API value without turning missing data into a real zero."""
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def as_bool(value: Any) -> bool:
            """Convert the numeric and textual booleans used by the API."""
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        def as_int(value: Any, default: int) -> int:
            """Convert an API enum value while retaining a safe default."""
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def value(item_id: str) -> Any:
            """Return a data-item value by its stable Remocon-Net ID."""
            return items.get(item_id, {}).get("value")

        def option_text(item_id: str) -> str | None:
            """Resolve the localized option text belonging to an enum value."""
            item = items.get(item_id, {})
            options = item.get("options")
            texts = item.get("optTexts")
            if not isinstance(options, list) or not isinstance(texts, list):
                return None
            current = as_int(item.get("value"), -1)
            try:
                index = [as_int(option, -2) for option in options].index(current)
            except ValueError:
                return None
            return str(texts[index]) if index < len(texts) else None

        comfort = items.get("ZoneComfortTemp", {})
        plant_mode = as_int(value("PlantMode"), -1)
        zone_request = as_bool(value("ZoneHeatRequest"))
        dhw_mode = as_int(value("DhwMode"), 0)
        zone_mode = as_int(value("ZoneMode"), MODE_AUTOMATIC)

        return RemoconData(
            comfort_temp=as_float(value("ZoneComfortTemp")),
            comfort_temp_min=as_float(comfort.get("min")) or 5.0,
            comfort_temp_max=as_float(comfort.get("max")) or 35.0,
            comfort_temp_step=as_float(comfort.get("step")) or 0.5,
            reduced_temp=as_float(value("ZoneEconomyTemp")),
            cooling_comfort_temp=as_float(value("ZoneComfortCoolingTemp")),
            cooling_reduced_temp=as_float(value("ZoneEconomyCoolingTemp")),
            desired_temp=as_float(value("ZoneDesiredTemp")),
            room_temp=as_float(value("ZoneMeasuredTemp")),
            zone_mode=zone_mode,
            zone_mode_text=option_text("ZoneMode"),
            zone_mode_texts=[
                str(text) for text in items.get("ZoneMode", {}).get("optTexts", [])
            ],
            heating_active=zone_request and plant_mode != 3,
            cooling_active=zone_request and plant_mode == 3,
            heat_or_cool_request=zone_request,
            automatic_thermoregulation=as_bool(value("AutomaticThermoregulation")),
            zone_pilot_on=as_bool(value("IsZonePilotOn")),
            holiday_active=as_bool(value("Holiday")),
            outside_temp=as_float(value("OutsideTemp")),
            dhw_temp=as_float(value("DhwStorageTemperature")),
            dhw_target_temp=as_float(value("DhwTemp")),
            dhw_comfort_temp=as_float(value("DhwTimeProgComfortTemp")),
            dhw_reduced_temp=as_float(value("DhwTimeProgEconomyTemp")),
            dhw_mode=dhw_mode,
            dhw_mode_text=option_text("DhwMode"),
            dhw_enabled=dhw_mode != 0,
            heat_pump_on=as_bool(value("IsHeatingPumpOn")),
            quiet_mode=as_bool(value("IsQuite")),
            dhw_boost=as_bool(value("IsDhwBoost")),
            resistor_on=as_bool(value("IsResistorOn")),
            system_pressure=as_float(value("HeatingCircuitPressure")),
            flow_temperature=as_float(value("ChFlowSetpointTemp")),
            has_room_sensor=as_float(value("ZoneMeasuredTemp")) is not None,
            plant_mode=plant_mode if plant_mode >= 0 else None,
            plant_mode_text=option_text("PlantMode"),
            energy=dict(self._energy_cache),
            settings=dict(self._settings_cache),
            time_programs=dict(self._time_program_cache),
        )

    def set_dhw_comfort_temperature(self, temperature: float) -> None:
        """Set the DHW time-program comfort temperature and verify the result."""
        if temperature < 35 or temperature > 65 or not float(temperature).is_integer():
            raise RemoconDataError(
                "DHW comfort temperature must be a whole degree between 35 and 65"
            )

        items = self._get_raw(use_cache=False)
        required_ids = (
            "DhwTimeProgComfortTemp",
            "DhwTimeProgEconomyTemp",
            "DhwMode",
            "DhwStorageTemperature",
            "IsDhwBoost",
        )
        if any(item_id not in items for item_id in required_ids):
            raise RemoconDataError("DHW write prerequisites are missing")

        previous_items: list[dict[str, Any]] = []
        request_items: list[dict[str, Any]] = []
        for item_id in required_ids:
            source = items[item_id]
            previous = {
                "id": item_id,
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
            }
            previous_items.append(previous)
            request_items.append(
                {
                    "itemId": item_id,
                    "value": (
                        int(temperature)
                        if item_id == "DhwTimeProgComfortTemp"
                        else source.get("value")
                    ),
                }
            )

        response = self._request(
            "POST",
            f"/R2/PlantDhw/Save/{self._gateway_id}",
            json={
                "requestItems": request_items,
                "prevDataItems": previous_items,
                "features": self._features(),
            },
        )
        if not isinstance(response, dict) or not response.get("ok"):
            raise RemoconDataError("Remocon-Net rejected the DHW temperature change")

        for attempt in range(3):
            confirmed = self._get_raw(use_cache=False).get(
                "DhwTimeProgComfortTemp", {}
            ).get("value")
            if self._float(confirmed) == float(temperature):
                return
            if attempt < 2:
                time.sleep(1)
        raise RemoconDataError("DHW temperature change could not be confirmed")

    def reauth(self) -> None:
        """Force re-authentication."""
        self._session = None
        self.login()
