# lcftp-driver-seestar

An LCFTP telescope driver for ZWO Seestar smart telescopes (developed on
an S30 Plus), built on [seestarpy](https://pypi.org/project/seestarpy/).

## Licensing

`seestarpy` is licensed under the GPLv3, so this package is licensed
**GPL-3.0-or-later** (see `LICENSE`). The rest of LCFTP, including
`lcftp-client`, is MIT-licensed and does not depend on this package; the
client finds it only through the `lcftp.drivers` entry point. Install it
alongside the client on an observatory that uses a Seestar:

    pip install lcftp-client lcftp-driver-seestar

## Authentication key

Seestar firmware 7.18 and later requires an RSA challenge-response
handshake. LCFTP does not ship, download or extract the key. If you
supply one, set `key_path` in the driver configuration (or the
`SEESTAR_KEY_PATH` environment variable). Without a key, connecting to
newer firmware fails with an error that says so.

The key is embedded in `libopenssllib.so` inside the Seestar Android app.
As of Seestar app v3.4.1 (packaged as an `.xapk`) that library, and so
the key, is in the `config.arm64_v8a.apk` ABI split rather than the base
apk, so point `seestarpy`'s extractor at that split:

    python -m seestarpy.extract_pem config.arm64_v8a.apk

(`seestarpy` 0.8's extractor expects the key in the base apk; a fix for
the 3.4+ split layout is in progress upstream.)

## Configuration

The driver configuration (the `driver_config` table in the client
configuration) accepts:

| Key | Default | Meaning |
| --- | --- | --- |
| `site_lat_deg`, `site_lon_deg` | required | Observatory location for the Sun-altitude safety check. Longitude is **east-positive** (west is negative, e.g. Utah is about -111). The driver refuses to connect if this is more than 1° from the location the telescope reports. |
| `site_elevation_m` | 0 | Observatory elevation |
| `host` | discovery | Telescope IP address or host name |
| `key_path` | none | Path to the authentication key (PEM) |
| `sub_exposure_s` | 10 | Sub-frame exposure: 2, 5, 10, 20, 30 or 60 s |
| `default_total_exposure_s` | 60 | Total exposure when a plan item gives only a target SNR |
| `goto_timeout_s` | 180 | Give up on a slew after this long |
| `max_pointing_error_fov` | 0.25 | Largest acceptable pointing error, as a fraction of the field |
| `poll_interval_s` | 5 | How often to poll stacking progress |
| `sun_altitude_limit_deg` | -6 | Refuse to slew while the Sun is above this altitude |

## Safety behavior

- The driver refuses to slew while the Sun is above
  `sun_altitude_limit_deg` at the configured site.
- Every firmware reply is checked; a rejected command raises an error.
- Parking waits until the firmware reports the arm is home, and fails
  loudly otherwise.
- A failed or abandoned goto stops the whole view (goto, plate solve and
  stack) on the telescope, and the next command waits for the slew to
  finish.

## Recording a session for the tests

`scripts/record-seestar-session.py` runs this driver against a real
telescope through the same sequence the driver contract test uses, and
records every call. See the script's `--help`. Recordings keep no pixel
data, no serial numbers, no network addresses and no site location.
