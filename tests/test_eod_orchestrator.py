import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_daily_update", ROOT / "scripts" / "run_daily_update.py")
orchestrator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orchestrator)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_db_stamp(monkeypatch):
    """Orchestrator tests must never write the real "last EOD update" timestamp."""
    stamped = []
    monkeypatch.setattr(orchestrator, "_record_success", lambda: stamped.append(1))
    return stamped


def test_steps_run_in_order_on_success(tmp_path, monkeypatch):
    log = tmp_path / "order.txt"
    steps = [
        (f"step{i}", [sys.executable, "-c", f"open({str(log)!r}, 'a').write('{i}\\n')"])
        for i in range(3)
    ]
    monkeypatch.setattr(orchestrator, "STEPS", steps)

    orchestrator.main()

    assert log.read_text().split() == ["0", "1", "2"]


def test_pipeline_stops_at_first_failing_step(tmp_path, monkeypatch):
    marker = tmp_path / "ran_after_failure"
    monkeypatch.setattr(
        orchestrator,
        "STEPS",
        [
            ("fails", [sys.executable, "-c", "import sys; sys.exit(3)"]),
            ("must not run", [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]),
        ],
    )

    try:
        orchestrator.main()
    except SystemExit as exc:
        assert exc.code == 3
    else:
        raise AssertionError("main() must exit non-zero when a step fails")

    assert not marker.exists()


def test_completion_is_stamped_only_when_every_step_succeeds(tmp_path, monkeypatch, _no_db_stamp):
    ok = [("ok", [sys.executable, "-c", "pass"])]
    monkeypatch.setattr(orchestrator, "STEPS", ok)
    orchestrator.main()
    assert _no_db_stamp == [1]

    monkeypatch.setattr(orchestrator, "STEPS", ok + [("bad", [sys.executable, "-c", "import sys; sys.exit(1)"])])
    try:
        orchestrator.main()
    except SystemExit:
        pass
    assert _no_db_stamp == [1]  # unchanged by the failed run
