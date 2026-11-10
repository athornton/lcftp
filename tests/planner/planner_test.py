from datetime import UTC, datetime, timedelta
from itertools import pairwise

import astropy.units as u
from astroplan import FixedTarget
from astropy.coordinates import SkyCoord
from astropy.time import Time

from lcftp.brokers.testing import make_candidate
from lcftp.planner.planner import plan_observations
from lcftp.planner.settings import PlannerSettings
from lcftp.planner.visibility import to_observer
from lcftp_models.candidate import Candidate
from lcftp_models.observatory import ObservatoryProfile
from lcftp_models.plan import AvailabilityWindow, ObservationPlan
from lcftp_models.site import FilterSpec, SiteLocation

TUCSON = SiteLocation(
    latitude=32.23,
    longitude=-110.95,
    elevation_m=728,
    timezone="America/Phoenix",
)
NIGHT = AvailabilityWindow(
    start=datetime(2026, 11, 10, 2, tzinfo=UTC),
    end=datetime(2026, 11, 10, 12, tzinfo=UTC),
)
ALERT = datetime(2026, 11, 10, 1, tzinfo=UTC)
SETTINGS = PlannerSettings()


def _profile(
    site: SiteLocation = TUCSON, limit: float = 15.0
) -> ObservatoryProfile:
    return ObservatoryProfile(
        id="obs1",
        owner="athornton",
        name="Backyard",
        site=site,
        aperture_m=0.05,
        limiting_mag=limit,
        fov_arcmin=77,
        filters=[
            FilterSpec(name="g", wavelength_nm=480),
            FilterSpec(name="r", wavelength_nm=620),
        ],
    )


def _target(oid: int, ra: float, dec: float, mag: float = 12.0) -> Candidate:
    return make_candidate(oid, ra=ra, dec=dec, mag=mag, when=ALERT)


def test_never_rising_target_is_not_planned() -> None:
    items = plan_observations(
        _profile(), NIGHT, [_target(1, 45, -80)], {}, SETTINGS
    )
    assert items == []


def test_visible_target_is_planned() -> None:
    items = plan_observations(
        _profile(), NIGHT, [_target(1, 45, 20)], {}, SETTINGS
    )
    assert [i.candidate.dia_object_id for i in items] == [1]
    item = items[0]
    assert item.suggested_filter == "r"
    assert item.exposure_hint.total_exposure_s == 300.0
    assert item.priority == 0
    assert item.item_id == "000"
    assert item.not_after - item.not_before == timedelta(minutes=10)


def test_too_faint_target_is_excluded() -> None:
    items = plan_observations(
        _profile(), NIGHT, [_target(1, 45, 20, mag=16)], {}, SETTINGS
    )
    assert items == []


def test_constraints_hold_at_both_ends() -> None:
    targets = [
        _target(i, ra, dec)
        for i, (ra, dec) in enumerate(
            [
                (0, 10),
                (30, 30),
                (45, 20),
                (60, 0),
                (90, 40),
                (120, 10),
                (330, -10),
            ]
        )
    ]
    items = plan_observations(_profile(), NIGHT, targets, {}, SETTINGS)
    assert items
    observer = to_observer(TUCSON)
    for item in items:
        target = FixedTarget(
            SkyCoord(item.candidate.ra * u.deg, item.candidate.dec * u.deg)
        )
        for when in (item.not_before, item.not_after):
            altaz = observer.altaz(Time(when), target)
            assert altaz.alt.deg >= 30 - 1e-6
            assert altaz.secz <= 2.0 + 1e-6
    ObservationPlan(
        plan_id="p",
        observatory_id="obs1",
        window=NIGHT,
        created_at=NIGHT.start,
        candidates_as_of=None,
        planner_version="1",
        items=items,
    )
    starts = [i.not_before for i in items]
    assert starts == sorted(starts)


def test_cap_lowers_priority() -> None:
    a, b = _target(1, 45, 20), _target(2, 45.5, 20)
    items = plan_observations(_profile(), NIGHT, [a, b], {1: 3}, SETTINGS)
    by_id = {i.candidate.dia_object_id: i for i in items}
    assert by_id[2].priority == 0
    assert by_id[1].priority == 1
    assert by_id[2].not_before < by_id[1].not_before


def test_daytime_window_is_empty() -> None:
    day = AvailabilityWindow(
        start=datetime(2026, 11, 10, 18, tzinfo=UTC),
        end=datetime(2026, 11, 10, 22, tzinfo=UTC),
    )
    assert (
        plan_observations(_profile(), day, [_target(1, 45, 20)], {}, SETTINGS)
        == []
    )


def test_window_shorter_than_slot_is_empty() -> None:
    short = AvailabilityWindow(
        start=datetime(2026, 11, 10, 7, tzinfo=UTC),
        end=datetime(2026, 11, 10, 7, 5, tzinfo=UTC),
    )
    assert (
        plan_observations(
            _profile(), short, [_target(1, 45, 20)], {}, SETTINGS
        )
        == []
    )


def test_mauna_kea_window_across_utc_midnight() -> None:
    mauna_kea = SiteLocation(
        latitude=19.82,
        longitude=-155.47,
        elevation_m=4200,
        timezone="Pacific/Honolulu",
    )
    window = AvailabilityWindow(
        start=datetime(2026, 11, 10, 5, tzinfo=UTC),
        end=datetime(2026, 11, 10, 15, tzinfo=UTC),
    )
    items = plan_observations(
        _profile(mauna_kea), window, [_target(1, 45, 20)], {}, SETTINGS
    )
    assert len(items) == 1


def test_dateline_site_does_not_raise() -> None:
    fiji = SiteLocation(
        latitude=-18.1,
        longitude=179.9,
        elevation_m=10,
        timezone="Pacific/Fiji",
    )
    window = AvailabilityWindow(
        start=datetime(2026, 11, 10, 9, tzinfo=UTC),
        end=datetime(2026, 11, 10, 17, tzinfo=UTC),
    )
    items = plan_observations(
        _profile(fiji), window, [_target(1, 45, -10)], {}, SETTINGS
    )
    assert all(i.not_before >= window.start for i in items)


def test_many_targets_are_packed_without_overlap() -> None:
    targets = [
        _target(i, (i * 7) % 360, (i * 13) % 80 - 20) for i in range(200)
    ]
    items = plan_observations(_profile(), NIGHT, targets, {}, SETTINGS)
    assert len(items) > 10
    for prev, item in pairwise(items):
        assert item.not_before >= prev.not_after
