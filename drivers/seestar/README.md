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

## Configuration

The driver configuration (the `driver_config` table in the client
configuration) accepts:

| Key | Default | Meaning |
| --- | --- | --- |
| `site_lat_deg`, `site_lon_deg` | required | Observatory location, for the Sun-altitude safety check |
| `site_elevation_m` | 0 | Observatory elevation |
| `host` | discovery | Telescope IP address or host name |
| `key_path` | none | Path to the authentication key (PEM) |
| `sub_exposure_s` | 10 | Sub-frame exposure: 2, 5, 10, 20, 30 or 60 s |
| `default_total_exposure_s` | 60 | Total exposure when a plan item gives only a target SNR |
| `goto_timeout_s` | 180 | Give up on a slew after this long |
| `max_pointing_error_fov` | 0.25 | Largest acceptable pointing error, as a fraction of the field |
| `poll_interval_s` | 5 | How often to poll stacking progress |
| `sun_altitude_limit_deg` | -6 | Refuse to slew while the Sun is above this altitude |
