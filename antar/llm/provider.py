"""The model layer.

Three requirements shaped this, in order of importance.

1.  **A judge must be able to clone the repo and run the demo with no API key.**
    Every model call is recorded to a fixture on first execution and replayed
    from disk thereafter. The fixtures are committed. Nothing about the demo
    depends on a network, a quota, or a key.
2.  **Free tiers shift without warning.** Everything sits behind one small
    interface, so swapping Gemini for Groq or a local model is an adapter, not
    a refactor.
3.  **The model must never be load-bearing for correctness.** Its output is
    parsed into a schema and validated before it can affect anything. A
    deterministic fallback produces a valid verdict when no model is reachable,
    so the pipeline degrades rather than breaks -- and says so out loud, because
    a system quietly running on its fallback while claiming to be agentic would
    be exactly the sort of dishonesty this project is about.

Resolution order: fixture -> live provider (if a key is present) -> deterministic
fallback. Whichever answered is recorded on the result and surfaced in the
console, so it is always visible which one you are looking at.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "llm"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class Completion:
    data: dict[str, Any]
    source: str          # "fixture" | "gemini" | "fallback"
    prompt_hash: str

    @property
    def is_live_model(self) -> bool:
        return self.source not in ("fallback",)


def prompt_key(prompt: str, schema_name: str) -> str:
    return hashlib.sha256(f"{schema_name}\x1f{prompt}".encode()).hexdigest()[:20]


class LLMProvider(Protocol):
    name: str

    def complete_json(self, prompt: str, schema_name: str) -> dict[str, Any] | None: ...


class GeminiProvider:
    """Google Gemini via the REST API.

    Uses urllib rather than a vendor SDK: one less dependency, and the request
    shape stays visible in the source where it can be read and checked.
    """

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.model = model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, prompt: str, schema_name: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.0},
        }).encode()
        url = GEMINI_ENDPOINT.format(model=self.model) + f"?key={self.api_key}"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, TimeoutError):
            # A model that is unreachable, rate-limited or returns malformed
            # JSON must not take the pipeline down with it.
            return None


class DeterministicFallback:
    """A rule-based stand-in, used only when no model is reachable.

    It exists so the pipeline never breaks and CI never depends on a network.
    It is deliberately unsophisticated, and everything it produces is labelled
    `fallback` all the way through to the console -- a demo silently running on
    canned rules while presenting itself as agentic would be the same kind of
    dishonesty this project exists to criticise.
    """

    name = "fallback"

    def complete_json(
        self, prompt: str, schema_name: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if schema_name != "incident_verdict":
            return None
        facts = context or {}
        systemic = facts.get("lift_vs_baseline", 0) >= 4.0 and \
            facts.get("share_transient_rail_class", 0) >= 0.7
        return {
            "is_systemic": systemic,
            "confidence": 0.6 if systemic else 0.5,
            "hypothesis": (
                f"{facts.get('issuer', 'unknown')} {facts.get('method', '')} failures ran "
                f"{facts.get('lift_vs_baseline', 0):.0f}x above baseline with "
                f"{facts.get('share_transient_rail_class', 0):.0%} in the transient-rail class."
                if systemic else
                "Elevated failures are not concentrated in the transient-rail class."
            ),
            "recommended_action": "freeze_and_reroute" if systemic else "release_to_targeting",
            "note": (
                "Rule-based verdict: no model was reachable. "
                "Concentration and lift both breach the systemic thresholds."
                if systemic else
                "Rule-based verdict: no model was reachable. Pattern does not match a rail incident."
            ),
        }


class CachingProvider:
    """Fixture-first resolution with record-on-miss."""

    name = "caching"

    def __init__(
        self,
        upstream: LLMProvider | None = None,
        fixture_dir: Path = FIXTURE_DIR,
        *,
        record: bool = True,
    ) -> None:
        self.upstream = upstream
        self.fixture_dir = Path(fixture_dir)
        self.record = record
        self.fallback = DeterministicFallback()
        self.fixture_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.fixture_dir / f"{key}.json"

    def complete(
        self, prompt: str, schema_name: str, context: dict[str, Any] | None = None
    ) -> Completion:
        """Resolve a completion.

        `context` is the structured evidence the caller already holds. The
        fallback consumes it directly rather than trying to recover it from the
        prompt text -- an earlier version scraped the prompt for a JSON block and
        silently swallowed the schema example printed alongside it, so every
        verdict came back empty and every incident was released.
        """
        key = prompt_key(prompt, schema_name)
        path = self._path(key)

        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            return Completion(cached["data"], cached.get("source", "fixture"), key)

        if self.upstream is not None:
            data = self.upstream.complete_json(prompt, schema_name)
            if data is not None:
                if self.record:
                    path.write_text(
                        json.dumps({"source": self.upstream.name, "prompt": prompt, "data": data},
                                   indent=2),
                        encoding="utf-8",
                    )
                return Completion(data, self.upstream.name, key)

        data = self.fallback.complete_json(prompt, schema_name, context)
        return Completion(data or {}, "fallback", key)


def default_provider(*, record: bool = True) -> CachingProvider:
    gemini = GeminiProvider()
    return CachingProvider(gemini if gemini.available else None, record=record)
