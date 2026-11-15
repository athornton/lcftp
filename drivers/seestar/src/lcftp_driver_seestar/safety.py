"""Hardware safety checks."""

import math
from datetime import datetime

__all__ = ["sun_altitude_deg"]

_UNIX_EPOCH_JD = 2440587.5
_J2000_JD = 2451545.0


def sun_altitude_deg(
    when: datetime, *, lat_deg: float, lon_deg: float
) -> float:
    """Geometric altitude of the Sun (degrees) at a site.

    Uses the NOAA low-precision solar position (good to ~0.01°), which
    needs no Earth-orientation tables. That matters on an observatory Pi
    that may not be able to download them.
    """
    jd = when.timestamp() / 86400 + _UNIX_EPOCH_JD
    t = (jd - _J2000_JD) / 36525
    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    anomaly = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    center = (
        math.sin(anomaly) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * anomaly) * (0.019993 - 0.000101 * t)
        + math.sin(3 * anomaly) * 0.000289
    )
    omega = math.radians(125.04 - 1934.136 * t)
    apparent_long = math.radians(
        mean_long + center - 0.00569 - 0.00478 * math.sin(omega)
    )
    obliquity = math.radians(
        23
        + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60)
        / 60
        + 0.00256 * math.cos(omega)
    )
    dec = math.asin(math.sin(obliquity) * math.sin(apparent_long))
    ra = math.atan2(
        math.cos(obliquity) * math.sin(apparent_long), math.cos(apparent_long)
    )
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - _J2000_JD)
        + 0.000387933 * t**2
        - t**3 / 38710000
    )
    hour_angle = math.radians(gmst + lon_deg) - ra
    lat = math.radians(lat_deg)
    return math.degrees(
        math.asin(
            math.sin(lat) * math.sin(dec)
            + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)
        )
    )
