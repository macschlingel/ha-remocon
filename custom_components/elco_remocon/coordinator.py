"""DataUpdateCoordinator for Elco Remocon-Net."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RemoconAuthError, RemoconClient, RemoconConnectionError, RemoconData
from .const import CONF_GATEWAY_ID, CONF_ZONE, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ElcoRemoconCoordinator(DataUpdateCoordinator[RemoconData]):
    """Coordinator to poll Elco Remocon-Net data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            always_update=False,
        )
        self.client = RemoconClient(
            email=config_entry.data[CONF_EMAIL],
            password=config_entry.data[CONF_PASSWORD],
            gateway_id=config_entry.data[CONF_GATEWAY_ID],
            zone=config_entry.data.get(CONF_ZONE, "1"),
        )

    @property
    def device_info(self) -> dict:
        """Return shared Home Assistant device metadata."""
        model = self.data.appliance_model if self.data else None
        gateway_id = self.config_entry.data[CONF_GATEWAY_ID]
        return {
            "identifiers": {(DOMAIN, gateway_id)},
            "name": model or "Remocon-Net Heat Pump",
            "manufacturer": "Elco",
            "model": model or "Unknown",
        }

    async def _async_update_data(self) -> RemoconData:
        """Fetch data from the Remocon-Net cloud API."""
        try:
            return await self.hass.async_add_executor_job(self.client.get_data)
        except RemoconAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except RemoconConnectionError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err
