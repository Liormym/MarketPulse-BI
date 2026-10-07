import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_daily_update", ROOT / "scripts" / "run_daily_update.py")
orchestrator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(orchestrator)


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
