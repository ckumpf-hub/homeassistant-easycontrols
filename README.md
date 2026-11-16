# homeassistant-easycontrols

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
![GitHub release (latest by date)](https://img.shields.io/github/v/release/laszlojakab/homeassistant-easycontrols)
![GitHub](https://img.shields.io/github/license/laszlojakab/homeassistant-easycontrols)
![GitHub all releases](https://img.shields.io/github/downloads/laszlojakab/homeassistant-easycontrols/total)
[![HA integration usage](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.easycontrols.total)](https://analytics.home-assistant.io/custom_integrations.json)
[![Donate](https://img.shields.io/badge/donate-Coffee-yellow.svg)](https://www.buymeacoffee.com/laszlojakab)

Helios EasyControls (2.x) Modbus TCP/IP integration for [Home Assistant](https://www.home-assistant.io/). The integration won't work with EasyControls 3.x version devices.

## Installation

You can install this integration via [HACS](#hacs) or [manually](#manual).

### HACS

This integration is included in HACS. Search for the `Easy Controls` integration and choose install. Reboot Home Assistant and configure the 'Helios Easy Controls' integration via the integrations page or press the blue button below.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=easycontrols)

### Manual

Copy the `custom_components/easycontrols` to your `custom_components` folder. Reboot Home Assistant and configure the 'Helios Easy Controls' integration via the integrations page or press the blue button below.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=easycontrols)

## Setup

- Login to device's configuration page.
- Tick `Modbus activated` checkbox under `Configuration/Device/Building control system` menu item.  
- Set `Minimum fan speed` to `0` under `Configuration/Fan/Fan configuration` menu item.

## Features

The integration adds the following entities and services to Home Assistant:

- **Fan**

  The ventilation unit can be controlled by the fan entity. Supported features:

  - turning on/off
  - settings speed
  - setting preset (auto, party, stand-by mode)

- **Automatic fan regulation**

  The `Fan regulator stage 2` switch enables a humidity- and temperature-based
  controller for fan stage 2. It reads extract-air humidity, the current stage 2
  supply-air voltage, and supply-, outdoor-, and extract-air temperatures. A new
  target is calculated at most once per minute and the current target is written
  to the unit every 15 seconds. The controller sets the device to manual mode
  and fan stage 2 while it is active.

  The measured extract-air humidity is multiplied by the configurable humidity
  correction factor before it is used. The adaptive humidity target starts at
  the configured start value (52% by default) and is smoothed toward the
  previous corrected reading with a 1/30 weighting per calculation. The
  difference between corrected humidity and this target is combined with the
  current supply-air voltage to calculate the extract-air fan voltage:

  ```text
  humidity error = corrected humidity - adaptive humidity target
  extract-air voltage = sqrt(5.9 * abs(current supply-air voltage / 1.2
                                      + 0.2 * humidity error))
  supply-air target = extract-air voltage * 1.2
  ```

  The result is adjusted for supply-air temperature: it is reduced above 25.5°C
  and again above 26.5°C, with a 3 V minimum after these reductions. Above 27°C
  the extract-air voltage is set to 0 V, overriding both the minimum and the
  warm-weather override. Otherwise, in warm-weather conditions
  (outdoor air above 12°C, cooler than extract air, and extract air above
  24.4°C), the extract-air voltage is kept at or above 6.3 V. The final supply-
  air target is limited to 0–10 V. When regulation is switched off, the
  controller stops updating and sets a fixed extract-air voltage of 5.9 V
  (7.1 V supply-air target) on stage 2.

- **Sensors**
  - Pre-heater and after-heater related sensors:
    - Preheater operation hours
    - Preheater percentage
    - Afterheater operation hours
    - Afterheater percentage
  - Temperature sensors:
    - Outside air temperature
    - Supply air temperature
    - Extract air temperature
    - Outgoing air temperature
  - Humidity sensors:
    - Extract air relative humidity
    - Corrected extract air relative humidity
    - Adaptive humidity target
  - External sensors:
    - FTF Humidity
    - FTF Temperature
    - CO₂
    - VOC
  - Operation related sensors:
    - Fan speed percentage
    - Fan stage
    - Extract air fan rpm
    - Extract air fan stage
    - Supply air fan rpm
    - Supply air fan stage
  - Binary sensors:
    - Bypass
    - Filter change required
  - Text sensors:
    - Errors
    - Warnings
    - Information
  - Additional computed sensors which values are not provided by the ventilation unit:
    - Airflow rate (approximately value)
    - Heat recovery efficiency (based on: https://www.engineeringtoolbox.com/heat-recovery-efficiency-d_201.html)
- **Humidity settings**

  The humidity correction factor defaults to `1.000` and is applied to the measured
  extract-air humidity for both the corrected sensor and the fan regulator. The raw
  sensor remains unchanged. The adaptive humidity start value defaults to `52%`; it
  initializes the adaptive target, which subsequently follows the corrected readings.
  Changing either setting resets the adaptive target to the configured start value.
- **Services**
  - `easycontrols.party_mode`

## Enable debug logging

The [logger](https://www.home-assistant.io/integrations/logger/) integration lets you define the level of logging activities in Home Assistant. Turning on debug mode will show more information about the running of the integration in the homeassistant.log file.

```yaml
logger:
  default: error
  logs:
    custom_components.easycontrols: debug
```
