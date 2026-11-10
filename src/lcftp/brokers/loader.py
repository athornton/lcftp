"""Load broker plugins from entry points."""

from collections.abc import Mapping, Sequence
from importlib.metadata import entry_points

from .base import BrokerFactory, BrokerSource

__all__ = ["ENTRY_POINT_GROUP", "broker_config", "load_brokers"]

ENTRY_POINT_GROUP = "lcftp.brokers"


def broker_config(name: str, env: Mapping[str, str]) -> dict[str, str]:
    """Collect ``LCFTP_BROKER_<NAME>_<KEY>`` variables as ``{key: value}``."""
    prefix = f"LCFTP_BROKER_{name.upper()}_"
    return {
        key.removeprefix(prefix).lower(): value
        for key, value in env.items()
        if key.startswith(prefix)
    }


def _registry() -> dict[str, BrokerFactory]:
    return {ep.name: ep.load() for ep in entry_points(group=ENTRY_POINT_GROUP)}


def load_brokers(
    names: Sequence[str],
    env: Mapping[str, str],
    *,
    registry: Mapping[str, BrokerFactory] | None = None,
) -> list[BrokerSource]:
    """Instantiate the named broker plugins.

    Parameters
    ----------
    names
        Plugin names, as in the ``LCFTP_BROKERS`` setting.
    env
        Environment from which plugin configuration is read.
    registry
        Factories by name; defaults to the ``lcftp.brokers`` entry points.

    Raises
    ------
    ValueError
        If a name is not a known plugin.
    """
    factories = registry if registry is not None else _registry()
    brokers = []
    for name in names:
        if name not in factories:
            msg = f"Unknown broker plugin: {name}"
            raise ValueError(msg)
        brokers.append(factories[name](broker_config(name, env)))
    return brokers
