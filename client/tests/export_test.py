import csv
import io

from lcftp_client.export import (
    CSV_COLUMNS,
    format_dec,
    format_ra,
    write_plan_csv,
)
from lcftp_models.plan import ObservationPlan


def test_format_ra() -> None:
    assert format_ra(83.82208) == "05h 35m 17.30s"
    assert format_ra(359.9999999) == "00h 00m 00.00s"


def test_format_dec() -> None:
    assert format_dec(-5.39111) == "-05d 23m 28.0s"
    assert format_dec(-0.5) == "-00d 30m 00.0s"
    assert format_dec(10.0) == "+10d 00m 00.0s"
    assert format_dec(29.9999999) == "+30d 00m 00.0s"
    assert format_dec(-90.0) == "-90d 00m 00.0s"


def test_write_plan_csv(plan: ObservationPlan) -> None:
    buf = io.StringIO()
    write_plan_csv(plan, buf)
    text = buf.getvalue()
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert CSV_COLUMNS[:5] == (
        "Familiar Name",
        "Catalogue Entry",
        "Right Ascension (J2000)",
        "Declination (J2000)",
        "Position Angle (East)",
    )
    assert len(rows) == 3
    r = rows[1]
    assert r["Familiar Name"] == "lcftp-plan1-001"
    assert r["Catalogue Entry"] == "diaObject 101"
    assert r["Right Ascension (J2000)"] == "02h 04m 00.00s"
    assert r["Declination (J2000)"] == "+10d 00m 00.0s"
    assert r["Position Angle (East)"] == "0"
    assert r["Not Before (UTC)"] == "2026-11-10T03:10:00Z"
    assert r["Not After (UTC)"] == "2026-11-10T03:20:00Z"
    assert r["RA (deg)"] == "31.000000"
    assert r["Dec (deg)"] == "10.000000"
    assert r["Filter"] == "r"
    assert r["Total Exposure (s)"] == "300"
    assert r["Target SNR"] == ""
    assert r["Mag"] == "12.00"
    assert r["Band"] == "r"
    assert r["Classification"] == ""
    assert r["Priority"] == "1"
    assert r["Dia Object ID"] == "101"
    assert r["Plan ID"] == "plan1"
    assert r["Item ID"] == "001"
    assert '"' not in text
