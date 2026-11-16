"""Humidity and temperature based fan voltage regulator."""

import asyncio
import logging
import math
from datetime import datetime, timedelta
from typing import Any, Self

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from custom_components.easycontrols.const import (
    OPERATING_MODE_MANUAL,
    VARIABLE_FAN_STAGE,
    VARIABLE_HUMIDITY_EXTRACT_AIR,
    VARIABLE_OPERATING_MODE,
    VARIABLE_SUPPLY_AIR_VOLTAGE_STAGE_2,
    VARIABLE_TEMPERATURE_EXTRACT_AIR,
    VARIABLE_TEMPERATURE_OUTSIDE_AIR,
    VARIABLE_TEMPERATURE_SUPPLY_AIR,
)
from custom_components.easycontrols.coordinator import EasyControlsDataUpdateCoordinator
from custom_components.easycontrols.modbus_variable import ModbusVariable

_LOGGER = logging.getLogger(__name__)
_FIXED_EXTRACT_AIR_VOLTAGE = 5.9


class FanRegulator:
    """Calculate a supply-air voltage setpoint from sensor readings."""

    def __init__(
        self: Self,
        initial_humidity: float = 52.0,
        reference_voltage: float = 5.9,
        measurements_per_hour: int = 30,
    ) -> None:
        if not 0.0 <= initial_humidity <= 100.0:
            raise ValueError("initial_humidity must be between 0 and 100")
        if reference_voltage < 0.0:
            raise ValueError("reference_voltage must be non-negative")
        if measurements_per_hour < 1:
            raise ValueError("measurements_per_hour must be positive")

        self._adaptive_humidity = initial_humidity
        self._previous_humidity = initial_humidity
        self._reference_voltage = reference_voltage
        self._measurements_per_hour = measurements_per_hour

    @property
    def adaptive_humidity(self: Self) -> float:
        """Return the current adaptive humidity target."""
        return self._adaptive_humidity

    def calculate_supply_voltage(
        self: Self,
        humidity: float,
        current_supply_voltage: float,
        supply_air_temperature: float,
        outdoor_air_temperature: float,
        extract_air_temperature: float,
    ) -> float:
        """Calculate the next supply-air voltage setpoint in the 0-10 V range."""
        self._adaptive_humidity = (
            self._adaptive_humidity * (self._measurements_per_hour - 1)
            + self._previous_humidity
        ) / self._measurements_per_hour

        if humidity < self._previous_humidity * 0.5:
            humidity = self._previous_humidity
        self._previous_humidity = humidity

        humidity_error = humidity - self._adaptive_humidity
        extract_voltage = math.sqrt(
            self._reference_voltage
            * abs(current_supply_voltage / 1.2 + 0.2 * humidity_error)
        )

        if supply_air_temperature > 25.5:
            extract_voltage *= 0.8
        if supply_air_temperature > 26.5:
            extract_voltage *= 0.65

        if extract_voltage < 3.0:
            extract_voltage = 3.0

        if supply_air_temperature > 27.0:
            extract_voltage = 0.0
        elif (
            outdoor_air_temperature < extract_air_temperature
            and outdoor_air_temperature > 12.0
            and extract_air_temperature > 24.4
        ):
            extract_voltage = max(extract_voltage, 6.3)

        extract_voltage = min(max(extract_voltage, 0.0), 10.0 / 1.2)
        supply_voltage = extract_voltage * 1.2
        return round(min(max(supply_voltage, 0.0), 10.0), 1)


