"""Put the backend package root on sys.path so `pytest` works as a console script.

Without this, `pytest tests -q` fails collection with "No module named 'caveat'" — modern
pytest does not add the invocation directory to sys.path. `python -m pytest` happens to
work because the interpreter adds cwd itself; this file makes both invocations equivalent.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
