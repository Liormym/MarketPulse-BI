"""The portfolio page's money maths (webapp/static/js/portfolio.js) is plain JavaScript;
node's built-in test runner executes tests/js/portfolio_math.test.js. Skipped without node."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_portfolio_javascript_math():
    done = subprocess.run(
        ["node", "--test", str(ROOT / "tests" / "js" / "portfolio_math.test.js")], capture_output=True, text=True, cwd=ROOT, timeout=60
    )
    assert done.returncode == 0, done.stdout + done.stderr