class FanRegulatorController:
    """Subscribe to measurements and periodically apply the regulator output."""

    _INPUT_VARIABLES = (
        VARIABLE_HUMIDITY_EXTRACT_AIR,
        VARIABLE_SUPPLY_AIR_VOLTAGE_STAGE_2,
        VARIABLE_TEMPERATURE_SUPPLY_AIR,
        VARIABLE_TEMPERATURE_OUTSIDE_AIR,
        VARIABLE_TEMPERATURE_EXTRACT_AIR,
    )

    def __init__(
        self: Self,
        hass: HomeAssistant,
        coordinator: EasyControlsDataUpdateCoordinator,
        sample_interval_seconds: int = 60,
        write_interval_seconds: int = 15,
        humidity_store: Store | None = None,
    ) -> None:
        if sample_interval_seconds < 1:
            raise ValueError("sample_interval_seconds must be positive")
        if write_interval_seconds < 1:
            raise ValueError("write_interval_seconds must be positive")

        self._hass = hass
        self._coordinator = coordinator
        self._regulator = FanRegulator()
        self._humidity_factor = 1.0
        self._initial_humidity = 52.0
        self._humidity_store = humidity_store
        self._humidity_settings_lock = asyncio.Lock()
        self._sample_interval_seconds = sample_interval_seconds
        self._write_interval_seconds = write_interval_seconds
        self._values: dict[str, float | None] = {
            variable.name: None for variable in self._INPUT_VARIABLES
        }
        self._target_supply_voltage: float | None = None
        self._last_sample_time: float | None = None
        self._timer: CALLBACK_TYPE | None = None
        self._write_lock = asyncio.Lock()
        self._humidity_listeners: list[CALLBACK_TYPE] = []
        self._started = False
        self._enabled = True

    @property
    def adaptive_humidity(self: Self) -> float:
        """Return the current adaptive humidity target."""
        return self._regulator.adaptive_humidity

    @property
    def humidity_factor(self: Self) -> float:
        """Return the relative humidity correction factor."""
        return self._humidity_factor

    @property
    def initial_humidity(self: Self) -> float:
        """Return the configured adaptive humidity start value."""
        return self._initial_humidity

    @property
    def corrected_humidity(self: Self) -> float | None:
        """Return corrected relative humidity when the reading is valid."""
        humidity = self._values[VARIABLE_HUMIDITY_EXTRACT_AIR.name]
        if humidity is None or not math.isfinite(humidity) or not 0 <= humidity <= 100:
            return None
        return min(humidity * self._humidity_factor, 100.0)

    @staticmethod
    def _validate_humidity_settings(factor: float, initial_humidity: float) -> None:
        if not math.isfinite(factor) or not 0.001 <= factor <= 2.0:
            raise ValueError("humidity factor must be between 0.001 and 2")
        if not math.isfinite(initial_humidity) or not 0 <= initial_humidity <= 100:
            raise ValueError("initial humidity must be between 0 and 100")

    def configure_humidity(self: Self, factor: float, initial_humidity: float) -> None:
        """Apply correction settings and restart the adaptive baseline."""
        self._validate_humidity_settings(factor, initial_humidity)
        if factor == self._humidity_factor and initial_humidity == self._initial_humidity:
            return
        self._humidity_factor = factor
        self._initial_humidity = initial_humidity
        self._regulator = FanRegulator(initial_humidity=initial_humidity)
        self._last_sample_time = None
        self._target_supply_voltage = None
        for listener in tuple(self._humidity_listeners):
            listener()

    async def async_configure_humidity(
        self: Self, *, factor: float | None = None, initial_humidity: float | None = None
    ) -> None:
        """Update and persist the humidity settings."""
        async with self._humidity_settings_lock:
            factor = self._humidity_factor if factor is None else factor
            initial_humidity = self._initial_humidity if initial_humidity is None else initial_humidity
            self._validate_humidity_settings(factor, initial_humidity)
            if self._humidity_store is not None:
                await self._humidity_store.async_save(
                    {"factor": factor, "initial_humidity": initial_humidity}
                )
            self.configure_humidity(factor, initial_humidity)

    def add_humidity_listener(self: Self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register a callback for adaptive humidity updates."""
        self._humidity_listeners.append(listener)

        @callback
        def remove_listener() -> None:
            self._humidity_listeners.remove(listener)

        return remove_listener

    @property
    def enabled(self: Self) -> bool:
        """Return whether automatic regulation is enabled."""
        return self._enabled

    async def async_set_enabled(self: Self, enabled: bool) -> None:
        """Enable automatic regulation or apply the fixed voltage once."""
        if self._enabled == enabled:
            return

        self._enabled = enabled
        if self._timer is not None:
            self._timer()
            self._timer = None

        if enabled:
            self._last_sample_time = None
            if self._started:
                self._schedule_next_tick()
        elif self._started:
            await self._write_target_voltage(round(_FIXED_EXTRACT_AIR_VOLTAGE * 1.2, 1))

    def start(self: Self) -> None:
        """Register measurement listeners and start the periodic controller."""
        if self._started:
            return

        self._started = True
        for variable in self._INPUT_VARIABLES:
            self._coordinator.add_listener(variable, self._value_updated)

        self._schedule_next_tick()

    def stop(self: Self) -> None:
        """Remove listeners and cancel the periodic controller."""
        if not self._started:
            return

        self._started = False
        if self._timer is not None:
            self._timer()
            self._timer = None

        for variable in self._INPUT_VARIABLES:
            self._coordinator.remove_listener(variable, self._value_updated)

    @callback
    def _value_updated(self: Self, variable: ModbusVariable, value: Any) -> None:
        if value is None:
            self._values[variable.name] = None
        else:
            try:
                self._values[variable.name] = float(value)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignoring invalid regulator input for %s: %s", variable.name, value)
                self._values[variable.name] = None
        if variable == VARIABLE_HUMIDITY_EXTRACT_AIR:
            for listener in tuple(self._humidity_listeners):
                listener()

    def _schedule_next_tick(self: Self) -> None:
        self._timer = async_call_later(
            self._hass,
            timedelta(seconds=self._write_interval_seconds),
            self._async_tick,
        )

    @callback
    async def _async_tick(self: Self, execution_time: datetime) -> None:  # noqa: ARG002
        if not self._started or not self._enabled:
            return

        try:
            corrected_humidity = self.corrected_humidity
            if corrected_humidity is not None and all(
                value is not None for value in self._values.values()
            ):
                now = asyncio.get_running_loop().time()
                if (
                    self._last_sample_time is None
                    or now - self._last_sample_time >= self._sample_interval_seconds
                ):
                    self._target_supply_voltage = self._regulator.calculate_supply_voltage(
                        humidity=corrected_humidity,
                        current_supply_voltage=self._values[
                            VARIABLE_SUPPLY_AIR_VOLTAGE_STAGE_2.name
                        ],
                        supply_air_temperature=self._values[
                            VARIABLE_TEMPERATURE_SUPPLY_AIR.name
                        ],
                        outdoor_air_temperature=self._values[
                            VARIABLE_TEMPERATURE_OUTSIDE_AIR.name
                        ],
                        extract_air_temperature=self._values[
                            VARIABLE_TEMPERATURE_EXTRACT_AIR.name
                        ],
                    )
                    self._last_sample_time = now
                    for listener in tuple(self._humidity_listeners):
                        listener()

            if self._target_supply_voltage is not None:
                await self._write_target_voltage(
                    self._target_supply_voltage,
                    only_if_enabled=True,
                )
        except Exception:
            _LOGGER.exception("Error while updating fan regulator")
        finally:
            if self._started and self._enabled:
                self._schedule_next_tick()

    async def _write_target_voltage(
        self: Self,
        supply_air_voltage: float,
        only_if_enabled: bool = False,
    ) -> None:
        async with self._write_lock:
            if only_if_enabled and not self._enabled:
                return

            mode_set = await self._coordinator.set_variable(
                VARIABLE_OPERATING_MODE, OPERATING_MODE_MANUAL
            )
            stage_set = await self._coordinator.set_variable(VARIABLE_FAN_STAGE, 2)
            voltages_set = await self._coordinator.set_fan_stage_voltages(
                2, supply_air_voltage
            )
            if not mode_set or not stage_set or not voltages_set:
                _LOGGER.warning("Could not apply fan regulator output")