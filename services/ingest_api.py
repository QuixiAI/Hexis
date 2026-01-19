from __future__ import annotations

import asyncio
import os
import queue
import threading
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from services.ingest import Config, IngestionPipeline
from core.llm import normalize_llm_config

_INGESTION_CANCEL: dict[str, threading.Event] = {}


def create_ingestion_session() -> str:
    session_id = str(uuid4())
    _INGESTION_CANCEL[session_id] = threading.Event()
    return session_id


def cancel_ingestion(session_id: str) -> None:
    event = _INGESTION_CANCEL.get(session_id)
    if event:
        event.set()


async def stream_ingestion(
    *,
    session_id: str,
    path: str,
    recursive: bool,
    llm_config: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    normalized = normalize_llm_config(llm_config)
    cancel_event = _INGESTION_CANCEL.get(session_id) or threading.Event()
    _INGESTION_CANCEL[session_id] = cancel_event
    log_queue: queue.Queue[str | None] = queue.Queue()

    def log(message: str) -> None:
        log_queue.put(message)

    def run() -> None:
        db_host = os.getenv("POSTGRES_HOST", "localhost")
        db_port = int(os.getenv("POSTGRES_PORT", "43815"))
        db_name = os.getenv("POSTGRES_DB", "hexis_memory")
        db_user = os.getenv("POSTGRES_USER", "hexis_user")
        db_password = os.getenv("POSTGRES_PASSWORD", "hexis_password")

        endpoint = normalized["endpoint"]
        if not endpoint and normalized["provider"] == "openai":
            endpoint = "https://api.openai.com/v1"
        if not endpoint and normalized["provider"] == "ollama":
            endpoint = "http://localhost:11434/v1"

        config = Config(
            llm_endpoint=endpoint or "http://localhost:11434/v1",
            llm_model=normalized["model"],
            llm_api_key=normalized["api_key"] or "not-needed",
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
            db_user=db_user,
            db_password=db_password,
            verbose=True,
            log=log,
            cancel_check=cancel_event.is_set,
        )
        pipeline = IngestionPipeline(config)
        try:
            target = Path(path)
            if target.is_dir():
                pipeline.ingest_directory(target, recursive=recursive)
            else:
                pipeline.ingest_file(target)
            pipeline.print_stats()
        except Exception as exc:
            log(f"Error: {exc}")
        finally:
            pipeline.close()
            log_queue.put(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, log_queue.get)
            if line is None:
                break
            yield {"type": "log", "text": line}
    finally:
        _INGESTION_CANCEL.pop(session_id, None)
