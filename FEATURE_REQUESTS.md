# Future Feature Requests

Phase 1 adds the verified temperature and mode controls for flat R2 Aerotop MONO gateways. DHW writes use `/R2/PlantDhw/Save/{gw}`; zone, plant, and quiet-mode writes use the real-device-validated v2 endpoint `/api/v2/remote/dataItems/{gw}/set?umsys=si` ( `/R2/PlantZone/Save` and `/R2/PlantMenu/Save` return 404 on GALEVO-family gateways). The items below are intentionally deferred until their write endpoints and payloads are verified against a live Remocon-Net session.

## Time programs

- Zone schedule: `progId=1`
- DHW schedule: `progId=7`
- Buffer schedule: `progId=15`

The exact save endpoint and payload still need to be captured from the Remocon-Net web application.

## Buffer and advanced parameters

- Buffer heating comfort temperature: `U6_10_1`
- Buffer heating reduced temperature: `U6_10_2`
- Buffer cooling comfort temperature: `U6_10_3`
- Buffer cooling reduced temperature: `U6_10_4`
- Summer/winter threshold temperature: `U6_3_5_0_1`
- Summer/winter delay: `U6_3_5_0_2`
- Legionella protection interval: `U6_9_5_1`
- PV DHW temperature increase: `U6_15_2`
- External heat source modes: `U6_13_0`, `U6_13_1`
- DHW heat-pump mode: `U6_13_2` (`standard`, `ecological`, `heat_pump`, `heat_pump_40`)
- Automatic thermoregulation: `U6_3_3`
- Internet time: `U6_16_6`
- Internet weather: `U6_16_7`

## Research method

1. Open the Remocon-Net web application for the gateway.
2. Change one setting at a time in the DHW, zone, or buffer menu.
3. Capture the request in the browser Network panel.
4. Record the endpoint, request and previous-value item structures, and the response.
5. Add a mocked API test before exposing the setting in Home Assistant.
