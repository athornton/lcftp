"""Export JSON Schemas of the contract for the snapshot test."""

from pydantic import BaseModel, JsonValue

from .candidate import BrokerRef, Candidate
from .errors import ErrorResponse
from .observatory import ObservatoryCreate, ObservatoryProfile
from .plan import (
    AvailabilityWindow,
    ExposureHint,
    ObservationPlan,
    PlanItem,
    PlanStatus,
)
from .site import FilterSpec, ObservingConstraints, SiteLocation
from .upload import (
    Checksums,
    FrameRecord,
    ItemOutcome,
    SignedUpload,
    UploadManifest,
    UploadResult,
    UploadTicket,
)

__all__ = ["CONTRACT_MODELS", "export_schema"]

CONTRACT_MODELS: tuple[type[BaseModel], ...] = (
    AvailabilityWindow,
    BrokerRef,
    Candidate,
    Checksums,
    ErrorResponse,
    ExposureHint,
    FilterSpec,
    FrameRecord,
    ItemOutcome,
    ObservationPlan,
    ObservatoryCreate,
    ObservatoryProfile,
    ObservingConstraints,
    PlanItem,
    PlanStatus,
    SignedUpload,
    SiteLocation,
    UploadManifest,
    UploadResult,
    UploadTicket,
)


def export_schema() -> dict[str, JsonValue]:
    """Return the JSON Schema of every contract model, keyed by name."""
    return {
        model.__name__: model.model_json_schema()
        for model in sorted(CONTRACT_MODELS, key=lambda m: m.__name__)
    }
