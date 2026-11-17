"""CLI command tests must not install native runtimes or download model weights."""

import pytest


@pytest.fixture(autouse=True)
def isolate_managed_inference_startup(monkeypatch):
    from apps import hexis_cli

    monkeypatch.setattr(hexis_cli, "_start_inference_runtime", lambda *a, **k: True)

    from core import inference_runtime

    monkeypatch.setattr(inference_runtime, "uninstall_runtimes", lambda: None)
    monkeypatch.setattr(
        inference_runtime.Runtime, "stop", lambda self, **kw: self.status()
    )
