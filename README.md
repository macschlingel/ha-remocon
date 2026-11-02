# ha-remocon

**Unofficial Home Assistant integration for Elco heat pumps via the Remocon-Net cloud service.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=macschlingel&repository=ha-remocon&category=integration)

Control and monitor your Elco heat pump (e.g. Aerotop SPK) through the Remocon-Net cloud API — directly in Home Assistant, no MQTT or AppDaemon needed.

> **Disclaimer:** This is an unofficial community project. It is not endorsed by or affiliated with Elco or the Ariston Thermo Group.

## Features

- **Climate entity** — Set target temperature, switch operation mode (Auto / Heat / Off), presets (Comfort / Reduced)
- **DHW controls** — Water heater, comfort and reduced temperature numbers, DHW mode, and boost switch
- **Zone controls** — Comfort and reduced temperature numbers and zone mode
- **Plant controls** — Plant mode select and quiet mode switch
- **Sensors** — Outside temperature, flow temperature, target temperature, system pressure
- **Binary sensors** — Heating active, cooling active, heat pump running
- **Config flow** — Easy setup directly in the Home Assistant UI
- **CLI tool** — Standalone `remocon.py` for testing and debugging from the terminal

## Requirements

- Elco heat pump with a Remocon-Net gateway (connected to the internet)
- Remocon-Net account ([remocon-net.remotethermo.com](https://www.remocon-net.remotethermo.com))
- Home Assistant >= 2024.1.0
- [HACS](https://hacs.xyz/) installed

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
2. **≡ Menu** → **Custom Repositories**
3. Add:
   - **URL:** `https://github.com/macschlingel/ha-remocon`
   - **Category:** Integration
4. Search for **"Remocon-Net"** in HACS and install
5. Restart Home Assistant

### Manual

```bash
cd /path/to/homeassistant/config/custom_components/
git clone https://github.com/macschlingel/ha-remocon.git elco_remocon_temp
cp -r elco_remocon_temp/custom_components/elco_remocon ./
rm -rf elco_remocon_temp
```

Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **"Remocon-Net"**
3. Enter your credentials:
   - **Email:** Your Remocon-Net login email
   - **Password:** Your Remocon-Net login password
   - **Gateway ID:** Your system's gateway ID (see below)
   - **Zone:** Heating zone (default: 1)

### Finding your Gateway ID

1. Log in at [remocon-net.remotethermo.com](https://www.remocon-net.remotethermo.com)
2. The gateway ID is shown in the URL, e.g. `A1B2C3D4E5F6` in:
   ```
   https://www.remocon-net.remotethermo.com/R2/Plant/Index/A1B2C3D4E5F6
   ```

The **Gateway ID** is the identifier in this URL. It is not the heat pump's serial number.

### Multiple heating zones

Add the integration once for each heating zone. Use the same Remocon-Net account and Gateway ID each time, and enter a different **Zone** value (for example, `1` and `2`). Zone 1 keeps the existing entity unique IDs; entities for other zones receive zone-specific unique IDs. Each setup creates a separate climate entity and its zone's sensors.

Some newer gateways use a flat R2 data format. Flat R2 gateways expose the verified write controls listed below. The legacy climate entity remains available for the older v2 API.

## Entities

After setup, the following entities are created:

| Entity | Type | Description |
|--------|------|-------------|
| `climate.remocon_net_heat_pump` | Climate | Temperature control, mode, presets |
| `sensor.outside_temperature` | Sensor | Outside temperature |
| `sensor.desired_temperature` | Sensor | Current target temperature |
| `sensor.reduced_temperature` | Sensor | Reduced setpoint temperature |
| `sensor.flow_temperature` | Sensor | Flow temperature |
| `sensor.system_pressure` | Sensor | System pressure (bar) |
| `binary_sensor.heating_active` | Binary | Heating is active |
| `binary_sensor.cooling_active` | Binary | Cooling is active |
| `binary_sensor.heat_pump_on` | Binary | Heat pump is running |
| `water_heater.dhw_water_heater` | Water heater | DHW target temperature and operation mode |
| `number.dhw_comfort_temperature` | Number | DHW comfort setpoint |
| `number.dhw_reduced_temperature` | Number | DHW reduced setpoint |
| `number.zone_comfort_temperature` | Number | Zone comfort setpoint |
| `number.zone_reduced_temperature` | Number | Zone reduced setpoint |
| `select.dhw_mode` | Select | DHW disabled, timed, or continuous |
| `select.zone_mode` | Select | Zone off, manual, or time program |
| `select.plant_mode` | Select | Summer, winter, heating, cooling, or off |
| `switch.dhw_boost` | Switch | Enable rapid DHW heating |
| `switch.quiet_mode` | Switch | Enable quiet operation |

### Additional gateway-dependent entities

These four read-only entities are added when the gateway reports a valid value
for the corresponding field during setup. The keys use the existing gateway
and zone unique ID scheme.

| Key | Type | Legacy R2 field | Flat R2 item |
|-----|------|-----------------|--------------|
| `flame_sensor` | Binary sensor (burner active) | `plantData.flameSensor` | No verified mapping; omitted |
| `heat_or_cool_request` | Binary sensor (heating/cooling request) | `zoneData.heatOrCoolRequest` | `ZoneHeatRequest` |
| `dhw_comfort_temp` | Temperature sensor (DHW comfort setpoint, °C) | `plantData.dhwComfortTemp.value` | `DhwTimeProgComfortTemp` |
| `dhw_reduced_temp` | Temperature sensor (DHW reduced setpoint, °C) | `plantData.dhwReducedTemp.value` | `DhwTimeProgEconomyTemp` |

An explicit `false` creates a binary sensor with state **off**. Missing, null,
or invalid values do not create the corresponding entity; they are never
reported as **off** or 0 °C. An existing entity shows **unknown** if its value
later disappears. If a previously omitted field becomes available, reload the
integration to create that entity. DHW setpoints can be displayed even when
hot water heating is switched off. Flat R2 gateways remain read-only.

### Climate entity

The climate entity supports:

- **HVAC modes:** `Heat` (Comfort), `Auto` (time program), `Off` (frost protection)
- **Presets:** `Comfort`, `Reduced`
- **Temperature:** Adjustable within the range configured on the heat pump

## CLI Tool

A standalone CLI tool is included for testing and debugging:

```bash
pip install -r requirements.txt

# Create config
cp config.example.json config.json
# Edit config.json with your email, password and gateway ID

# Check status
python3 remocon.py --config-file config.json status

# Set temperature
python3 remocon.py --config-file config.json set-temp --comfort 22.0

# Change mode
python3 remocon.py --config-file config.json set-mode comfort

# Flat-R2 controls (the gateway protocol is detected automatically)
python3 remocon.py --config-file config.json set-mode manual
python3 remocon.py --config-file config.json set-dhw-mode continuous
python3 remocon.py --config-file config.json set-plant-mode winter
python3 remocon.py --config-file config.json set-quiet-mode on
python3 remocon.py --config-file config.json set-dhw-boost on

# Raw API response (debug)
python3 remocon.py --config-file config.json raw-get

# JSON output (for scripting)
python3 remocon.py --config-file config.json status --json
```

The CLI keeps the existing commands and legacy API behavior for older gateways. Flat-R2 gateways are detected automatically; their zone modes are `off`, `manual`, or `time_program`, and DHW modes are `disabled`, `timed`, or `continuous` (`on`/`off` remain aliases). `set-plant-mode`, `set-quiet-mode`, and `set-dhw-boost` are available on flat-R2 gateways. Flat-R2 cooling setpoint and cooling zone-mode writes are not yet supported.

## Known limitations

- **Cloud-dependent:** Control goes through the Remocon-Net cloud. No control possible during internet outages.
- **Polling:** Data is fetched every 2 minutes (no real-time streaming).
- **No room sensor:** If no room thermostat is connected, `current_temperature` shows the target value.
- **Flat-R2 verification scope:** Time programs, buffer temperatures, and advanced menu parameters remain read-only until their web-app write payloads are reverse-engineered.

## Technical details

The integration uses the same API as the Elco Remocon-Net web app:

- **Login:** Cookie-based authentication via `/R2/Account/Login`
- **Data:** R2 Web API (`/R2/PlantHome/GetData/`, falling back to `/R2/PlantHomeBsb/GetData/` for BSB-bus systems such as Aerotop/Aquatop, which 500 on the non-BSB path) + v2 REST API (`/api/v2/remote/dataItems/`)
- **Control:** v2 REST API for legacy systems; `/R2/PlantDhw/Save/{gw}` for DHW on Aerotop MONO systems and `/api/v2/remote/dataItems/{gw}/set?umsys=si` for zone, plant, and quiet-mode writes on flat R2 gateways
- **Platform:** remotethermo.com (Ariston Thermo Group)

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

MIT
