from collections.abc import Mapping

import pytest

from lcftp.brokers.base import BrokerSource
from lcftp.brokers.loader import broker_config, load_brokers
from lcftp.brokers.testing import FakeBroker


def _fake(config: Mapping[str, str]) -> BrokerSource:
    return FakeBroker([], name=config.get("name", "fake"))


def test_loads_registered_plugin() -> None:
    brokers = load_brokers(
        ["fake"],
        {"LCFTP_BROKER_FAKE_NAME": "renamed"},
        registry={"fake": _fake},
    )
    assert [b.name for b in brokers] == ["renamed"]


def test_unknown_plugin() -> None:
    with pytest.raises(ValueError, match=r"^Unknown broker plugin: nope$"):
        load_brokers(["nope"], {}, registry={"fake": _fake})


def test_broker_config_extracts_prefixed_keys() -> None:
    env = {
        "LCFTP_BROKER_ALERCE_PAGE_SIZE": "50",
        "LCFTP_BROKER_FINK_X": "1",
        "OTHER": "2",
    }
    assert broker_config("alerce", env) == {"page_size": "50"}
