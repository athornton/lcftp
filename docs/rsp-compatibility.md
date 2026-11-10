# RSP compatibility

The observatory client (`lcftp-client`) and the shared models
(`lcftp-models`) must run in the Rubin Science Platform notebook kernel so
that the quickstart notebook works there.

## Kernel

- Source: `sciplat-conda-stack.yaml` from the RSP image, supplied
  2026-11-05.
- Python 3.13.18. `lcftp-models` and `lcftp-client` therefore declare
  `requires-python = ">=3.13"`; the server and `lcftp-auth` require 3.14.

## Runtime requirements

All present in the kernel; nothing needs an RFC.

| Package | Needed by | RSP kernel |
| --- | --- | --- |
| pydantic | models, client | 2.14.2 |
| google-crc32c | models | 1.9.0 |
| httpx | client | 0.28.1 |
| click | client | 8.5.0 |
| astropy | client | 8.0.2 |
| numpy | client | 2.4.6 |
| tomli-w | client | 1.2.0 |
| matplotlib (optional, notebook plot) | notebook | 3.10.9 (`matplotlib-base`) |

## To RFC

None.

## Keeping this current

`client/tests/rsp_compat_test.py` checks every runtime requirement of
`lcftp-client` and `lcftp-models` against
`client/tests/data/rsp-kernel-packages.txt`. When the RSP image changes,
regenerate that file from the new `sciplat-conda-stack.yaml`:

```sh
grep -E '^\s*- [A-Za-z0-9_.-]+=[^=]+=' sciplat-conda-stack.yaml \
  | sed -E 's/^\s*- ([^=]+)=([^=]+)=.*/\1==\2/' | sort -u
```

