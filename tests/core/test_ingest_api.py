import pytest

from services import ingest_api

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.core]


async def test_create_and_cancel_ingestion_session():
    session_id = ingest_api.create_ingestion_session()
    assert session_id in ingest_api._INGESTION_CANCEL  # noqa: SLF001
    ingest_api.cancel_ingestion(session_id)
    assert ingest_api._INGESTION_CANCEL[session_id].is_set()  # noqa: SLF001
    ingest_api._INGESTION_CANCEL.pop(session_id, None)  # noqa: SLF001


async def test_stream_ingestion_emits_logs(monkeypatch, tmp_path):
    class StubPipeline:
        def __init__(self, config):
            self.config = config

        def ingest_file(self, target):
            self.config.log(f"ingest_file:{target}")

        def ingest_directory(self, target, recursive=False):
            self.config.log(f"ingest_dir:{target}:{recursive}")

        def print_stats(self):
            self.config.log("stats")

        def close(self):
            return None

    monkeypatch.setattr(ingest_api, "IngestionPipeline", StubPipeline)

    test_file = tmp_path / "note.txt"
    test_file.write_text("hello", encoding="utf-8")

    session_id = ingest_api.create_ingestion_session()
    logs = []
    async for event in ingest_api.stream_ingestion(
        session_id=session_id,
        path=str(test_file),
        recursive=False,
        llm_config={"provider": "openai", "model": "gpt-4o"},
    ):
        logs.append(event["text"])

    assert any("ingest_file" in line for line in logs)
    assert any("stats" in line for line in logs)
    assert session_id not in ingest_api._INGESTION_CANCEL  # noqa: SLF001
