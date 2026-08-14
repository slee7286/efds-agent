"""Make checkout-local scripts work without an editable install."""

import sys
from pathlib import Path

if sys.version_info < (3, 12):  # noqa: UP036
    raise SystemExit(
        "efds-agent requires Python 3.12+. "
        "Create a 3.12+ virtual environment and run the setup commands in README.md."
    )

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
