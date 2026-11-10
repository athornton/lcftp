import pytest
from pydantic import ValidationError

from lcftp_models.candidate import Candidate


def test_candidate_rejects_ra_360(candidate: Candidate) -> None:
    data = candidate.model_dump()
    data["ra"] = 360.0
    with pytest.raises(ValidationError):
        Candidate.model_validate(data)


def test_candidate_requires_a_source(candidate: Candidate) -> None:
    data = candidate.model_dump()
    data["sources"] = []
    with pytest.raises(ValidationError):
        Candidate.model_validate(data)


def test_candidate_round_trip(candidate: Candidate) -> None:
    assert (
        Candidate.model_validate_json(candidate.model_dump_json()) == candidate
    )
