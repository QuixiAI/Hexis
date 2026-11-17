"""Behavioral contracts for managed runtimes: ownership, collisions and assets."""

import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from core import inference_runtime as runtime


def test_busy_port_is_not_reused():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        replacement = runtime.allocate_port(port)
        assert replacement != port


def test_instances_are_isolated_and_urls_follow_port_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name, port in [("alpha", 49123), ("beta", 49124)]:
        r = runtime.Runtime(name)
        runtime.write_json(
            r.state_path,
            dict(
                enabled=True,
                gateway_url=f"http://127.0.0.1:{port}/v1",
                local_url=f"http://127.0.0.1:{port+10}/v1",
                gateway_model="reserve",
                model_id="gemma",
            ),
        )
    assert (
        runtime.resolved_routes("alpha", container=False)[0]["endpoint"]
        == "http://127.0.0.1:49123/v1"
    )
    assert (
        runtime.resolved_routes("beta", container=True)[0]["endpoint"]
        == "http://host.docker.internal:49124/v1"
    )
    r = runtime.Runtime("alpha")
    state = runtime.read_json(r.state_path)
    state["local_url"] = "http://127.0.0.1:49200/v1"
    runtime.write_json(r.state_path, state)
    assert ":49200/" in runtime.resolved_routes("alpha", container=False)[1]["endpoint"]
    r.configure(enabled=False)
    assert runtime.resolved_routes("alpha") == []
    assert runtime.resolved_routes("beta")


def test_cannot_escape_instance_storage():
    with pytest.raises(runtime.InferenceRuntimeError):
        runtime.instance_name("../other")


def test_unowned_process_is_never_terminated(monkeypatch):
    monkeypatch.setattr(runtime, "fingerprint", lambda pid: "different process")
    monkeypatch.setattr(
        os, "kill", lambda *args: pytest.fail("killed an unowned process")
    )
    runtime.terminate({"pid": 123, "fingerprint": "previous process"})


def test_process_fingerprint_does_not_change_with_timezone(monkeypatch):
    before = runtime.fingerprint(os.getpid())
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    assert runtime.fingerprint(os.getpid()) == before
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert runtime.fingerprint(os.getpid()) == before


def test_external_service_configuration_never_adopts_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    r = runtime.Runtime("external")
    r.configure(
        local_url="http://localhost:8080/v1",
        local_model="chosen-model",
        gateway_url="http://localhost:4000/v1",
        gateway_model="my-route",
    )
    monkeypatch.setattr(
        runtime,
        "provision",
        lambda *a: pytest.fail("installed software despite explicit external URLs"),
    )
    monkeypatch.setattr(
        runtime,
        "request_json",
        lambda *a, **k: {"choices": [{"message": {"content": "READY"}}]},
    )
    result = r.ensure(start_supervisor=False)
    assert result["ready"]
    assert result["local"]["external"] and not result["local"]["owned"]
    r.stop()
    assert not runtime.resolved_routes("external")


def test_archive_path_traversal_is_rejected(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("../escape")
        info.size = 4
        handle.addfile(info, io.BytesIO(b"evil"))
    with pytest.raises(runtime.InferenceRuntimeError):
        runtime.unpack(archive, tmp_path / "output")
    assert not (tmp_path / "escape").exists()


def test_download_resumes_and_verifies_before_activation(tmp_path):
    content = b"verified release artifact"
    ranges = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            offset = int(
                self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0]
            )
            ranges.append(offset)
            self.send_response(206 if offset else 200)
            self.send_header(
                "Content-Range", f"bytes {offset}-{len(content)-1}/{len(content)}"
            )
            self.end_headers()
            self.wfile.write(content[offset:])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        dest = tmp_path / "binary"
        dest.with_suffix(".part").write_bytes(content[:7])
        asset = {
            "url": f"http://127.0.0.1:{server.server_port}/binary",
            "filename": "binary",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        assert runtime.download(asset, dest, lambda _: None).read_bytes() == content
        assert ranges == [7]
        # Reusing a verified cache is completely offline.
        server.shutdown()
        assert runtime.download(asset, dest, lambda _: None) == dest
    finally:
        server.server_close()


def test_bad_hash_is_never_activated(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        status = 200
        headers = {}

    monkeypatch.setattr(
        runtime.urllib.request, "urlopen", lambda *a, **k: Response(b"bad")
    )
    asset = {
        "url": "https://example.invalid/file",
        "filename": "file",
        "size": 3,
        "sha256": hashlib.sha256(b"good").hexdigest(),
    }
    dest = tmp_path / "file"
    with pytest.raises(runtime.InferenceRuntimeError, match="Verification failed"):
        runtime.download(asset, dest, lambda _: None)
    assert not dest.exists()


def test_manifest_pins_every_artifact_and_supported_platform():
    lock = runtime.manifest()
    assert {
        "darwin-arm64",
        "darwin-x86_64",
        "linux-arm64",
        "linux-x86_64",
        "windows-x86_64",
    } <= lock["platforms"].keys()
    for spec in lock["platforms"].values():
        for asset in [
            spec["gateway"],
            *[x for key, value in spec.items() if key != "gateway" for x in value],
        ]:
            assert len(asset["sha256"]) == 64 and asset["size"] > 0
            assert "/latest/" not in asset["url"]
    assert len(lock["model"]["sha256"]) == 64


def test_process_identity_is_timezone_independent(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        monkeypatch.setenv("TZ", "America/Los_Angeles")
        first = runtime.fingerprint(process.pid)
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        second = runtime.fingerprint(process.pid)
        assert first and first == second
        assert runtime.owned({"pid": process.pid, "fingerprint": first})
        assert not runtime.owned({"pid": process.pid, "fingerprint": "another process"})
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_login_service_does_not_replace_an_unrelated_service(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
    root = tmp_path / "Library" / "LaunchAgents"
    root.mkdir(parents=True)
    service = root / "ai.hexis.inference.example.plist"
    import plistlib

    original = plistlib.dumps({"Label": "someone.else"})
    service.write_bytes(original)
    with pytest.raises(runtime.InferenceRuntimeError, match="left untouched"):
        runtime.install_login_service(runtime.Runtime("example"))
    assert service.read_bytes() == original


def test_login_service_contains_paths_not_provider_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-copied")
    r = runtime.Runtime("example")
    runtime.install_login_service(r)
    text = (
        tmp_path / "Library" / "LaunchAgents" / "ai.hexis.inference.example.plist"
    ).read_text()
    assert "must-not-be-copied" not in text
    assert str(r.root) in text and "core.inference_runtime" in text


def test_process_ownership_is_independent_of_caller_timezone(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        monkeypatch.setenv("TZ", "America/New_York")
        first = runtime.fingerprint(process.pid)
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        assert first and runtime.fingerprint(process.pid) == first
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_reused_or_unowned_pid_is_not_signaled(monkeypatch):
    signals = []
    monkeypatch.setattr(runtime.os, "kill", lambda *args: signals.append(args))
    runtime.terminate({"pid": os.getpid(), "fingerprint": "a different process birth"})
    assert not signals


def test_external_services_are_not_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(runtime, "request_json", lambda *a, **k: {"data": []})
    signals = []
    monkeypatch.setattr(runtime.os, "kill", lambda *args: signals.append(args))
    r = runtime.Runtime("external")
    r.configure(
        local_url="http://127.0.0.1:8080/v1",
        local_model="mine",
        gateway_url="http://127.0.0.1:4000/v1",
        gateway_model="my-route",
    )
    result = r.stop(disable=True)
    assert not signals and result["local"]["external"] and result["gateway"]["external"]
