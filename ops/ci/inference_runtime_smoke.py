"""Run from an installed wheel: no database, ambient tools, credentials or models."""

from pathlib import Path
import tempfile
from core.inference_runtime import Runtime, cache_root

# A clean ownership root exercises the packaged lifecycle. CI may cache only
# verified artifacts; no user's gateway/model installation is ever discovered.
with tempfile.TemporaryDirectory(prefix="hexis-inference-smoke-") as directory:
    original_home = Path.home
    Path.home = classmethod(lambda cls: Path(directory))
    runtime = Runtime("release-smoke")
    runtime.configure(backend="cpu", context_tokens=4096)
    try:
        result = runtime.ensure(start_supervisor=False)
        assert (
            result["ready"] and result["local"]["owned"] and result["gateway"]["owned"]
        )
        assert result["local"]["url"] != result["gateway"]["url"]
        print("Installed-wheel CPU inference and gateway inference verified.")
    finally:
        runtime.stop(disable=True)
        Path.home = original_home
