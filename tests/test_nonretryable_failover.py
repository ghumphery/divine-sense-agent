"""v0.9.96.1 — non-retryable HTTP statuses skip the attempt-2 retry
and go STRAIGHT to cross-provider failover.

Log evidence (gui/app.log 2026-09-23, four separate turns): every
OpenRouter 402 (Payment Required) turn paid TWO wasted create attempts
(~1s + 2 API round-trips) before the failover walk even started —
retrying the SAME payload can never recover an auth/credit/permission
failure. Deterministic statuses (401/402/403) now break out of the
primary retry loop after attempt 1; transient statuses (429/5xx) keep
the 2-attempt behaviour.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from openai import APIStatusError

import src.agent.agent as A


class _Resp:
    """httpx.Response duck-type — just enough for APIStatusError."""

    status_code = 500
    headers: dict = {}
    request = object()


def _api_error(status: int) -> APIStatusError:
    return APIStatusError(
        message=f"HTTP {status}",
        response=type("R", (_Resp,), {"status_code": status})(),
        body=None,
    )


class _Stub:
    """Client stub whose completions.create counts calls and raises."""

    def __init__(self, exc_factory):
        self.calls = 0
        self._exc_factory = exc_factory

    @property
    def chat(self):
        outer = self

        class _C:
            class completions:
                pass

        class _Completions:
            @staticmethod
            def create(**kw):
                outer.calls += 1
                raise outer._exc_factory()

        _C.completions = _Completions()
        return _C()


def _agent_with(primary: _Stub) -> A.Agent:
    agent = object.__new__(A.Agent)
    agent._provider = "ollama"
    agent._model = "stub-model"
    agent.last_provider = None
    agent._client = primary  # the lazy property reads _client
    return agent


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    monkeypatch.setattr(A, "_EMPTY_RESPONSE_RETRY_DELAY", 0)


def test_402_fails_over_after_single_attempt(monkeypatch):
    primary = _Stub(lambda: _api_error(402))
    agent = _agent_with(primary)
    fallback = _Stub(lambda: _api_error(500))
    monkeypatch.setattr(A.Agent, "_failover_candidates",
                        lambda self: [("openai", "m2", fallback)])
    with pytest.raises(APIStatusError):
        A.Agent._create_validated(agent, messages=[{"role": "user"}])
    assert primary.calls == 1  # NO attempt 2 on 402
    assert fallback.calls == 1  # failover walked


@pytest.mark.parametrize("status", [401, 402, 403])
def test_auth_credit_statuses_skip_retry(monkeypatch, status):
    primary = _Stub(lambda: _api_error(status))
    agent = _agent_with(primary)
    fallback = MagicMock()
    fallback.chat.completions.create.return_value = object.__new__(object)
    monkeypatch.setattr(
        A.Agent, "_failover_candidates",
        lambda self: [("openai", "m2", fallback)],
    )
    # failover succeeds with a valid completion object
    resp = MagicMock()
    resp.choices = [MagicMock()]
    fallback.chat.completions.create.return_value = resp
    out, ok = A.Agent._create_validated(agent, messages=[])
    assert ok is True
    assert primary.calls == 1
    assert agent.last_provider == "openai"


@pytest.mark.parametrize("status", [429, 500])
def test_transient_statuses_still_retry_twice(monkeypatch, status):
    primary = _Stub(lambda: _api_error(status))
    agent = _agent_with(primary)
    monkeypatch.setattr(A.Agent, "_failover_candidates", lambda self: [])
    with pytest.raises(APIStatusError):
        A.Agent._create_validated(agent, messages=[])
    assert primary.calls == 2  # transient keeps the 2-attempt retry