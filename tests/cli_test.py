from click.testing import CliRunner

from lcftp.cli import main


def test_cli_lists_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ("web", "worker", "migrate"):
        assert command in result.output
