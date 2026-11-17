"""Windows-only: make fixture Path.write_text write LF like POSIX does."""

from __future__ import annotations

import os
from pathlib import Path

if os.name == "nt" and not getattr(Path.write_text, "_lf_default", False):
    _original = Path.write_text

    def _write_text(self, data, encoding=None, errors=None, newline=None):
        return _original(self, data, encoding, errors, "\n" if newline is None else newline)

    _write_text._lf_default = True
    Path.write_text = _write_text
