"""Tests for RemoconClient._get_raw's non-BSB/BSB endpoint fallback.

Remocon-Net splits systems onto two endpoint families depending on whether
they're BSB-bus (e.g. Aerotop/Aquatop heat pumps) or not (e.g. older
boilers), and each family 500s if you call the other one's endpoint. These
tests cover the probe/fallback/cache/self-heal behavior in _get_raw without
making any real HTTP requests.
"""
from unittest.mock import patch

import pytest

from custom_components.elco_remocon.api import RemoconClient, RemoconConnectionError

GOOD_RESPONSE = {"ok": True, "data": {"plantData": {}, "zoneData": {}}}


def make_client() -> RemoconClient:
    return RemoconClient(email="e@example.com", password="pw", gateway_id="GW123", zone="1")


def test_first_prefix_succeeds_makes_one_request():
    client = make_client()
    with patch.object(client, "_request", return_value=GOOD_RESPONSE) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 1
    called_path = mock_request.call_args[0][1]
    assert called_path.startswith("/R2/PlantHome/GetData/")
    assert client._planthome_prefix == "/R2/PlantHome"


def test_falls_back_to_bsb_and_caches_it():
    client = make_client()
    with patch.object(
        client,
        "_request",
        side_effect=[RemoconConnectionError("500"), GOOD_RESPONSE],
    ) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 2
    first_path = mock_request.call_args_list[0][0][1]
    second_path = mock_request.call_args_list[1][0][1]
    assert first_path.startswith("/R2/PlantHome/GetData/")
    assert second_path.startswith("/R2/PlantHomeBsb/GetData/")
    assert client._planthome_prefix == "/R2/PlantHomeBsb"


def test_both_prefixes_fail_raises_first_error_and_caches_nothing():
    client = make_client()
    first_error = RemoconConnectionError("500 non-bsb")
    second_error = RemoconConnectionError("500 bsb")
    with patch.object(client, "_request", side_effect=[first_error, second_error]):
        with pytest.raises(RemoconConnectionError) as exc_info:
            client._get_raw()

    assert exc_info.value is first_error
    assert client._planthome_prefix is None


def test_cached_prefix_is_used_on_subsequent_call_without_reprobing():
    client = make_client()
    client._planthome_prefix = "/R2/PlantHomeBsb"
    with patch.object(client, "_request", return_value=GOOD_RESPONSE) as mock_request:
        client._get_raw()

    assert mock_request.call_count == 1
    called_path = mock_request.call_args[0][1]
    assert called_path.startswith("/R2/PlantHomeBsb/GetData/")


def test_cached_prefix_failing_clears_cache_for_next_call():
    client = make_client()
    client._planthome_prefix = "/R2/PlantHomeBsb"
    with patch.object(client, "_request", side_effect=RemoconConnectionError("500 again")):
        with pytest.raises(RemoconConnectionError):
            client._get_raw()

    assert client._planthome_prefix is None
