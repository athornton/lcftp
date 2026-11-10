import base64
from pathlib import Path

import google_crc32c

from lcftp_models.checksums import compute_checksums


def test_compute_checksums(tmp_path: Path) -> None:
    path = tmp_path / "hello.txt"
    path.write_bytes(b"hello\n")
    sums = compute_checksums(path)
    assert sums.sha256 == (
        "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )
    assert sums.md5_b64 == "sZRqySSS0jR8YjW00mERhA=="
    expected = google_crc32c.value(b"hello\n").to_bytes(4, "big")
    assert sums.crc32c_b64 == base64.b64encode(expected).decode()


def test_compute_checksums_streams(tmp_path: Path) -> None:
    data = bytes(range(256)) * 10_000
    path = tmp_path / "big.bin"
    path.write_bytes(data)
    assert compute_checksums(path, chunk_size=1000) == compute_checksums(path)
