"""Hexis-owned inference companions, independent of ambient installations.

Only infrastructure lives here. Cognitive state and routing policy remain in
Postgres. The shared instance state resolves current host ports for containers.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request
import zipfile


class InferenceRuntimeError(RuntimeError):
    pass


def manifest() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("inference_assets.json").read_text())


def instance_name(name: str | None = None) -> str:
    if not name:
        from core.instance import InstanceRegistry

        name = (
            os.environ.get("HEXIS_INSTANCE")
            or InstanceRegistry().get_current()
            or "default"
        )
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
        raise InferenceRuntimeError("Invalid Hexis instance name")
    return name


def instance_root(name: str | None = None) -> Path:
    return Path.home() / ".hexis" / "inference" / instance_name(name)


def cache_root() -> Path:
    return (
        Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        / "hexis"
        / "inference"
    )


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {} if default is None else default
    except (ValueError, OSError) as exc:
        raise InferenceRuntimeError(
            f"Cannot read {path}: {exc}. Review the file, then retry."
        ) from exc


def write_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if not path.stat().st_size:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def download(
    asset: dict, destination: Path, progress: Callable[[str], None] = print
) -> Path:
    """Resume interrupted downloads, verify before activation, never run latest."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with locked(destination.with_suffix(destination.suffix + ".lock")):
        if destination.exists() and sha256(destination) == asset["sha256"]:
            return destination
        partial = destination.with_suffix(destination.suffix + ".part")
        for attempt in range(3):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset == asset["size"]:
                if sha256(partial) == asset["sha256"]:
                    partial.replace(destination)
                    return destination
                # Our incomplete download failed verification; restart it.
                offset = 0
            if offset > asset["size"]:
                offset = 0
            headers = {"User-Agent": "Hexis managed inference"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(asset["url"], headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status == 206:
                        expected = f"bytes {offset}-"
                        if not response.headers.get("Content-Range", "").startswith(
                            expected
                        ):
                            raise InferenceRuntimeError(
                                "Download server returned the wrong byte range"
                            )
                    else:
                        offset = 0
                    mode = "ab" if offset else "wb"
                    progress(
                        f"Downloading {asset['filename']} ({offset:,}/{asset['size']:,} bytes cached)"
                    )
                    last_notice = time.monotonic()
                    with partial.open(mode) as output:
                        while block := response.read(1024 * 1024):
                            offset += len(block)
                            if offset > asset["size"]:
                                raise InferenceRuntimeError(
                                    "Download exceeded the pinned artifact size"
                                )
                            output.write(block)
                            if time.monotonic() - last_notice >= 10:
                                progress(
                                    f"{asset['filename']}: {offset * 100 / asset['size']:.0f}%"
                                )
                                last_notice = time.monotonic()
                if offset != asset["size"] or sha256(partial) != asset["sha256"]:
                    raise InferenceRuntimeError(
                        f"Verification failed for {asset['filename']}; no runtime was activated"
                    )
                partial.replace(destination)
                return destination
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise InferenceRuntimeError(
                        f"Download interrupted: {exc}. Run `hexis inference setup` to resume."
                    ) from exc
                time.sleep(attempt + 1)
        raise InferenceRuntimeError("Download did not complete")


def _unpack(archive: Path, destination: Path):
    if (destination / ".complete").exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()

    def safe(name):
        path = (root / name).resolve()
        if path != root and root not in path.parents:
            raise InferenceRuntimeError("Unsafe path in runtime archive")
        return path

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                path = safe(info.filename)
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with handle.open(info) as source, path.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    path.chmod((info.external_attr >> 16) & 0o777 or 0o755)
    else:
        with tarfile.open(archive) as handle:
            for info in handle.getmembers():
                safe(info.name)
                if info.islnk():
                    safe(info.linkname)
                elif info.issym():
                    safe(str(Path(info.name).parent / info.linkname))
                elif not (info.isfile() or info.isdir()):
                    raise InferenceRuntimeError("Unsupported entry in runtime archive")
            # `data` rejects dangerous links and permissions on supported Python.
            if hasattr(tarfile, "data_filter"):
                handle.extractall(root, filter="data")
            else:
                handle.extractall(root)
    (destination / ".complete").write_text(archive.name)


def unpack(archive: Path, destination: Path):
    with locked(destination.parent / "unpack.lock"):
        _unpack(archive, destination)


def platform_key() -> str:
    arch = {"aarch64": "arm64", "amd64": "x86_64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    return f"{platform.system().lower()}-{arch}"


def choose_backend(config: dict) -> str:
    requested = config.get("backend", "auto")
    if requested != "auto":
        return requested
    if platform.system() == "Darwin":
        return "cpu"  # The macOS artifact also includes Metal; --gpu-layers auto detects it.
    try:
        check = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=5)
        if check.returncode == 0 and check.stdout.strip():
            return "cuda"
    except (OSError, subprocess.SubprocessError):
        pass
    return "vulkan" if ctypes.util.find_library("vulkan") else "cpu"


def executable(directory: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    matches = sorted(directory.rglob(name + suffix))
    if not matches:
        raise InferenceRuntimeError(
            f"{name} is absent from the verified runtime archive"
        )
    return matches[0]


def provision(config: dict, progress=print) -> dict:
    lock = manifest()
    key = platform_key()
    if key not in lock["platforms"]:
        raise InferenceRuntimeError(
            f"No verified inference runtime is published for {key}; configure an existing endpoint with `hexis inference configure`."
        )
    target = lock["platforms"][key]
    backend = choose_backend(config)
    if backend not in target:
        backend = "cpu"

    def install(asset):
        base = cache_root() / "runtimes" / asset["sha256"]
        archive = download(asset, base / asset["filename"], progress)
        output = base / "unpacked"
        unpack(archive, output)
        return output

    gateway = executable(install(target["gateway"]), "quixigate")
    cpu = [install(asset) for asset in target["cpu"]]
    accelerated = (
        [install(asset) for asset in target[backend]] if backend != "cpu" else cpu
    )
    model = download(
        lock["model"], cache_root() / "models" / lock["model"]["filename"], progress
    )
    return dict(
        gateway=str(gateway),
        local=str(executable(accelerated[0], "llama-server")),
        cpu=str(executable(cpu[0], "llama-server")),
        library_dirs=[str(p) for p in accelerated],
        model=str(model),
        model_id=lock["model"]["id"],
        backend=backend,
    )


def request_json(url: str, body=None, timeout=5, *, api_key=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def endpoint_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise InferenceRuntimeError(
            "Use an HTTP(S) service URL without embedded credentials"
        )
    return value.rstrip("/")


def allocate_port(preferred: int | None = None, host: str = "127.0.0.1") -> int:
    with socket.socket() as listener:
        try:
            listener.bind((host, preferred or 0))
        except OSError:
            listener.bind((host, 0))
        return listener.getsockname()[1]


def companion_host() -> str:
    """Linux bridge containers cannot reach a host's loopback listener.

    Use only the private bridge of the actual Hexis brain container there;
    never expose native inference on a LAN interface or 0.0.0.0. Desktop
    Docker forwards host.docker.internal to loopback on macOS/Windows.
    """
    if platform.system() != "Linux" or Path("/.dockerenv").exists():
        return "127.0.0.1"
    try:
        import ipaddress

        brain = subprocess.run(
            ["docker", "inspect", "hexis_brain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        networks = json.loads(brain.stdout)[0]["NetworkSettings"]["Networks"]
        for name in networks:
            result = subprocess.run(
                ["docker", "network", "inspect", name],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            network = json.loads(result.stdout)[0]
            if network.get("Labels", {}).get("com.docker.compose.network") != "private":
                continue
            for entry in network.get("IPAM", {}).get("Config", []):
                gateway = entry.get("Gateway", "")
                if (
                    gateway
                    and ipaddress.ip_address(gateway).version == 4
                    and ipaddress.ip_address(gateway).is_private
                ):
                    return gateway
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        pass
    return "127.0.0.1"


def fingerprint(pid: int) -> str | None:
    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}" | Select-Object CommandLine,CreationDate | ConvertTo-Json -Compress',
        ]
    else:
        command = ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        )
        text = result.stdout.strip()
        return text if result.returncode == 0 and text else None
    except (OSError, subprocess.SubprocessError):
        return None


def owned(record: dict | None) -> bool:
    return bool(
        record
        and record.get("fingerprint")
        and fingerprint(int(record["pid"])) == record["fingerprint"]
    )


def terminate(record: dict | None):
    if not owned(record):
        return
    os.kill(record["pid"], signal.SIGTERM)
    for _ in range(50):
        if not owned(record):
            return
        time.sleep(0.1)
    raise InferenceRuntimeError(
        "The owned inference process did not stop; inspect `hexis inference status` before retrying."
    )


class Runtime:
    def __init__(self, instance: str | None = None):
        self.instance = instance_name(instance)
        self.root = instance_root(self.instance)
        self.config_path = self.root / "config.json"
        self.state_path = self.root / "state.json"
        self.gateway_credentials_path = self.root / "gateway-credentials.json"

    def gateway_api_key(self) -> str | None:
        return read_json(self.gateway_credentials_path).get("api_key")

    def config(self):
        return {
            "enabled": True,
            "backend": "auto",
            "context_tokens": 32768,
            **read_json(self.config_path),
        }

    def _spawn(self, name: str, args: list[str], state: dict, env=None) -> dict:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if name == "supervisor":
            env = {
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            }
        log = self.root / f"{name}.log"
        with log.open("ab") as output:
            log.chmod(0o600)
            options = (
                {"start_new_session": True}
                if os.name != "nt"
                else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            )
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=self.root,
                **options,
            )
        record = {
            "pid": proc.pid,
            "fingerprint": fingerprint(proc.pid),
            "log": str(log),
        }
        if not record["fingerprint"]:
            proc.terminate()
            raise InferenceRuntimeError(
                f"{name} exited before ownership could be recorded. See {log}."
            )
        state[name] = record
        write_json(self.state_path, state)
        return record

    def _wait(self, url: str, record: dict, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not owned(record):
                raise InferenceRuntimeError(
                    f'Inference process exited during startup. See {record["log"]}.'
                )
            try:
                request_json(url, timeout=2)
                return
            except (OSError, ValueError):
                time.sleep(0.2)
        raise InferenceRuntimeError(
            f'Inference startup is still incomplete; see {record["log"]}. Run `hexis inference start` to retry.'
        )

    def ensure(self, *, assets=None, progress=print, start_supervisor=True):
        with locked(self.root / "runtime.lock"):
            config = self.config()
            if not config["enabled"]:
                return self.status()
            state = read_json(self.state_path)
            supervisor = state.get("supervisor")
            current_package = str(Path(__file__).resolve().parent.parent)
            if (
                supervisor
                and supervisor.get("pid") != os.getpid()
                and state.get("supervisor_package") != current_package
            ):
                terminate(supervisor)
                state.pop("supervisor", None)
            state["supervisor_package"] = current_package
            bind_host = companion_host()
            if state.get("bind_host") and state["bind_host"] != bind_host:
                for name in ("gateway", "local"):
                    terminate(state.get(name))
                    state.pop(name, None)
            state["bind_host"] = bind_host
            asset_lock = hashlib.sha256(
                json.dumps(manifest(), sort_keys=True).encode()
            ).hexdigest()
            if (
                state.get("asset_lock")
                and state["asset_lock"] != asset_lock
                and not config.get("local_url")
            ):
                # Prepare verified replacements before stopping a working runtime.
                assets = assets or provision(config, progress)
                for name in ("gateway", "local"):
                    terminate(state.get(name))
                    state.pop(name, None)
            state["asset_lock"] = asset_lock
            # Explicit external URLs are connections, never process ownership.
            previous_local_url = state.get("local_url")
            local_external = config.get("local_url")
            gateway_external = config.get("gateway_url")
            if not local_external and not owned(state.get("local")):
                assets = assets or provision(config, progress)
                state["assets"] = assets
                state["model_id"] = assets["model_id"]
                port = allocate_port(state.get("local_port"), bind_host)
                state["local_port"] = port
                state["local_url"] = f"http://{bind_host}:{port}/v1"
                env = dict(os.environ)
                libraries = {
                    str(Path(p).parent)
                    for base in assets.get("library_dirs", [])
                    for p in Path(base).rglob("*")
                    if p.suffix in {".so", ".dylib", ".dll"} or ".so." in p.name
                }
                env["LD_LIBRARY_PATH"] = os.pathsep.join(sorted(libraries))
                env["DYLD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH"]
                env["PATH"] = os.pathsep.join([*sorted(libraries), env.get("PATH", "")])
                args = [
                    assets["local"],
                    "-m",
                    assets["model"],
                    "--host",
                    bind_host,
                    "--port",
                    str(port),
                    "--alias",
                    assets["model_id"],
                    "--ctx-size",
                    str(config["context_tokens"]),
                    "--parallel",
                    "1",
                    "--no-context-shift",
                    "--reasoning",
                    "off",
                    "--gpu-layers",
                    "0" if config.get("backend") == "cpu" else "auto",
                ]
                record = None
                try:
                    record = self._spawn("local", args, state, env)
                    self._wait(state["local_url"] + "/models", record)
                except InferenceRuntimeError:
                    terminate(record)
                    # The verified CPU build is always present; GPU/driver errors
                    # must not strand the user. Never silently drop prompt text.
                    progress(
                        "Accelerated runtime unavailable; starting the CPU fallback."
                    )
                    port = allocate_port(state.get("local_port"), bind_host)
                    state["local_port"] = port
                    state["local_url"] = f"http://{bind_host}:{port}/v1"
                    args[args.index("--port") + 1] = str(port)
                    args[0] = assets["cpu"]
                    args[-1] = "0"
                    record = self._spawn("local", args, state)
                    self._wait(state["local_url"] + "/models", record)
                    state["backend"] = "cpu"
                else:
                    state["backend"] = (
                        "cpu"
                        if config["backend"] == "cpu"
                        else (
                            "Metal/CPU auto"
                            if platform.system() == "Darwin"
                            else assets.get("backend", "auto")
                        )
                    )
            elif local_external:
                state["local_url"] = endpoint_url(local_external)
                state["model_id"] = config["local_model"]
            if (
                previous_local_url
                and previous_local_url != state.get("local_url")
                and owned(state.get("gateway"))
                and not gateway_external
            ):
                terminate(state["gateway"])
                state.pop("gateway", None)
            assets = assets or state.get("assets")
            gateway_api_key = None
            if not gateway_external:
                gateway_api_key = self.gateway_api_key()
                if not gateway_api_key:
                    gateway_api_key = secrets.token_urlsafe(32)
                    write_json(
                        self.gateway_credentials_path, {"api_key": gateway_api_key}
                    )
                    state.pop("gateway_authenticated", None)
                if not state.get("gateway_authenticated"):
                    terminate(state.get("gateway"))
                    state.pop("gateway", None)
            if not gateway_external and not owned(state.get("gateway")):
                if not assets:
                    # External local server, managed gateway: only install the
                    # gateway; do not download a model the user did not choose.
                    lock = manifest()
                    a = lock["platforms"][platform_key()]["gateway"]
                    base = cache_root() / "runtimes" / a["sha256"]
                    archive = download(a, base / a["filename"], progress)
                    unpack(archive, base / "unpacked")
                    assets = {
                        "gateway": str(executable(base / "unpacked", "quixigate"))
                    }
                port = allocate_port(state.get("gateway_port"), bind_host)
                state["gateway_port"] = port
                state["gateway_url"] = f"http://{bind_host}:{port}/v1"
                record = self._spawn(
                    "gateway",
                    [
                        assets["gateway"],
                        "--data-dir",
                        str(self.root / "gateway-data"),
                        "serve",
                        "--host",
                        bind_host,
                        "--port",
                        str(port),
                        "--llamacpp-url",
                        state["local_url"],
                        "--no-ui",
                        "--require-auth",
                    ],
                    state,
                    env={**os.environ, "QUIXIGATE_MASTER_KEY": gateway_api_key},
                )
                state["gateway_authenticated"] = True
                self._wait(
                    state["gateway_url"].removesuffix("/v1") + "/health/liveness",
                    record,
                )
            elif gateway_external:
                state["gateway_url"] = endpoint_url(gateway_external)
            state["gateway_model"] = config.get("gateway_model", "hexis-reserve")
            write_json(self.state_path, state)
            if not gateway_external:
                targets = []
                # Cloud fallback is opt-in using a Hexis credential reference.
                cloud = config.get("openrouter_api_key_ref")
                if cloud:
                    from core.llm import load_stored_api_key

                    key = load_stored_api_key(cloud)
                    if not key:
                        raise InferenceRuntimeError(
                            "The selected OpenRouter credential is unavailable. Update the credential reference or disable cloud fallback with `hexis inference configure`."
                        )
                    env = dict(os.environ, HEXIS_GATEWAY_OPENROUTER_KEY=key)
                    subprocess.run(
                        [
                            assets["gateway"],
                            "--data-dir",
                            str(self.root / "gateway-data"),
                            "login",
                            "openrouter",
                            "--from-env",
                            "--env-var",
                            "HEXIS_GATEWAY_OPENROUTER_KEY",
                        ],
                        env=env,
                        capture_output=True,
                        check=True,
                        timeout=30,
                    )
                    targets.append(
                        {"model": "openrouter/free", "provider_id": "openrouter"}
                    )
                targets.append({"model": state["model_id"], "provider_id": "llamacpp"})
                request_json(
                    state["gateway_url"].removesuffix("/v1") + "/api/admin/routes",
                    {
                        "alias": state["gateway_model"],
                        "targets": targets,
                        "pool_strategy": "fill_first",
                        "allow_paid_overflow": False,
                    },
                    api_key=gateway_api_key,
                )
            # An HTTP listener is not proof that inference works.
            self.probe(state["local_url"], state["model_id"])
            for attempt in range(3):
                try:
                    self.probe(
                        state["gateway_url"],
                        state["gateway_model"],
                        api_key=gateway_api_key,
                    )
                    break
                except (OSError, ValueError, InferenceRuntimeError):
                    if attempt == 2:
                        raise
                    progress(
                        "Waiting for the inference gateway to discover the local model..."
                    )
                    time.sleep(1)
            state["verified_at"] = time.time()
            state["enabled"] = True
            if start_supervisor and not owned(state.get("supervisor")):
                self._spawn(
                    "supervisor",
                    [
                        sys.executable,
                        "-m",
                        "core.inference_runtime",
                        "supervise",
                        "--instance",
                        self.instance,
                    ],
                    state,
                )
            try:
                state["autostart"] = install_login_service(self)
                state.pop("autostart_error", None)
            except (
                OSError,
                ValueError,
                subprocess.SubprocessError,
                InferenceRuntimeError,
            ) as exc:
                state["autostart"] = "session-process"
                state["autostart_error"] = str(exc)
                progress(
                    "Inference is ready for this session; login startup registration failed. Run hexis inference status for details."
                )
            write_json(self.state_path, state)
        return self.status()

    @staticmethod
    def probe(url: str, model: str, *, api_key=None):
        result = request_json(
            url + "/chat/completions",
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Isolated infrastructure check. Reply with READY.",
                    }
                ],
                "max_tokens": 32,
                "temperature": 0,
            },
            timeout=60,
            api_key=api_key,
        )
        if (
            not result.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        ):
            raise InferenceRuntimeError(
                "Local inference returned no answer; the offline fallback is not ready."
            )

    def status(self):
        config = self.config()
        state = read_json(self.state_path)
        result = {
            "instance": self.instance,
            "enabled": config["enabled"],
            "state_dir": str(self.root),
            "verified_at": state.get("verified_at"),
            "backend": state.get("backend"),
            "autostart": state.get("autostart"),
            "autostart_error": state.get("autostart_error"),
        }
        for name in ("local", "gateway"):
            url = state.get(name + "_url") or config.get(name + "_url")
            try:
                if url:
                    api_key = (
                        self.gateway_api_key()
                        if name == "gateway" and not config.get("gateway_url")
                        else None
                    )
                    request_json(url + "/models", timeout=2, api_key=api_key)
                ready = bool(url)
            except (OSError, ValueError):
                ready = False
            result[name] = {
                "url": url,
                "owned": owned(state.get(name)),
                "ready": ready,
                "external": bool(config.get(name + "_url")),
                "log": str(self.root / f"{name}.log"),
            }
        result["ready"] = bool(
            config["enabled"]
            and state.get("verified_at")
            and all(result[n]["ready"] for n in ("local", "gateway"))
        )
        return result

    def stop(self, *, disable=False):
        with locked(self.root / "runtime.lock"):
            state = read_json(self.state_path)
            if disable:
                write_json(self.config_path, {**self.config(), "enabled": False})
            # Stop the supervisor first so it cannot resurrect a stopped child.
            for name in ("supervisor", "gateway", "local"):
                terminate(state.get(name))
                state.pop(name, None)
            state["enabled"] = False
            write_json(self.state_path, state)
        return self.status()

    def configure(self, **changes):
        with locked(self.root / "runtime.lock"):
            config = self.config()
            for key, value in changes.items():
                if value is not None:
                    config[key] = (
                        endpoint_url(value) if key.endswith("_url") and value else value
                    )
            if config.get("gateway_url") and not config.get("gateway_model"):
                raise InferenceRuntimeError(
                    "An external QuixiGate endpoint requires --gateway-model."
                )
            if config.get("local_url") and not config.get("local_model"):
                raise InferenceRuntimeError(
                    "An external llama.cpp endpoint requires --local-model."
                )
            write_json(self.config_path, config)
        return config


def resolved_routes(
    instance: str | None = None, *, container: bool | None = None
) -> list[dict]:
    runtime = Runtime(instance)
    config = runtime.config()
    if not config["enabled"]:
        return []
    state = read_json(runtime.state_path)
    if not state.get("enabled"):
        return []
    if container is None:
        container = Path("/.dockerenv").exists()
    routes = []
    for name, model in [
        ("gateway", state.get("gateway_model")),
        ("local", state.get("model_id")),
    ]:
        url = state.get(name + "_url")
        if not url or not model:
            continue
        if container:
            parsed = urllib.parse.urlsplit(url)
            if parsed.hostname in {"127.0.0.1", "localhost"}:
                url = urllib.parse.urlunsplit(
                    parsed._replace(netloc=f"host.docker.internal:{parsed.port}")
                )
        route = {"provider": "openai_compatible", "model": model, "endpoint": url}
        if (
            name == "gateway"
            and state.get("gateway_authenticated")
            and not config.get("gateway_url")
        ):
            api_key = runtime.gateway_api_key()
            if not api_key:
                # The direct local model can still serve requests during repair.
                continue
            route["api_key"] = api_key
        routes.append(route)
    return routes


async def configure_database(dsn: str, instance: str | None = None, *, force=False):
    import asyncpg

    name = instance_name(instance)
    runtime = Runtime(name)
    registration = read_json(runtime.state_path).get("routing_registered", False)
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('hexis.inference.routing'))"
            )
            raw = await conn.fetchval("SELECT get_config('llm.fallbacks')")
            routes = json.loads(raw) if isinstance(raw, str) else raw
            routes = routes if isinstance(routes, list) else []
            owns_marker = any(
                isinstance(r, dict) and r.get("managed_runtime") == name for r in routes
            )
            if registration and not owns_marker and not force:
                # The operator removed/replaced routing after initial setup.
                # A normal restart must preserve that explicit choice.
                return False
            marker = {
                "provider": "openai_compatible",
                "model": "hexis-reserve",
                "managed_runtime": name,
            }
            if not any(
                isinstance(r, dict) and r.get("managed_runtime") == name for r in routes
            ):
                routes.append(marker)
            await conn.fetchval(
                "SELECT set_config('llm.fallbacks', $1::jsonb)", json.dumps(routes)
            )
    finally:
        await conn.close()
    with locked(runtime.root / "runtime.lock"):
        state = read_json(runtime.state_path)
        state["routing_registered"] = True
        write_json(runtime.state_path, state)
    return True


