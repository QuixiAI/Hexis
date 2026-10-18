"""Text is cleaned before it is read into memory, and new documents don't wait."""
from __future__ import annotations

import asyncio

from services.ingest.textclean import clean_document_text


def test_prose_loses_invisible_characters_and_stray_whitespace_but_keeps_structure():
    raw = "Title​  of the   note \r\n\r\n\r\n\r\n  - first   item\n  - second﻿ item   \n"
    assert clean_document_text(raw, ".md") == "Title of the note\n\n  - first item\n  - second item"


def test_code_keeps_its_spacing():
    raw = "def f():\n    x  =  1   \n\n\n\n    return x\n"
    assert clean_document_text(raw, ".py") == "def f():\n    x  =  1\n\n    return x"


def test_eml_upload_bodies_are_cleaned_like_gmail(tmp_path):
    from services.ingest.readers import EmailReader

    eml = tmp_path / "m.eml"
    eml.write_text(
        "Subject: Statement ready\nFrom: bank@example.com\nTo: me@example.com\n"
        "Content-Type: text/html; charset=utf-8\n\n"
        "<html><head><style>p{margin:0}</style></head><body><p>Hi Eric&#44;</p>"
        "&zwnj;͏<p>Your statement is ready.</p></body></html>\n"
    )
    text = EmailReader.read(eml)
    assert "Hi Eric," in text and "Your statement is ready." in text
    assert "margin" not in text and "&#" not in text and "͏" not in text


async def test_a_document_queued_during_a_long_read_starts_without_waiting(monkeypatch):
    from services.ingest import jobs

    started: dict[str, float] = {}
    finished: dict[str, float] = {}
    queue = [[{"id": "long"}], [], [{"id": "new"}]]
    loop = asyncio.get_running_loop()

    async def fake_claim(pool, limit):
        return queue.pop(0) if queue else []

    async def fake_process(pool, job, *, config_override=None):
        started[job["id"]] = loop.time()
        await asyncio.sleep(1.0 if job["id"] == "long" else 0.01)
        finished[job["id"]] = loop.time()

    async def fake_config_int(pool, key, default):
        return {"ingest.job_batch_size": 4}.get(key, default)

    monkeypatch.setattr(jobs, "_claim", fake_claim)
    monkeypatch.setattr(jobs, "_process_job", fake_process)
    monkeypatch.setattr(jobs, "_config_int", fake_config_int)
    monkeypatch.setattr(jobs, "_REFILL_SECONDS", 0.05)
    handled = await jobs.run_ingestion_jobs_step(None, until_empty=True)
    assert handled == 2
    assert started["new"] < finished["long"]  # it did not wait for the long read
