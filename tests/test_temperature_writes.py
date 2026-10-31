"""Regression tests for temperature writes with incomplete legacy responses.

All requests, including the reads that discover the BSB browser API, use a
mocked session. An additional guard rejects any unexpected real HTTP request.
"""

import json
from unittest.mock import Mock, patch

import pytest
import requests

from custom_components.elco_remocon.api import (
    BASE_URL,
    RemoconClient,
    RemoconDataError,
)


TEMPERATURE_CASES = [
    pytest.param(
        "zoneData", "chReducedTemp", "set_zone_temperatures", {"comfort": 22},
        id="comfort-needs-current-reduced",
    ),
    pytest.param(
        "zoneData", "chComfortTemp", "set_zone_temperatures", {"reduced": 17},
        id="reduced-needs-current-comfort",
    ),
    pytest.param(
        "plantData", "dhwReducedTemp", "set_dhw_temperature", {"comfort": 55},
        id="dhw-comfort-needs-current-reduced",
    ),
    pytest.param(
        "plantData", "dhwComfortTemp", "set_dhw_temperature", {"reduced": 40},
        id="dhw-reduced-needs-current-comfort",
    ),
]


@pytest.fixture(autouse=True)
def reject_real_http():
    """Keep a missing session mock from opening a real network connection."""
    with patch(
        "requests.sessions.Session.request",
        side_effect=AssertionError("Real HTTP requests are forbidden in these tests"),
    ):
        yield


def temperature_response():
    return {"ok": True, "data": {
        "zoneData": {
            "chComfortTemp": {"value": 21.5},
            "chReducedTemp": {"value": 18},
        },
        "plantData": {
            "dhwComfortTemp": {"value": 52},
            "dhwReducedTemp": {"value": 42},
        },
    }}


def http_response(payload, status=200):
    response = requests.Response()
    response.status_code = status
    response.url = f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123"
    response._content = json.dumps(payload).encode("utf-8")
    return response


def mocked_client(payload, variant="bsb_browser"):
    client = RemoconClient(
        email="e@example.com", password="pw", gateway_id="GW123", zone="1"
    )
    client._planthome_variant = variant
    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        http_response(payload), http_response({"ok": True}),
    ]
    client._session = session
    return client, session


def assert_only_browser_read(session):
    assert session.request.call_count == 1
    assert session.request.call_args.args == (
        "POST", f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123",
    )


@pytest.mark.parametrize("section,key,method,arguments", TEMPERATURE_CASES)
@pytest.mark.parametrize("missing", ["section", "temperature", "value"])
def test_partial_browser_response_aborts_entire_temperature_write(
    section, key, method, arguments, missing
):
    payload = temperature_response()
    if missing == "section":
        del payload["data"][section]
    elif missing == "temperature":
        del payload["data"][section][key]
    else:
        del payload["data"][section][key]["value"]
    client, session = mocked_client(payload)

    with pytest.raises(RemoconDataError, match="current temperature"):
        getattr(client, method)(**arguments)

    assert_only_browser_read(session)
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False


