"""The threaded heartbeat loop lets a live chat turn go first."""

from services import hexis_rlm

_CFG = {"provider": "openai_compatible", "model": "m", "endpoint": "http://x/v1", "api_key": "k"}


async def _fake_completion(**kwargs):
    return {"content": "ok", "raw": None}


async def test_heartbeat_completion_checks_for_chat(monkeypatch):
    seen = []

    async def _yield(dsn):
        seen.append(dsn)

    monkeypatch.setattr(hexis_rlm, "_yield_to_chat", _yield)
    monkeypatch.setattr(hexis_rlm, "chat_completion", _fake_completion)
    out = await hexis_rlm._llm_completion([{"role": "user", "content": "hi"}], _CFG, yield_to_chat_dsn="postgresql://db")
    assert out == "ok" and seen == ["postgresql://db"]


async def test_chat_completion_path_does_not_wait(monkeypatch):
    seen = []

    async def _yield(dsn):
        seen.append(dsn)

    monkeypatch.setattr(hexis_rlm, "_yield_to_chat", _yield)
    monkeypatch.setattr(hexis_rlm, "chat_completion", _fake_completion)
    await hexis_rlm._llm_completion([{"role": "user", "content": "hi"}], _CFG)
    assert seen == []
