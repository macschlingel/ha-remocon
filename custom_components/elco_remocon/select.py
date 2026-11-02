"""Select entities for Elco Remocon-Net — mode dropdowns."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RemoconData
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

_LOGGER = logging.getLogger(__name__)

# Mode mappings: HA option label -> API integer value
DHW_MODE_MAP: dict[str, int] = {
    "disabled": 0,
    "timed": 1,
    "continuous": 2,
}
DHW_MODE_REVERSE: dict[int, str] = {v: k for k, v in DHW_MODE_MAP.items()}

PLANT_MODE_MAP: dict[str, int] = {
    "summer": 0,
    "winter": 1,
    "heating_only": 2,
    "cooling": 3,
    "off": 5,
}
PLANT_MODE_REVERSE: dict[int, str] = {v: k for k, v in PLANT_MODE_MAP.items()}

ZONE_MODE_MAP: dict[str, int] = {
    "off": 0,
    "manual": 2,
    "time_program": 3,
}
ZONE_MODE_REVERSE: dict[int, str] = {v: k for k, v in ZONE_MODE_MAP.items()}


@dataclass(kw_only=True)
class ElcoSelectDescription(SelectEntityDescription):
    """Describe an Elco select entity."""

    key: str
    translation_key: str
    options: list[str]
    current_fn: Any  # Callable[[RemoconData], int]
    reverse_map: dict[int, str]
    mode_map: dict[str, int]
    set_fn: Any  # Callable[[RemoconClient, int], None]
    exists_fn: Any  # Callable[[RemoconData], bool]


SELECTS: tuple[ElcoSelectDescription, ...] = (
    ElcoSelectDescription(
        key="dhw_mode",
        translation_key="dhw_mode",
        options=list(DHW_MODE_MAP.keys()),
        current_fn=lambda d: d.dhw_mode,
        reverse_map=DHW_MODE_REVERSE,
        mode_map=DHW_MODE_MAP,
        set_fn=lambda client, m: client.set_dhw_mode(m),
        exists_fn=lambda d: d.dhw_available,
    ),
    ElcoSelectDescription(
        key="plant_mode",
        translation_key="plant_mode",
        options=list(PLANT_MODE_MAP.keys()),
        current_fn=lambda d: d.plant_mode if d.plant_mode >= 0 else None,
        reverse_map=PLANT_MODE_REVERSE,
        mode_map=PLANT_MODE_MAP,
        set_fn=lambda client, m: client.set_plant_mode(m),
        exists_fn=lambda d: d.plant_mode >= 0,
    ),
    ElcoSelectDescription(
        key="zone_mode",
        translation_key="zone_mode",
        options=list(ZONE_MODE_MAP.keys()),
        current_fn=lambda d: d.zone_mode,
        reverse_map=ZONE_MODE_REVERSE,
        mode_map=ZONE_MODE_MAP,
        set_fn=lambda client, m: client.set_zone_mode(m),
        exists_fn=lambda _: True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco select entities."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create select entities for flat R2 gateways
    if not coordinator.client.is_flat_api:
        _LOGGER.info(
            "Skipping select entities for gateway %s — flat R2 write API required",
            entry.data["gateway_id"],
        )
        return

    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities = [
        ElcoSelect(coordinator, gw_id, zone, desc)
        for desc in SELECTS
        if desc.exists_fn(coordinator.data)
    ]
    async_add_entities(entities)


class ElcoSelect(CoordinatorEntity[ElcoRemoconCoordinator], SelectEntity):
    """Elco select entity for mode control."""

    _attr_has_entity_name = True
    entity_description: ElcoSelectDescription

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        description: ElcoSelectDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = entity_unique_id(gw_id, zone, description.key)
        self._attr_options = description.options
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        raw = self.entity_description.current_fn(self.coordinator.data)
        if raw is None:
            return None
        return self.entity_description.reverse_map.get(int(raw))

    async def async_select_option(self, option: str) -> None:
        """Set the selected option."""
        mode = self.entity_description.mode_map.get(option)
        if mode is None:
            _LOGGER.error("Unknown option %s for %s", option, self.entity_description.key)
            return
        await self.hass.async_add_executor_job(
            self.entity_description.set_fn,
            self.coordinator.client,
            mode,
        )
        await self.coordinator.async_request_refresh()
