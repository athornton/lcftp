"""Create the configured object store."""

from ..config import Settings
from .base import ObjectStore
from .gcs import GcsObjectStore
from .memory import MemoryObjectStore
from .s3 import S3ObjectStore

__all__ = ["create_object_store"]


def create_object_store(settings: Settings) -> ObjectStore:
    """Create the object store selected by ``LCFTP_STORAGE_BACKEND``."""
    match settings.storage_backend:
        case "s3":
            endpoint = settings.storage_endpoint_url
            return S3ObjectStore(
                settings.storage_bucket,
                endpoint_url=str(endpoint) if endpoint else None,
                region=settings.storage_region,
            )
        case "gcs":
            return GcsObjectStore(settings.storage_bucket)
        case "memory":
            return MemoryObjectStore()
