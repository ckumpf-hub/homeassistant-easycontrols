"""Switch entities for Helios Easy Controls."""

import asyncio
from typing import Self

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store

from custom_components.easycontrols import get_coordinator
from custom_components.easycontrols.const import (
    DATA_FAN_REGULATOR,
    DOMAIN,
    VARIABLE_AFTERHEATER_STATUS,
    VARIABLE_BYPASS_FROM_DAY,
    VARIABLE_BYPASS_FROM_MONTH,
    VARIABLE_BYPASS_OUTDOOR_AIR_TEMPERATURE,
    VARIABLE_BYPASS_SUPPLY_AIR_OFFSET,
    VARIABLE_BYPASS_TO_DAY,
    VARIABLE_BYPASS_TO_MONTH,
    VARIABLE_PASSIVE_COOLING,
)
from custom_components.easycontrols.coordinator import EasyControlsDataUpdateCoordinator
from custom_components.easycontrols.fan_regulator import FanRegulatorController
from custom_components.easycontrols.modbus_variable import IntModbusVariable


class BypassRequestSwitch(SwitchEntity):
    """Request bypass operation through the manufacturer's cooling settings."""

    _SETTINGS = (
        (VARIABLE_BYPASS_FROM_DAY, 1),
        (VARIABLE_BYPASS_FROM_MONTH, 5),
        (VARIABLE_BYPASS_TO_DAY, 31),
        (VARIABLE_BYPASS_TO_MONTH, 10),
        (VARIABLE_BYPASS_OUTDOOR_AIR_TEMPERATURE, 5),
        (VARIABLE_BYPASS_SUPPLY_AIR_OFFSET, 3),
    )

    def __init__(
        self: Self,
        coordinator: EasyControlsDataUpdateCoordinator,
        store: Store,
        original_settings: dict[str, int] | None,
    ) -> None:
        self._coordinator = coordinator
        self._store = store
        self._original_settings = original_settings
        self._operation_lock = asyncio.Lock()
        self._attr_unique_id = f"{coordinator.mac}_bypass_request"
        self._attr_name = f"{coordinator.device_name} bypass opening request"
        self._attr_icon = "mdi:air-filter"
        self._attr_should_poll = False
        self._attr_available = False
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.serial_number)})

    async def async_added_to_hass(self: Self) -> None:
        """Subscribe to the manufacturer's passive cooling setting."""
        await super().async_added_to_hass()
        self._coordinator.add_listener(VARIABLE_PASSIVE_COOLING, self._value_updated)
        self._coordinator.schedule_update(VARIABLE_PASSIVE_COOLING)

    async def async_will_remove_from_hass(self: Self) -> None:
        """Remove the passive cooling listener."""
        self._coordinator.remove_listener(VARIABLE_PASSIVE_COOLING, self._value_updated)
        await super().async_will_remove_from_hass()

    def _value_updated(self: Self, variable: IntModbusVariable, value: int | None) -> None:
        self._attr_available = value in (1, 2)
        self._attr_is_on = value == 2
        self.async_write_ha_state()

    async def _set_variable(self: Self, variable: IntModbusVariable, value: int) -> None:
        if not await self._coordinator.set_variable(variable, value):
            raise HomeAssistantError(f"Failed to write {variable.name}")

    async def _check_afterheater(self: Self) -> None:
        if await self._coordinator.get_variable(VARIABLE_AFTERHEATER_STATUS) != 5:
            raise HomeAssistantError("Bypass opening requires the afterheater mode to be off")

    async def async_turn_on(self: Self, **kwargs) -> None:  # noqa: ARG002
        """Request opening using the least restrictive supported thresholds."""
        async with self._operation_lock:
            try:
                await self._check_afterheater()
                if self._original_settings is None:
                    original_settings = {}
                    for variable, _ in self._SETTINGS:
                        original_settings[variable.name] = await self._coordinator.get_variable(
                            variable
                        )
                    await self._store.async_save(original_settings)
                    self._original_settings = original_settings

                await self._set_variable(VARIABLE_PASSIVE_COOLING, 1)
                for variable, value in self._SETTINGS:
                    await self._set_variable(variable, value)
                await self._check_afterheater()
                await self._set_variable(VARIABLE_PASSIVE_COOLING, 2)
            finally:
                self._coordinator.schedule_update(VARIABLE_PASSIVE_COOLING)

    async def async_turn_off(self: Self, **kwargs) -> None:  # noqa: ARG002
        """Disable the request and restore the saved manufacturer settings."""
        async with self._operation_lock:
            try:
                await self._set_variable(VARIABLE_PASSIVE_COOLING, 1)
                if self._original_settings is not None:
                    for variable, _ in self._SETTINGS:
                        await self._set_variable(variable, self._original_settings[variable.name])
                    await self._store.async_remove()
                    self._original_settings = None
            finally:
                self._coordinator.schedule_update(VARIABLE_PASSIVE_COOLING)


class FanRegulatorSwitch(RestoreEntity, SwitchEntity):
    """Enable or disable automatic fan stage 2 regulation."""

    entity_description = SwitchEntityDescription(
        key="fan_regulator_stage_2",
        name="Fan regulator stage 2",
        icon="mdi:fan",
    )

    def __init__(
        self: Self,
        coordinator: EasyControlsDataUpdateCoordinator,
        regulator: FanRegulatorController,
    ) -> None:
        self._regulator = regulator
        self._attr_unique_id = f"{coordinator.mac}_fan_regulator_stage_2"
        self._attr_should_poll = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.serial_number)},
        )

    @property
    def is_on(self) -> bool:
        """Return whether automatic regulation is enabled."""
        return self._regulator.enabled

    async def async_added_to_hass(self: Self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            await self._regulator.async_set_enabled(last_state.state == "on")

    async def async_turn_on(self: Self, **kwargs) -> None:  # noqa: ARG002
        await self._regulator.async_set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self: Self, **kwargs) -> None:  # noqa: ARG002
        await self._regulator.async_set_enabled(False)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the fan regulator switch for a config entry."""
    mac_address = config_entry.data[CONF_MAC]
    coordinator = get_coordinator(hass, mac_address)
    regulator = hass.data[DOMAIN][DATA_FAN_REGULATOR][mac_address]
    store = Store(hass, 1, f"{DOMAIN}.bypass_settings.{config_entry.entry_id}")
    original_settings = await store.async_load()
    async_add_entities(
        [
            FanRegulatorSwitch(coordinator, regulator),
            BypassRequestSwitch(coordinator, store, original_settings),
        ]
    )