@pytest.mark.parametrize("section,key,method,arguments", TEMPERATURE_CASES[::2])
@pytest.mark.parametrize("invalid", [
    pytest.param(None, id="none"),
    pytest.param("", id="empty-string"),
    pytest.param("   ", id="whitespace"),
    pytest.param("invalid", id="not-numeric"),
    pytest.param(False, id="false"),
    pytest.param(True, id="true"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(float("-inf"), id="negative-infinity"),
    pytest.param("NaN", id="nan-string"),
    pytest.param("1e999", id="overflowing-string"),
    pytest.param(10 ** 1000, id="overflowing-int"),
    pytest.param([], id="list"),
    pytest.param({}, id="dict"),
])
def test_invalid_current_temperature_aborts_without_control_request(
    section, key, method, arguments, invalid
):
    payload = temperature_response()
    payload["data"][section][key]["value"] = invalid
    client, session = mocked_client(payload)

    with pytest.raises(RemoconDataError, match="current temperature"):
        getattr(client, method)(**arguments)

    assert_only_browser_read(session)


@pytest.mark.parametrize("section,key,method,arguments", TEMPERATURE_CASES[::2])
@pytest.mark.parametrize("level", ["section", "temperature"])
@pytest.mark.parametrize("malformed", [None, [], 21])
def test_malformed_temperature_structure_raises_data_error_before_write(
    section, key, method, arguments, level, malformed
):
    payload = temperature_response()
    if level == "section":
        payload["data"][section] = malformed
    else:
        payload["data"][section][key] = malformed
    client, session = mocked_client(payload)

    with pytest.raises(RemoconDataError, match="current temperature"):
        getattr(client, method)(**arguments)

    assert_only_browser_read(session)


@pytest.mark.parametrize("section,key,method,arguments", TEMPERATURE_CASES)
def test_initial_browser_discovery_does_not_enable_write_with_partial_response(
    section, key, method, arguments
):
    payload = temperature_response()
    del payload["data"][section][key]
    client, session = mocked_client(payload, variant=None)
    session.request.side_effect = [
        http_response({}, status=500),
        http_response({}, status=500),
        http_response(payload),
    ]

    with pytest.raises(RemoconDataError, match="current temperature"):
        getattr(client, method)(**arguments)

    assert [entry.args[1] for entry in session.request.call_args_list] == [
        f"{BASE_URL}/R2/PlantHome/GetData/GW123?umsys=si",
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123?umsys=si",
        f"{BASE_URL}/R2/PlantHomeBsb/GetData/GW123",
    ]
    assert client._planthome_variant == "bsb_browser"
    assert client.is_flat_api is False


@pytest.mark.parametrize("section,key,method,_", TEMPERATURE_CASES)
def test_supplying_both_new_temperatures_still_requires_both_old_values(
    section, key, method, _
):
    payload = temperature_response()
    del payload["data"][section][key]
    client, session = mocked_client(payload)

    with pytest.raises(RemoconDataError, match="current temperature"):
        getattr(client, method)(comfort=22, reduced=17)

    assert_only_browser_read(session)


@pytest.mark.parametrize("section,key,method,arguments", TEMPERATURE_CASES)
@pytest.mark.parametrize("variant", ["planthome_si", "bsb_si"])
def test_partial_si_responses_also_abort_without_control_request(
    section, key, method, arguments, variant
):
    payload = temperature_response()
    del payload["data"][section][key]
    client, session = mocked_client(payload, variant=variant)

    with pytest.raises(RemoconDataError, match="current temperature"):
        getattr(client, method)(**arguments)

    prefix = "PlantHome" if variant == "planthome_si" else "PlantHomeBsb"
    assert session.request.call_count == 1
    assert session.request.call_args.args == (
        "POST", f"{BASE_URL}/R2/{prefix}/GetData/GW123?umsys=si",
    )
    assert client._planthome_variant == variant


@pytest.mark.parametrize("variant", ["planthome_si", "bsb_si", "bsb_browser"])
@pytest.mark.parametrize("method,arguments,path,expected", [
    pytest.param(
        "set_zone_temperatures", {"comfort": 22},
        "/api/v2/remote/bsbZones/GW123/1/temperatures?isCooling=false",
        {"new": {"comf": 22, "econ": 18.0}, "old": {"comf": 21.5, "econ": 18.0}},
        id="comfort",
    ),
    pytest.param(
        "set_zone_temperatures", {"reduced": 17},
        "/api/v2/remote/bsbZones/GW123/1/temperatures?isCooling=false",
        {"new": {"comf": 21.5, "econ": 17}, "old": {"comf": 21.5, "econ": 18.0}},
        id="reduced",
    ),
    pytest.param(
        "set_dhw_temperature", {"comfort": 55},
        "/api/v2/remote/bsbPlantData/GW123/dhwTemp",
        {"new": {"comf": 55, "econ": 42.0}, "old": {"comf": 52.0, "econ": 42.0}},
        id="dhw-comfort",
    ),
    pytest.param(
        "set_dhw_temperature", {"reduced": 40},
        "/api/v2/remote/bsbPlantData/GW123/dhwTemp",
        {"new": {"comf": 52.0, "econ": 40}, "old": {"comf": 52.0, "econ": 42.0}},
        id="dhw-reduced",
    ),
])
def test_complete_responses_keep_existing_temperature_write_payloads(
    variant, method, arguments, path, expected
):
    payload = temperature_response()
    # Numeric strings are already accepted by the legacy API write path.
    for section in payload["data"].values():
        for record in section.values():
            record["value"] = str(record["value"])
    client, session = mocked_client(payload, variant=variant)

    getattr(client, method)(**arguments)

    assert session.request.call_count == 2
    assert session.request.call_args.args == ("POST", f"{BASE_URL}{path}")
    assert session.request.call_args.kwargs == {"json": expected, "timeout": 15}
    assert client._planthome_variant == variant
    assert client.is_flat_api is False


@pytest.mark.parametrize("section,key,method,arguments", TEMPERATURE_CASES)
@pytest.mark.parametrize("zero", [0, -0.0, "0"])
def test_present_zero_current_temperature_is_preserved_in_write(
    section, key, method, arguments, zero
):
    payload = temperature_response()
    payload["data"][section][key]["value"] = zero
    client, session = mocked_client(payload)

    getattr(client, method)(**arguments)

    assert session.request.call_count == 2
    body = session.request.call_args.kwargs["json"]
    value_key = "comf" if key in ("chComfortTemp", "dhwComfortTemp") else "econ"
    assert body["old"][value_key] == 0.0
    assert body["new"][value_key] == 0.0
    assert "/api/v2/remote/" in session.request.call_args.args[1]


@pytest.mark.parametrize("method,arguments", [
    ("set_zone_temperatures", {"comfort": 22}),
    ("set_zone_temperatures", {"reduced": 17}),
    ("set_dhw_temperature", {"comfort": 55}),
    ("set_dhw_temperature", {"reduced": 40}),
])
def test_flat_api_temperature_writes_remain_disabled(method, arguments):
    payload = {"ok": True, "data": {"items": [
        {"id": "ZoneComfortTemp", "value": "21.5"},
        {"id": "ZoneEconomyTemp", "value": "18"},
        {"id": "DhwTimeProgComfortTemp", "value": "52"},
        {"id": "DhwTimeProgEconomyTemp", "value": "42"},
    ]}}
    client, session = mocked_client(payload)
    session.request.side_effect = None
    session.request.return_value = http_response(payload)

    with pytest.raises(RemoconDataError, match="Control is disabled"):
        getattr(client, method)(**arguments)

    assert session.request.call_count == 3
    assert all("/GetData/" in entry.args[1] for entry in session.request.call_args_list)
    assert client.is_flat_api is True
