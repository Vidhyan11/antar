"""Load credentials from a local .env file.

Keeps secrets out of shell history and off command lines. `.env` is gitignored,
so nothing here can be committed by accident.

No dependency on python-dotenv: the format we need is a dozen lines of parsing,
and one fewer install is one fewer thing to go wrong on a fresh clone.

Real environment variables always win. If a value is already exported, the file
does not override it -- CI and containers set variables the normal way, and a
stray .env should never silently take precedence over them.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Read KEY=value lines into os.environ. Returns what was actually set."""
    target = Path(path) if path else ENV_PATH
    if not target.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def redact(secret: str, keep: int = 8) -> str:
    """Show enough of a key to confirm which one it is, never enough to use it."""
    if not secret:
        return "(unset)"
    # ASCII only: Windows terminals default to cp1252 and an ellipsis raises.
    return secret[:keep] + "..." + f"({len(secret)} chars)"