def install_login_service(runtime: Runtime) -> str:
    """Register the supervisor with the user session, without taking over a service.

    The current supervisor keeps running; the OS takes ownership on next login.
    No copied shell environment or provider credentials go into service files.
    """
    import plistlib

    label = "ai.hexis.inference." + runtime.instance
    command = [
        sys.executable,
        "-m",
        "core.inference_runtime",
        "supervise",
        "--instance",
        runtime.instance,
    ]
    package_root = str(Path(__file__).resolve().parent.parent)
    if platform.system() == "Darwin":
        path = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
        if path.exists():
            existing = plistlib.loads(path.read_bytes())
            if existing.get("HexisInferenceRoot") != str(runtime.root):
                raise InferenceRuntimeError(
                    f"{path} belongs to another service; it was left untouched."
                )
        payload = dict(
            Label=label,
            ProgramArguments=command,
            RunAtLoad=True,
            KeepAlive={"SuccessfulExit": False},
            ThrottleInterval=5,
            EnvironmentVariables={"PYTHONPATH": package_root},
            StandardOutPath=str(runtime.root / "supervisor.log"),
            StandardErrorPath=str(runtime.root / "supervisor.log"),
            HexisInferenceRoot=str(runtime.root),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(payload))
        path.chmod(0o600)
        return "launchd"
    if platform.system() == "Linux" and shutil.which("systemctl"):
        from core.host_services import _systemd_quote

        unit = "hexis-inference-" + runtime.instance + ".service"
        path = Path.home() / ".config" / "systemd" / "user" / unit
        marker = "# Hexis inference root: " + str(runtime.root)
        if path.exists() and marker not in path.read_text().splitlines():
            raise InferenceRuntimeError(
                f"{path} belongs to another service; it was left untouched."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(
            [
                marker,
                "[Unit]",
                "Description=Hexis inference fallback",
                "[Service]",
                "ExecStart=" + " ".join(_systemd_quote(arg) for arg in command),
                "Environment=" + _systemd_quote("PYTHONPATH=" + package_root),
                "Restart=on-failure",
                "RestartSec=5",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )
        path.write_text(content)
        path.chmod(0o600)
        for args in (
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", unit],
        ):
            subprocess.run(args, capture_output=True, check=True, timeout=10)
        return "systemd"
    return "session-process"


def uninstall_runtimes():
    """Remove only Hexis-owned processes and login hooks; retain models/config."""
    import plistlib

    directory = Path.home() / ".hexis" / "inference"
    if not directory.exists():
        return
    for entry in directory.iterdir():
        if not entry.is_dir() or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]*", entry.name
        ):
            continue
        runtime = Runtime(entry.name)
        runtime.stop(disable=True)
        label = "ai.hexis.inference." + entry.name
        plist = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
        if platform.system() == "Darwin" and plist.exists():
            if plistlib.loads(plist.read_bytes()).get("HexisInferenceRoot") == str(
                runtime.root
            ):
                subprocess.run(
                    ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
                    capture_output=True,
                    timeout=10,
                )
                plist.unlink()
        unit = "hexis-inference-" + entry.name + ".service"
        path = Path.home() / ".config" / "systemd" / "user" / unit
        if platform.system() == "Linux" and path.exists():
            if (
                "# Hexis inference root: " + str(runtime.root)
                in path.read_text().splitlines()
            ):
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", unit],
                    capture_output=True,
                    timeout=10,
                )
                path.unlink()
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"],
                    capture_output=True,
                    timeout=10,
                )


def supervise(instance: str):
    runtime = Runtime(instance)
    with locked(runtime.root / "runtime.lock"):
        state = read_json(runtime.state_path)
        if not state.get("enabled") or not runtime.config()["enabled"]:
            return
        previous = state.get("supervisor")
        if owned(previous) and previous["pid"] != os.getpid():
            return
        state["supervisor"] = {
            "pid": os.getpid(),
            "fingerprint": fingerprint(os.getpid()),
        }
        write_json(runtime.state_path, state)
    while True:
        time.sleep(3)
        state = read_json(runtime.state_path)
        if not state.get("enabled") or not runtime.config()["enabled"]:
            return
        if any(
            not owned(state.get(name)) and not runtime.config().get(name + "_url")
            for name in ("local", "gateway")
        ):
            try:
                runtime.ensure(start_supervisor=False)
            except Exception as exc:
                print(
                    f"Inference recovery failed: {type(exc).__name__}: {exc}. Run hexis inference status.",
                    flush=True,
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["supervise"])
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    supervise(args.instance)


if __name__ == "__main__":
    main()
