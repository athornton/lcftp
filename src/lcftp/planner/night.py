"""Which observing night a time belongs to."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

__all__ = ["observing_night"]


def observing_night(when: datetime, tz: str) -> date:
    """Return the local calendar date on which this night began.

    Times are shifted back twelve hours in the site's timezone, so an
    evening and the following morning share one night.
    """
    return (when.astimezone(ZoneInfo(tz)) - timedelta(hours=12)).date()
