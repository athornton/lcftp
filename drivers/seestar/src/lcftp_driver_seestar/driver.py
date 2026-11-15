"""The Seestar telescope driver."""

from collections.abc import Mapping

from pydantic import JsonValue

__all__ = ["create_driver"]


def create_driver(config: Mapping[str, JsonValue]) -> object:
    """Create the driver (implemented in a later task)."""
    raise NotImplementedError
