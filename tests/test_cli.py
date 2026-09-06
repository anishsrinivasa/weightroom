from contextlib import contextmanager
from types import SimpleNamespace

from typer.testing import CliRunner

from keystone.cli import _has_pending_certification, app


def test_dev_command_is_available_for_the_managed_local_stack() -> None:
    result = CliRunner().invoke(app, ["dev", "--help"])

    assert result.exit_code == 0
    assert "local API and certification worker" in result.output
    assert "--worker-interval" in result.output


def test_idle_worker_checks_local_queue_before_opening_modal() -> None:
    class Store:
        @contextmanager
        def session(self):
            yield object()

        def listings_in_state(self, session, state):
            return [SimpleNamespace(id="listing-1")]

    store = Store()
    assert _has_pending_certification(store)
    assert _has_pending_certification(store, listing_id="listing-1")
    assert not _has_pending_certification(store, listing_id="listing-2")
