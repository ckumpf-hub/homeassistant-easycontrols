"""Configurable humidity settings for Helios Easy Controls."""

from typing import Self

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.easycontrols import get_coordinator
from custom_components.easycontrols.const import DATA_FAN_REGULATOR, DOMAIN
from custom_components.easycontrols.coordinator import EasyControlsDataUpdateCoordinator
from custom_components.easycontrols.fan_regulator import FanRegulatorController


class HumiditySettingNumber(NumberEntity):
    """Set either the humidity correction factor or the adaptive start value."""

    _attr_mode = NumberMode.BOX
    _attr_should_poll = False

    def __init__(
        self: Self,
        coordinator: EasyControlsDataUpdateCoordinator,
        regulator: FanRegulatorController,
        *,
        factor: bool,
    ) -> None:
        self._regulator = regulator
        self._factor = factor
        key = "humidity_correction_factor" if factor else "adaptive_humidity_start"
        name = "humidity correction factor" if factor else "adaptive humidity start"
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_name = f"{coordinator.device_name} {name}"
        self._attr_native_min_value = 0.001 if factor else 0.0
        self._attr_native_max_value = 2.0 if factor else 100.0
        self._attr_native_step = 0.001 if factor else 0.1
        self._attr_native_unit_of_measurement = None if factor else "%"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.serial_number)})

    @property
    def native_value(self: Self) -> float:
        """Return the current setting."""
        return self._regulator.humidity_factor if self._factor else self._regulator.initial_humidity

    async def async_added_to_hass(self: Self) -> None:
        """Subscribe to humidity configuration changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._regulator.add_humidity_listener(self.async_write_ha_state))

    async def async_set_native_value(self: Self, value: float) -> None:
        """Update the setting and persist it."""
        if self._factor:
            await self._regulator.async_configure_humidity(factor=value)
        else:
            await self._regulator.async_configure_humidity(initial_humidity=value)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up humidity configuration entities for a device."""
    mac_address = config_entry.data[CONF_MAC]
    coordinator = get_coordinator(hass, mac_address)
    regulator = hass.data[DOMAIN][DATA_FAN_REGULATOR][mac_address]
    async_add_entities(
        [
            HumiditySettingNumber(coordinator, regulator, factor=True),
            HumiditySettingNumber(coordinator, regulator, factor=False),
        ]
    )