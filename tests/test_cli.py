from typer.testing import CliRunner

from keystone.cli import app


def test_dev_command_is_available_for_the_managed_local_stack() -> None:
    result = CliRunner().invoke(app, ["dev", "--help"])

    assert result.exit_code == 0
    assert "local API and certification worker" in result.output
    assert "--worker-interval" in result.output
