#!/usr/bin/env python3

import datetime
import logging
import math
from dataclasses import dataclass
from typing import Any, Self

from alerce.core import Alerce

@dataclass
class Observatory:
    lat: float
    lon: float
    max_mag: float

@dataclass
class Observation:
    ra: float
    dec: float
    oid: int
    mag: float|None = None

    @classmethod
    def from_obj(cls, obj:dict[str, Any]) -> Self:
        return cls(
            ra=obj["meanra"],
            dec=obj["meandec"],
            oid=obj["oid"]
        )
        
class Targeter:

    def __init__(self, site:Observatory,
                 constraints: dict[str,str]|None=None) -> None:
        self._site = site
        self._start = datetime.datetime.now(tz=datetime.UTC)
        self._limit_dec = self._get_limit_dec()
        self._constraints = constraints
        self._client = Alerce()
        logging.basicConfig(level=logging.INFO)
        self._logger = logging.getLogger()
        self._targets: list[Obvs] = []
        

    def _get_limit_dec(self) -> float:
        here = self._site
        if here.lat > 0:
            lim_dec = - (90 - here.lat) + 10  # 10 is a gross approximation
        else:
            lim_dec = 90 + here.lat - 10
        return lim_dec

    def _is_allowable_dec(self, obj: Obvs) -> bool:
        if self._limit_dec < 0:
            if obj.dec < self._limit_dec:
                return False
        else:
            if self._limit_dec < obj.dec:
                return False
        return True

    def _is_allowable_ra(self, obj: Obvs) -> bool:
        # Figure out how to calculate this
        return True

    def filter_objects(self, limit: int=50) -> None:
        p=0
        retval: list[Obvs] = []
        while True:
            page = self._client.query_objects(survey="lsst", format="json",
                                              page=p)
            self._logger.info(f"Page {p}")
            for entry in page:
                observation=Observation.from_obj(entry)
                if not self._is_allowable_dec(observation):
                    continue
                if not self._is_allowable_ra(observation):
                    continue
                oid = observation.oid
                lc = self._client.query_lightcurve(
                    survey="lsst", oid=oid
                )
                # Get most recent detection
                if (not lc or "detections" not in lc or
                    len(lc["detections"]) < 1):
                    self._logger.info(f"No detection data for {oid}")
                    continue
                det = lc["detections"][0]
                if "psfFlux" not in det:
                    self._logger.info(f"No flux data for {observation.oid}")
                    continue
                psf_flux=det['psfFlux']
                mag=self._nj_to_mag(psf_flux)
                observation.mag = mag
                if mag > self._site.max_mag:
                    self._logger.info(f"Skipping too-faint {oid}:mag {mag}")
                    continue
                self._targets.append(observation)
                self._logger.info(f"{oid} is observable")
                if len(self._targets) >= limit:
                    return
            p += 1

    def _nj_to_mag(self, nj: float) -> float:
        return -2.5 * math.log( 1e9 * nj / 3630.78, 10) + 8.9

import math
from datetime import datetime

def is_celestial_visible(
    ra_deg: float,
    dec_deg: float,
    lat_deg: float,
    lon_deg: float,
    utc_time: datetime,
    min_altitude_deg: float = 0.0
) -> bool:
    """
    Determines if a celestial object is geometrically visible from a given location and time.

    Args:
        ra_deg: Right Ascension in degrees (0-360)
        dec_deg: Declination in degrees (-90 to 90)
        lat_deg: Observer latitude in degrees (-90 to 90)
        lon_deg: Observer longitude in degrees (-180 to 180)
        utc_time: UTC time as a datetime object
        min_altitude_deg: Minimum altitude threshold (default 0.0 for geometric horizon)

    Returns:
        True if altitude > min_altitude_deg, False otherwise

    Vibecoded with qwen-3.6-27b
    """
    # Convert to radians
    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    # Julian Date calculation (Meeus algorithm)
    Y = utc_time.year
    M = utc_time.month
    if M <= 2:
        Y -= 1
        M += 12
    A = int(Y / 100)
    B = 2 - A + int(A / 4)
    C = 365.25 * (Y + 4716)
    D = 30.6001 * (M + 1)
    jd = C + D + utc_time.day + utc_time.hour / 24.0 + utc_time.minute / 60.0 + utc_time.second / 3600.0 + B - 1524.5

    # Greenwich Mean Sidereal Time (GMST) in degrees
    T = (jd - 2451545.0) / 36525.0
    gmst_deg = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T**2 - T**3 / 38710000.0
    gmst_deg %= 360.0

    # Local Sidereal Time (LST)
    lst_deg = (gmst_deg + lon_deg) % 360.0

    # Hour Angle (HA)
    ha_deg = lst_deg - ra_deg
    ha = math.radians(ha_deg)

    # Altitude calculation using spherical trigonometry
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
    # Clamp to [-1, 1] to handle floating-point inaccuracies
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_deg = math.degrees(math.asin(sin_alt))

    return alt_deg > min_altitude_deg

    
def main() -> None:
    ob=Observatory(lat=32, lon=-111, max_mag=20)
    tgt = Targeter(site = ob)
    tgt.filter_objects(limit=3)
    print(tgt._targets)

if __name__ == "__main__":
    main()
