"""Load telescope drivers from entry points."""

from collections.abc import Mapping
from importlib.metadata import entry_points

from pydantic import JsonValue

from .base import DriverFactory, TelescopeDriver

__all__ = ["ENTRY_POINT_GROUP", "load_driver"]

ENTRY_POINT_GROUP = "lcftp.drivers"


def load_driver(
    name: str,
    config: Mapping[str, JsonValue],
    *,
    registry: Mapping[str, DriverFactory] | None = None,
) -> TelescopeDriver:
    """Instantiate the named driver plugin.

    Raises
    ------
    ValueError
        If no driver of that name is installed.
    """
    if registry is None:
        eps = {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}
        if name not in eps:
            msg = f"Unknown telescope driver: {name}"
            raise ValueError(msg)
        factory: DriverFactory = eps[name].load()
    elif name in registry:
        factory = registry[name]
    else:
        msg = f"Unknown telescope driver: {name}"
        raise ValueError(msg)
    return factory(config)
