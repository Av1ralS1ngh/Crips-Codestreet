"""Deliberately vulnerable shopping agent + injection harness.

This package is the attacker's side of the demo. It runs from the repo root
(`python -m agent.shopper ...`), so `backend/` is put on the import path here —
the kernel under test lives there and is imported, never copied.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
