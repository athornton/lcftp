from datetime import UTC, date, datetime

from lcftp.planner.night import observing_night


def test_night_in_phoenix() -> None:
    when = datetime(2026, 11, 10, 5, tzinfo=UTC)
    assert observing_night(when, "America/Phoenix") == date(2026, 11, 9)


def test_morning_belongs_to_previous_night() -> None:
    when = datetime(2026, 11, 10, 13, tzinfo=UTC)  # 06:00 in Phoenix
    assert observing_night(when, "America/Phoenix") == date(2026, 11, 9)


def test_evening_belongs_to_that_night() -> None:
    when = datetime(2026, 11, 10, 2, tzinfo=UTC)  # 19:00 the 9th in Phoenix
    assert observing_night(when, "America/Phoenix") == date(2026, 11, 9)


def test_dateline_site() -> None:
    when = datetime(2026, 11, 10, 9, tzinfo=UTC)  # 21:00 on the 10th in Fiji
    assert observing_night(when, "Pacific/Fiji") == date(2026, 11, 10)
