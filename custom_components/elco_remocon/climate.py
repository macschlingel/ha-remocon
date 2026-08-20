"""Climate entity for Elco Remocon-Net heat pump."""

from __future__ import annotations

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AUTOMATIC, MODE_COMFORT, MODE_PROTECTION, MODE_REDUCTION
from .coordinator import ElcoRemoconCoordinator

PRESET_COMFORT = "comfort"
PRESET_REDUCED = "reduced"

HVAC_MODE_MAP = {
    MODE_PROTECTION: HVACMode.OFF,
    MODE_AUTOMATIC: HVACMode.AUTO,
    MODE_REDUCTION: HVACMode.HEAT,
    MODE_COMFORT: HVACMode.HEAT,
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco climate entity."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ElcoClimateEntity(coordinator, entry)])


class ElcoClimateEntity(CoordinatorEntity[ElcoRemoconCoordinator], ClimateEntity):
    """Climate entity for the Elco heat pump zone."""

    _attr_has_entity_name = True
    _attr_translation_key = "elco_heat_pump"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = [PRESET_COMFORT, PRESET_REDUCED]
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        gw_id = entry.data["gateway_id"]
        self._attr_unique_id = f"{gw_id}_climate_zone_{entry.data.get('zone', '1')}"
        self._attr_device_info = coordinator.device_info

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        data = self.coordinator.data
        if data.room_temp > 0:
            return data.room_temp
        # If no room sensor, use desired temp as indicator
        return data.desired_temp if data.desired_temp > 0 else None

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        data = self.coordinator.data
        return data.comfort_temp if data.comfort_temp > 0 else None

    @property
    def target_temperature_step(self) -> float:
        """Return temperature step."""
        return self.coordinator.data.comfort_temp_step or 0.5

    @property
    def min_temp(self) -> float:
        """Return min temperature."""
        return self.coordinator.data.comfort_temp_min or 5.0

    @property
    def max_temp(self) -> float:
        """Return max temperature."""
        return self.coordinator.data.comfort_temp_max or 35.0

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        mode = self.coordinator.data.zone_mode
        return HVAC_MODE_MAP.get(mode, HVACMode.AUTO)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action."""
        data = self.coordinator.data
        if data.zone_mode == MODE_PROTECTION:
            return HVACAction.OFF
        if data.heating_active:
            return HVACAction.HEATING
        if data.cooling_active:
            return HVACAction.COOLING
        if data.heat_or_cool_request:
            return HVACAction.IDLE
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        """Return the raw and translated operating modes from the API."""
        data = self.coordinator.data
        return {
            "zone_mode": data.zone_mode_text,
            "zone_mode_value": data.zone_mode,
            "plant_mode": data.plant_mode_text,
            "plant_mode_value": data.plant_mode,
        }

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode."""
        data = self.coordinator.data
        if data.zone_mode == MODE_COMFORT:
            return PRESET_COMFORT
        if data.zone_mode == MODE_REDUCTION:
            return PRESET_REDUCED
        return None

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_zone_temperatures, temperature, None
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new HVAC mode."""
        mode_map = {
            HVACMode.OFF: MODE_PROTECTION,
            HVACMode.AUTO: MODE_AUTOMATIC,
            HVACMode.HEAT: MODE_COMFORT,
        }
        mode = mode_map.get(hvac_mode)
        if mode is None:
            return
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_zone_mode, mode
        )
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        mode_map = {
            PRESET_COMFORT: MODE_COMFORT,
            PRESET_REDUCED: MODE_REDUCTION,
        }
        mode = mode_map.get(preset_mode)
        if mode is None:
            return
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_zone_mode, mode
        )
        await self.coordinator.async_request_refresh()
