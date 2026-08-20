# ha-remocon

Unofficial Home Assistant integration for ELCO heat pumps connected through the
Remocon-Net cloud service.

> This community project is not endorsed by ELCO or the Ariston Group.

## Current scope

Version 0.3.1 exposes broad read-only monitoring plus one explicitly verified
write control: the DHW time-program comfort temperature. The control is limited
to whole degrees between 35 and 65 °C and confirms the result with an uncached
read after every write. All other write endpoints remain disabled.

The integration polls the live Remocon-Net status every two minutes and publishes:

- room temperature, when a room sensor is present;
- outside, target, comfort, reduced and flow temperatures;
- domestic-hot-water temperature and setpoints;
- heating-circuit pressure and heating-zone operating mode;
- heating, cooling, heat-pump and DHW status.
- current-month and current-year consumed and produced energy;
- calculated performance factors where both energy values are available;
- selected advanced settings and diagnostic values;
- normalized weekly plans for zone, DHW and buffer programs.

To limit cloud traffic, energy and time programs are refreshed at most every 30
minutes and advanced settings at most every 60 minutes.

An expired cloud session is renewed automatically once. Authentication failures
are handed to Home Assistant so that its normal reauthentication flow can take
over.

## Installation

### HACS custom repository

1. Add this repository to HACS as an integration custom repository.
2. Install **Remocon-Net (unofficial)**.
3. Restart Home Assistant.
4. Open **Settings > Devices & services > Add integration** and search for
   **Remocon-Net**.

### Manual

Copy `custom_components/elco_remocon` into the `custom_components` directory of
your Home Assistant configuration and restart Home Assistant.

## Configuration

The config flow asks for:

- the Remocon-Net account email and password;
- the gateway ID shown in the Remocon-Net plant URL;
- the heating-zone number (normally `1`).

Credentials are stored in the Home Assistant config entry. Response bodies,
credentials and cookies are never written to integration logs.

## Entities

| Entity suffix | Type | Description |
| --- | --- | --- |
| `room_temp` | Sensor | Room temperature, if available |
| `outside_temp` | Sensor | Outside temperature |
| `desired_temp` | Sensor | Current target temperature |
| `comfort_temp` | Sensor | Comfort setpoint |
| `reduced_temp` | Sensor | Reduced setpoint |
| `flow_temp` | Sensor | Heating flow temperature |
| `system_pressure` | Sensor | Heating-circuit pressure |
| `dhw_temp` | Sensor | Domestic-hot-water temperature |
| `dhw_comfort_temp` | Sensor | DHW comfort setpoint |
| `dhw_reduced_temp` | Sensor | DHW reduced setpoint |
| `zone_mode` | Sensor | Heating-zone operating mode |
| `heating_active` | Binary sensor | Heating active |
| `cooling_active` | Binary sensor | Cooling active |
| `heat_pump_on` | Binary sensor | Heat pump running |
| `dhw_enabled` | Binary sensor | Domestic hot water enabled |

Optional values are only created when the cloud reports them during initial
setup. A missing measurement is represented as unavailable, not as a false zero.

## Limitations

- The integration depends on the Remocon-Net cloud and does not provide local
  control.
- Cloud API endpoints are unofficial and may change without notice.
- Write access is limited to the verified DHW comfort-temperature control.

## License

MIT
