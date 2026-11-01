"""Switch entities for Elco Remocon-Net — boost and quiet mode toggles."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RemoconData
from .const import CONF_ZONE, DEFAULT_ZONE, DOMAIN
from .coordinator import ElcoRemoconCoordinator
from .identifiers import entity_unique_id

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True)
class ElcoSwitchDescription(SwitchEntityDescription):
    """Describe an Elco switch entity."""

    key: str
    translation_key: str
    is_on_fn: Any  # Callable[[RemoconData], bool]
    turn_on_fn: Any  # Callable[[RemoconClient], None]
    turn_off_fn: Any  # Callable[[RemoconClient], None]
    exists_fn: Any  # Callable[[RemoconData], bool]


SWITCHES: tuple[ElcoSwitchDescription, ...] = (
    ElcoSwitchDescription(
        key="dhw_boost",
        translation_key="dhw_boost",
        is_on_fn=lambda d: d.dhw_boost,
        turn_on_fn=lambda client: client.set_dhw_boost(True),
        turn_off_fn=lambda client: client.set_dhw_boost(False),
        exists_fn=lambda d: d.dhw_available and d.dhw_boost_available,
    ),
    ElcoSwitchDescription(
        key="quiet_mode",
        translation_key="quiet_mode",
        is_on_fn=lambda d: d.quiet_mode,
        turn_on_fn=lambda client: client.set_quiet_mode(True),
        turn_off_fn=lambda client: client.set_quiet_mode(False),
        exists_fn=lambda _: True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Elco switch entities."""
    coordinator: ElcoRemoconCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Only create switch entities for flat R2 gateways
    if not coordinator.client.is_flat_api:
        _LOGGER.info(
            "Skipping switch entities for gateway %s — flat R2 write API required",
            entry.data["gateway_id"],
        )
        return

    gw_id = entry.data["gateway_id"]
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)

    entities = [
        ElcoSwitch(coordinator, gw_id, zone, desc)
        for desc in SWITCHES
        if desc.exists_fn(coordinator.data)
    ]
    async_add_entities(entities)


class ElcoSwitch(CoordinatorEntity[ElcoRemoconCoordinator], SwitchEntity):
    """Elco switch entity."""

    _attr_has_entity_name = True
    entity_description: ElcoSwitchDescription

    def __init__(
        self,
        coordinator: ElcoRemoconCoordinator,
        gw_id: str,
        zone: str,
        description: ElcoSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = entity_unique_id(gw_id, zone, description.key)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, gw_id)},
            "name": "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": "Aerotop SPK",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self.entity_description.is_on_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.hass.async_add_executor_job(
            self.entity_description.turn_on_fn,
            self.coordinator.client,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.hass.async_add_executor_job(
            self.entity_description.turn_off_fn,
            self.coordinator.client,
        )
        await self.coordinator.async_request_refresh()
