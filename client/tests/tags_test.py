from lcftp_client.tags import TagRef, find_tags, target_tag


def test_target_tag() -> None:
    assert target_tag("3f9a1c2e" + "0" * 24, "007") == "lcftp-3f9a1c2e-007"


def test_find_tags_in_nina_filename() -> None:
    name = "2026-11-24_LCFTP-3F9A1C2E-007_LIGHT_r_60.00s_0003.fits"
    assert find_tags(name) == [TagRef("3f9a1c2e", "007")]


def test_find_tags_none_and_multiple() -> None:
    assert find_tags("M42_LIGHT.fits") == []
    text = "lcftp-aaaaaaaa-001 lcftp-bbbbbbbb-002 lcftp-aaaaaaaa-001"
    assert find_tags(text) == [
        TagRef("aaaaaaaa", "001"),
        TagRef("bbbbbbbb", "002"),
    ]


def test_find_tags_rejects_short_prefix() -> None:
    assert find_tags("lcftp-3f9a1c2-007") == []
    assert find_tags("lcftp-3f9a1c2e0-007") == []
