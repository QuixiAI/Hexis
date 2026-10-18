"""One LLM library for every provider: LiteLLM.

Every chat, heartbeat, subconscious and ingestion call goes through
``litellm.acompletion``. The hexis-specific sign-ins (ChatGPT, Copilot, Chutes,
Qwen Portal, MiniMax Portal, Anthropic setup tokens) stay owned by
``core.auth``; this module only maps each provider onto LiteLLM's model prefix
and connection arguments.

The two Google Code Assist providers (``google-gemini-cli``,
``google-antigravity``) speak a protocol LiteLLM does not know (the
``v1internal`` Cloud Code Assist API), so they keep their own client in
``core.providers.google_code_assist`` behind the same ``chat_completion`` door.
"""

from __future__ import annotations

import json
import logging
import os
import re
from types import SimpleNamespace
from typing import Any, AsyncIterator

from core.auth.store import AUTH_DIR

# Every provider is reached through LiteLLM's SDK. Left at its defaults,
# importing LiteLLM loads any .env in the working directory into the process
# environment ("DEV" mode) and fetches its model price list from GitHub; the
# first could pull in unrelated secrets or settings, and a locked-down network
# must not depend on the second.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
# LiteLLM's chatgpt/ provider (Codex) reads its tokens from this folder; the
# sign-in in core.auth.openai_codex keeps them there, in the shared auth store.
os.environ.setdefault("CHATGPT_TOKEN_DIR", str(AUTH_DIR / "litellm-chatgpt"))
# The chatgpt/ provider otherwise puts the Codex CLI prompt ("You are Codex…
# a coding agent", ~3,000 words) in front of every request, which drowns the
# agent's character. ChatGPT's backend accepts other instructions; ours defer
# to the prompt each caller sends (chat, heartbeat, subconscious, …).
os.environ.setdefault(
    "CHATGPT_DEFAULT_INSTRUCTIONS",
    "You are the mind of a persistent AI built on Hexis. "
    "Follow the instructions that come next.",
)
try:
    import litellm
except Exception:  # pragma: no cover
    litellm = None  # type: ignore[assignment]
else:
    litellm.suppress_debug_info = True

logger = logging.getLogger(__name__)


def _keep_parallel_tool_arguments() -> None:
    """Don't lose the arguments of parallel tool calls on the ChatGPT stream.

    LiteLLM turns the Responses API's ``output_item.done`` for a function call
    into an empty chunk, assuming the arguments already arrived as
    ``function_call_arguments.delta`` events. ChatGPT's backend sends those
    for a single call, but when the model calls several tools at once it sends
    the arguments only in ``output_item.done``, so every call came through as
    ``{}`` and failed for "missing required arguments" (seen with litellm
    1.104.3). When a call finishes with no argument deltas, emit its complete
    arguments from the finished item.
    """
    try:
        from litellm.completion_extras.litellm_responses_transformation.transformation import (
            OpenAiResponsesToChatCompletionStreamIterator as Iterator,
        )
        from litellm.types.llms.openai import ChatCompletionToolCallFunctionChunk
        from litellm.types.utils import ChatCompletionToolCallChunk, Delta, ModelResponseStream, StreamingChoices
    except Exception:  # pragma: no cover - a LiteLLM layout change
        logger.warning("Could not patch LiteLLM's ChatGPT stream; parallel tool calls may lose arguments")
        return
    original = Iterator.chunk_parser
    if getattr(original, "_hexis_patched", False):
        return

    def chunk_parser(self, chunk):  # type: ignore[no-untyped-def]
        seen = self.__dict__.setdefault("_hexis_argument_deltas", set())
        event = chunk.get("type") if isinstance(chunk, dict) else None
        event = getattr(event, "value", event)
        output_index = chunk.get("output_index", 0) if isinstance(chunk, dict) else 0
        if event == "response.function_call_arguments.delta" and chunk.get("delta"):
            seen.add(output_index)
        elif event == "response.output_item.done":
            item = chunk.get("item") or {}
            arguments = item.get("arguments")
            if item.get("type") == "function_call" and arguments and output_index not in seen:
                index = Iterator._sequential_tool_call_index(self._tool_call_index_map, output_index)
                return self._with_stream_scoped_id(ModelResponseStream(choices=[StreamingChoices(
                    index=0,
                    delta=Delta(tool_calls=[ChatCompletionToolCallChunk(
                        id=None, index=index, type="function",
                        function=ChatCompletionToolCallFunctionChunk(name=None, arguments=arguments),
                    )]),
                    finish_reason=None,
                )]))
        return original(self, chunk)

    chunk_parser._hexis_patched = True  # type: ignore[attr-defined]
    Iterator.chunk_parser = chunk_parser


def _no_interactive_chatgpt_login() -> None:
    """Never let LiteLLM's ChatGPT provider wait for a device-code login.

    Hexis owns the ChatGPT sign-in (core.auth.openai_codex) and refreshes it
    before each call. When LiteLLM can't read its token file at that moment
    (the auth folder is shared with the containers, and a read can miss the
    file while it is replaced), it would print a login code to the worker's
    log and wait up to 15 minutes for someone to enter it. Instead, read the
    file again; if there is still no token, fail the call so the caller
    retries, and say where to sign in.
    """
    try:
        from litellm.llms.chatgpt.authenticator import Authenticator
    except Exception:  # pragma: no cover - a LiteLLM layout change
        logger.warning("Could not patch LiteLLM's ChatGPT sign-in; it may wait for a device-code login")
        return
    if getattr(Authenticator._login_device_code, "_hexis_patched", False):
        return

    def _login_device_code(self: Any) -> dict[str, str]:
        import time

        for _ in range(5):
            time.sleep(0.2)
            data = self._read_auth_file() or {}
            token = data.get("access_token")
            if isinstance(token, str) and token and not self._is_token_expired(data, token):
                return {"access_token": token}
        raise RuntimeError(
            "The ChatGPT sign-in is missing or expired. Sign in again with "
            "`hexis auth openai-codex login` or from the dashboard's setup page."
        )

    _login_device_code._hexis_patched = True  # type: ignore[attr-defined]
    Authenticator._login_device_code = _login_device_code
    # A device code requested earlier makes LiteLLM wait out a cooldown first.
    Authenticator._get_device_code_cooldown_remaining = lambda self, auth_data: 0.0


if litellm is not None:
    _keep_parallel_tool_arguments()
    _no_interactive_chatgpt_login()


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

# Providers that speak the OpenAI Chat Completions protocol (directly or
# through a gateway).
OPENAI_COMPATIBLE = {
    "openai",
    "openai_compatible",
    "openai-chat-completions-endpoint",
    "grok",
    "chutes",
    "github-copilot",
    "qwen-portal",
    "azure_openai",
    "openrouter",
    "zai",
    "deepseek",
    "qwen",
    "qwen_coding",
    "meta",
    "minimax",
}

# Providers that speak the Anthropic Messages protocol.
ANTHROPIC_COMPATIBLE = {"anthropic", "minimax-portal"}

# Google Cloud Code Assist (a Gemini CLI / Antigravity sign-in). Not a protocol
# LiteLLM speaks; see core.providers.google_code_assist.
CODE_ASSIST_PROVIDERS = {"google-gemini-cli", "google-antigravity"}

# Codex uses the ChatGPT subscription OAuth backend.
_CODEX_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
# Bedrock and Vertex AI serve several model families; LiteLLM picks the
# request format from the model id.
SUPPORTED_PROVIDERS = (
    OPENAI_COMPATIBLE
    | ANTHROPIC_COMPATIBLE
    | CODE_ASSIST_PROVIDERS
    | {"gemini", "openai-codex", "xai-oauth", "bedrock", "vertex"}
)
DEFAULT_BEDROCK_REGION = "us-east-1"
DEFAULT_VERTEX_LOCATION = "us-central1"
_MINIMAX_DEFAULT_ENDPOINT = "https://api.minimax.io/anthropic"


def _unsupported_provider(provider: str) -> ValueError:
    return ValueError(
        f"Unsupported LLM provider: {provider!r}. Supported providers: "
        f"{', '.join(sorted(SUPPORTED_PROVIDERS))}. Run `hexis init` to choose one."
    )


def parse_vertex_endpoint(endpoint: str | None) -> tuple[str, str]:
    """``projects/<project>/locations/<location>`` -> (project, location)."""
    parts = (endpoint or "").strip().strip("/").split("/")
    project = parts[1] if len(parts) >= 2 and parts[0] == "projects" else ""
    location = parts[3] if len(parts) >= 4 and parts[2] == "locations" else ""
    return project, location or DEFAULT_VERTEX_LOCATION


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

_LLM_MAX_RETRIES = 4
_LLM_RETRY_BACKOFF_BASE = 2  # seconds
_LLM_RETRY_MAX_WAIT = 60.0  # seconds; cap for Retry-After honoring


def is_transient_error(exc: BaseException) -> bool:
    """True for failures that go away on their own: rate limits, server
    errors, timeouts, dropped connections. Same rule for every provider."""
    exc_str = str(exc).lower()
    status = getattr(exc, 'status_code', None) or getattr(exc, 'status', None)
    return (
        status in (429, 500, 502, 503, 504, 529)
        or 'rate' in exc_str
        or 'overloaded' in exc_str
        or 'timeout' in exc_str
        or 'timed out' in exc_str
        or 'connection' in exc_str
        or isinstance(exc, (ConnectionError, TimeoutError, OSError))
    )


async def _retry_on_transient(
    coro_factory,
    *,
    max_retries: int = _LLM_MAX_RETRIES,
    should_retry: Any | None = None,
) -> Any:
    """Retry an LLM call on transient errors (rate limits, server errors, network).

    ``should_retry`` is an optional zero-arg predicate consulted before each
    retry — streaming callers use it to refuse replay once tokens have
    already reached the consumer.
    """
    import asyncio as _asyncio
    import random as _random

    last_exc = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            is_transient = is_transient_error(exc)
            if should_retry is not None and not should_retry():
                is_transient = False
            if is_transient and attempt < max_retries - 1:
                wait = _LLM_RETRY_BACKOFF_BASE ** attempt + _random.uniform(0.0, 1.0)
                retry_after = getattr(exc, 'retry_after', None)
                if retry_after:
                    wait = max(wait, float(retry_after))
                wait = min(wait, _LLM_RETRY_MAX_WAIT)
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries, wait, exc,
                )
                await _asyncio.sleep(wait)
                continue
            raise
    raise last_exc  # Should not reach here, but just in case


# ---------------------------------------------------------------------------
# Per-endpoint memory of what a model rejects
# ---------------------------------------------------------------------------

def _endpoint_cache_key(endpoint: str | None) -> str:
    return (endpoint or "default").rstrip("/")


_endpoint_schema_support: dict[str, bool] = {}


def _schema_from_response_format(response_format: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    """(name, JSON schema) when response_format asks for json_schema, else None."""
    if not response_format or response_format.get("type") != "json_schema":
        return None
    spec = response_format.get("json_schema") or {}
    schema = spec.get("schema") if isinstance(spec, dict) else None
    if not isinstance(schema, dict):
        return None
    return str(spec.get("name") or "result"), schema


def _is_request_rejected(exc: Exception) -> bool:
    """True for a 4xx validation rejection of the request (400 or 422).

    A server or gateway that cannot honour a structured-output request words
    the rejection its own way: "response_format is not supported" (Ollama,
    LM Studio, older vLLM), or, behind LiteLLM, the forced tool_choice the
    gateway translated it into ("tool_choice ... not supported" from Bedrock).
    So a request carrying response_format that is rejected is re-asked once
    without it; the constraint is taken to be the cause only if that retry
    succeeds (see _remember_schema_unsupported).
    """
    return litellm is not None and isinstance(exc, (litellm.BadRequestError, litellm.UnprocessableEntityError))


def _schema_key(endpoint: str | None, model: str) -> str:
    # Per model: behind one gateway some models take a schema and some do not.
    return f"{_endpoint_cache_key(endpoint)}|{model}"


def _schema_support_known_missing(endpoint: str | None, model: str) -> bool:
    return _endpoint_schema_support.get(_schema_key(endpoint, model)) is False


def _remember_schema_unsupported(endpoint: str | None, model: str, exc: Exception) -> None:
    logger.warning(
        "LLM %s at %s rejected a request with response_format and accepted it without; "
        "continuing without a schema for this model: %s", model, endpoint, exc,
    )
    _endpoint_schema_support[_schema_key(endpoint, model)] = False


# Reasoning effort: how long a thinking model may think before answering.
# None leaves the model's default. Short structured calls (the inline
# appraisal) ask for "low": a thinking model left at its default can spend
# its whole output budget thinking and return nothing. OpenAI reasoning
# models, vLLM and SGLang take the standard parameter; a model or server
# that rejects it (non-reasoning OpenAI models) is remembered per endpoint
# and model, and the call goes ahead without it.
_reasoning_rejected: set[str] = set()


def _reasoning_key(endpoint: str | None, model: str) -> str:
    return f"{_endpoint_cache_key(endpoint)}|{model}"


def _usable_reasoning_effort(endpoint: str | None, model: str, effort: str | None) -> str | None:
    if not effort or _reasoning_key(endpoint, model) in _reasoning_rejected:
        return None
    return effort


def _is_reasoning_unsupported_error(exc: Exception) -> bool:
    """True when a server rejected the reasoning-effort parameter itself."""
    if litellm is None or not isinstance(exc, (litellm.BadRequestError, litellm.UnprocessableEntityError)):
        return False
    return "reasoning" in str(exc).lower()


def _finish_reason(value: Any) -> str:
    """Collapse each provider's stop reason to: stop, length, tool_calls or other."""
    text = str(getattr(value, "value", value) or "").lower()
    if text in ("length", "max_tokens", "max_output_tokens") or "max_token" in text:
        return "length"
    if text in ("tool_calls", "tool_use", "function_call"):
        return "tool_calls"
    if text in ("stop", "end_turn", "stop_sequence", "completed", "finish_reason_stop"):
        return "stop"
    return text or "stop"


# ---------------------------------------------------------------------------
# Leaked reasoning
# ---------------------------------------------------------------------------

_REASONING_PATTERNS = [
    # DeepSeek/Qwen-style reasoning tags, if they surface inline in `content`.
    re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE),
    # Gemma 4 native reasoning channel — <|channel>thought ... <channel|>.
    # A well-behaved Gemma 4 emits these special tokens; llama.cpp does not
    # parse them, so they leak into `content`.
    re.compile(r"<\|?channel\|?>\s*thought\b.*?<\|?channel\|?>", re.DOTALL | re.IGNORECASE),
    # Markdown-fenced reasoning block — ```thought ... ``` — the form actually
    # observed in production: an abliterated Gemma 4 merge degrades special-token
    # adherence and approximates its thought channel as a plaintext fence.
    re.compile(r"```(?:thought|thinking|reasoning)\b.*?```", re.DOTALL | re.IGNORECASE),
]


def strip_reasoning(content: str) -> str:
    """
    Remove leaked chain-of-thought from model output.

    Some models — abliterated community merges in particular — emit their
    reasoning trace as visible content instead of through a separate reasoning
    channel: either ``<think>...</think>`` tags or a ```thought fenced block.
    Hexis never wants that text in a user-facing reply.

    Model-agnostic and a no-op when the content is already clean, so it is safe
    to apply unconditionally regardless of which model/provider is configured.
    """
    if not content:
        return content
    lowered = content.lower()
    if "```" not in content and "<think>" not in lowered and "channel" not in lowered:
        return content
    cleaned = content
    for pat in _REASONING_PATTERNS:
        cleaned = pat.sub("", cleaned)
    cleaned = cleaned.strip()
    if cleaned == content:
        return content
    if not cleaned:
        logger.warning(
            "strip_reasoning: model output was entirely reasoning trace, "
            "nothing left after strip (%d chars removed)", len(content),
        )
        return ""
    logger.info(
        "strip_reasoning: removed %d chars of leaked reasoning", len(content) - len(cleaned),
    )
    return cleaned


# ---------------------------------------------------------------------------
# Config normalisation
# ---------------------------------------------------------------------------

_PROVIDER_ALIASES: dict[str, str] = {
    "openai_chat_completions_endpoint": "openai-chat-completions-endpoint",
    "openai_codex": "openai-codex",
    "github_copilot": "github-copilot",
    "qwen_portal": "qwen-portal",
    "minimax_portal": "minimax-portal",
    "google_gemini_cli": "google-gemini-cli",
    "google_antigravity": "google-antigravity",
    "xai_oauth": "xai-oauth",
}


def normalize_provider(provider: str | None) -> str:
    if not provider:
        return "openai"
    raw = provider.strip().lower()
    return _PROVIDER_ALIASES.get(raw, raw)


def normalize_endpoint(provider: str, endpoint: str | None) -> str | None:
    # Subscription tokens must never be sent to a custom/API-key endpoint.
    if provider == "openai-codex":
        return _CODEX_DEFAULT_BASE_URL
    if provider == "xai-oauth":
        return "https://api.x.ai/v1"
    if provider == "azure_openai" and endpoint and endpoint.strip():
        # The resource URL people copy from the Azure portal; the v1 API under it
        # speaks the plain OpenAI protocol, with the deployment name as the model.
        base = endpoint.strip().rstrip("/")
        return base if "/openai/" in base + "/" else f"{base}/openai/v1"
    if endpoint:
        return endpoint.strip() or None
    _DEFAULTS: dict[str, str] = {
        "grok": "https://api.x.ai/v1",
        "chutes": "https://api.chutes.ai/v1",
        "qwen-portal": "https://portal.qwen.ai/v1",
        "minimax-portal": _MINIMAX_DEFAULT_ENDPOINT,
        "google-gemini-cli": "https://cloudcode-pa.googleapis.com",
        "google-antigravity": "https://cloudcode-pa.googleapis.com",
        "openrouter": "https://openrouter.ai/api/v1",
        "bedrock": DEFAULT_BEDROCK_REGION,
        "zai": "https://api.z.ai/api/paas/v4",
        "deepseek": "https://api.deepseek.com",
        "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen_coding": "https://coding-intl.dashscope.aliyuncs.com/v1",
        "meta": "https://api.meta.ai/v1",
        "minimax": "https://api.minimax.io/v1",
    }
    return _DEFAULTS.get(provider)


def resolve_api_key(api_key_env: str | None) -> str | None:
    if not api_key_env:
        return None
    value = api_key_env.strip()
    if not value:
        return None
    return os.getenv(value)


def load_stored_api_key(ref: str | None) -> str | None:
    """The API key saved under *ref* in the auth store, if any.

    A key typed during setup lives in the auth store (``~/.hexis/auth``),
    which every service mounts, so no file edit or restart is needed.
    """
    if not ref:
        return None
    try:
        from core.auth.store import load_auth

        stored = load_auth(ref)
    except Exception:
        return None
    if isinstance(stored, dict):
        value = stored.get("api_key")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# Which environment variable each API-key provider reads when the config
# names no key. Deliberately absent: openai_compatible — a local server must
# never silently consume an ambient OPENAI_API_KEY (Bar #5).
_PROVIDER_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "grok": "XAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai-chat-completions-endpoint": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "zai": "ZAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "qwen_coding": "QWEN_API_KEY",
    "meta": "META_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


def normalize_llm_config(config: dict[str, Any] | None, *, default_model: str = "gpt-4o") -> dict[str, Any]:
    """Normalize a raw LLM config dict (provider aliases, env-var API keys, endpoint defaults).

    .. warning::
        This function does **not** run provider-specific credential loaders
        (OAuth token refresh for Codex, Copilot, Gemini CLI, etc.).  Entry
        points that need fully-resolved credentials should use
        :func:`core.llm_config.resolve_llm_config` or
        :func:`core.llm_config.load_llm_config` instead.
    """
    config = config or {}
    provider = normalize_provider(str(config.get("provider") or "openai"))
    model = str(config.get("model") or default_model)
    endpoint = normalize_endpoint(provider, str(config.get("endpoint") or "").strip() or None)
    api_key = config.get("api_key")
    if not api_key:
        api_key = resolve_api_key(str(config.get("api_key_env") or "").strip() or None)
    if not api_key:
        api_key = load_stored_api_key(str(config.get("api_key_ref") or "").strip() or None)
    if not api_key:
        env_name = _PROVIDER_ENV_KEYS.get(provider)
        if env_name:
            api_key = os.getenv(env_name)
    result: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "api_key": api_key,
    }
    # Preserve auth_mode when set by provider-specific config loaders.
    auth_mode = config.get("auth_mode")
    if auth_mode:
        result["auth_mode"] = auth_mode
    return result


# ---------------------------------------------------------------------------
# Message shaping
# ---------------------------------------------------------------------------

def _extract_system_prompt(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    parts, rest = _extract_system_parts(messages)
    return "\n\n".join(parts), rest


def _extract_system_parts(messages: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """System messages kept as separate parts, in order.

    Callers assemble the prompt so that everything stable comes first and
    everything per-turn comes last (see services.agent.SystemPrompt). Providers
    that can cache a prefix need that boundary preserved rather than flattened,
    so the parts stay split until the last moment.
    """
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            text = _content_text(msg.get("content"))
            if text.strip():
                system_parts.append(text)
        else:
            rest.append(msg)
    return system_parts, rest


def _anthropic_system_blocks(parts: list[str]) -> list[dict[str, Any]] | None:
    """Anthropic `system` as content blocks, with a cache breakpoint.

    Everything except the final part is the stable prefix, so the breakpoint
    goes on the last stable block: Anthropic caches up to and including the
    marked block and re-reads only what follows. With a single part there is
    nothing to cache against, so it is sent plain.
    """
    if not parts:
        return None
    if len(parts) == 1:
        return [{"type": "text", "text": parts[0]}]
    blocks: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        block: dict[str, Any] = {"type": "text", "text": part}
        if index == len(parts) - 2:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    return blocks


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"text", "input_text", "output_text"}:
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content or "")


def _image_url_from_part(part: dict[str, Any]) -> str | None:
    raw = part.get("image_url")
    if isinstance(raw, dict):
        url = raw.get("url")
        return str(url) if url else None
    if isinstance(raw, str):
        return raw
    url = part.get("url")
    return str(url) if url else None


def _image_detail_from_part(part: dict[str, Any]) -> str:
    raw = part.get("detail")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raw_image_url = part.get("image_url")
    if isinstance(raw_image_url, dict):
        raw = raw_image_url.get("detail")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "auto"


def _data_url_parts(url: str) -> tuple[str | None, str | None]:
    if not url.startswith("data:") or "," not in url:
        return None, None
    header, data = url.split(",", 1)
    media_type = header[5:].split(";", 1)[0] or None
    return media_type, data


def _content_to_openai_chat(content: Any) -> Any:
    if not isinstance(content, list):
        return _content_text(content)
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype in {"text", "input_text", "output_text"}:
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append({"type": "text", "text": text})
            continue
        if ptype in {"image_url", "input_image"}:
            url = _image_url_from_part(part)
            if url:
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url,
                        "detail": _image_detail_from_part(part),
                    },
                })
    return parts or _content_text(content)


def _openai_tool_calls(raw_calls: list[Any]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for call in raw_calls or []:
        fn = getattr(call, "function", None) or {}
        name = getattr(fn, "name", None) or fn.get("name")
        raw_args = getattr(fn, "arguments", None) or fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except Exception:
            logger.debug("Failed to parse tool arguments: %r", str(raw_args)[:200])
            args = {}
        tool_calls.append({"id": getattr(call, "id", None), "name": name, "arguments": args})
    return tool_calls


# ---------------------------------------------------------------------------
# Anthropic subscription (setup-token) specifics
# ---------------------------------------------------------------------------

# Must be verbatim: Anthropic's subscription (OAuth) endpoint validates the
# Claude-Code identity preamble. hermes-agent and openclaw both send this exact
# string; a different wording can 401.
_CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
# LiteLLM recognises the sk-ant-oat token itself (Bearer auth plus the OAuth
# beta); these are the remaining Claude Code request headers the
# subscription endpoint expects.
_CLAUDE_CODE_HEADERS = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "user-agent": "claude-cli/2.1.2 (external, cli)",
    "x-app": "cli",
}


def _oauth_tool_name(name: str) -> str:
    """Normalize a tool name for the OAuth/subscription path.

    Anthropic's subscription billing classifier flags single-underscore
    ``mcp_`` tool names as third-party apps and 400s the request; Claude Code
    uses a double underscore (``mcp__``). Hexis emits ``mcp_<server>_<tool>``
    (see core/tools/mcp.py), so rewrite those. Matches hermes-agent.
    """
    if name.startswith("mcp_") and not name.startswith("mcp__"):
        return "mcp__" + name[len("mcp_"):]
    return name


def _oauth_tools(tools: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]] | None, dict[str, str]]:
    """Tools as sent on the subscription path, and wire name -> original name."""
    if not tools:
        return tools, {}
    renamed: list[dict[str, Any]] = []
    restore: dict[str, str] = {}
    for tool in tools:
        fn = tool.get("function") or {}
        original = str(fn.get("name") or "")
        wire = _oauth_tool_name(original)
        if wire != original:
            restore[wire] = original
            tool = {**tool, "function": {**fn, "name": wire}}
        renamed.append(tool)
    return renamed, restore


def _restore_tool_names(tool_calls: list[dict[str, Any]], restore: dict[str, str]) -> list[dict[str, Any]]:
    if not restore:
        return tool_calls
    return [{**call, "name": restore.get(call.get("name") or "", call.get("name"))} for call in tool_calls]


def _uses_anthropic_messages(provider: str, model: str) -> bool:
    """True when LiteLLM will send this request as Anthropic Messages, where a
    `cache_control` breakpoint on the system blocks is honoured."""
    if provider in ANTHROPIC_COMPATIBLE:
        return True
    return provider in {"bedrock", "vertex"} and "claude" in model.lower()


def _litellm_messages(
    messages: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    auth_mode: str | None,
) -> list[dict[str, Any]]:
    """Messages in LiteLLM's (OpenAI chat) shape.

    For Anthropic-protocol targets the system parts become one system message
    of content blocks with the cache breakpoint on the last stable block (see
    _anthropic_system_blocks); every other provider receives the system
    messages as they are — OpenAI and Gemini cache a stable prefix on their
    own, and LiteLLM folds several system messages into the provider's single
    instruction field, in order.
    """
    converted = [{**msg, "content": _content_to_openai_chat(msg.get("content"))} for msg in messages]
    if not _uses_anthropic_messages(provider, model):
        return converted
    parts, rest = _extract_system_parts(converted)
    # Only reshape the wire format when the caller actually split the prompt.
    # A single system message has no cacheable boundary to exploit, and the
    # subscription path is validated by Anthropic (it checks the identity
    # preamble), so it keeps the exact shape it has always had.
    split = len(parts) > 1
    if provider == "anthropic" and auth_mode == "setup-token":
        # The identity preamble is the most stable text there is; it leads.
        parts = [_CLAUDE_CODE_IDENTITY, *parts]
    if not parts:
        return rest
    content: Any = _anthropic_system_blocks(parts) if split else "\n\n".join(parts)
    return [{"role": "system", "content": content}, *rest]


# ---------------------------------------------------------------------------
# LiteLLM routing
# ---------------------------------------------------------------------------

# LiteLLM's prefix for each provider that maps onto one directly.
_LITELLM_PREFIX = {
    "openai": "openai",
    "openai_compatible": "openai",
    "openai-chat-completions-endpoint": "openai",
    # Subscription sign-ins that expose an OpenAI-compatible endpoint; the
    # OAuth access token is the API key (core.llm_config injects a fresh one).
    "chutes": "openai",
    "qwen-portal": "openai",
    "github-copilot": "openai",
    "grok": "xai",
    "openrouter": "openrouter",
    "anthropic": "anthropic",
    # MiniMax's Anthropic-compatible endpoint, with a MiniMax Portal sign-in.
    "minimax-portal": "anthropic",
    "gemini": "gemini",
    "azure_openai": "azure",
    "bedrock": "bedrock",
    "vertex": "vertex_ai",
    "zai": "zai",
    "deepseek": "deepseek",
    # Alibaba Model Studio (DashScope); the Coding Plan is the same protocol
    # on its own subscription endpoint.
    "qwen": "dashscope",
    "qwen_coding": "dashscope",
    # Meta's Muse models are served only through the Responses API.
    "meta": "openai/responses",
    # MiniMax's OpenAI-compatible API with an API key (the Portal sign-in
    # above uses its Anthropic-compatible endpoint instead).
    "minimax": "minimax",
    # A SuperGrok / X sign-in: the OAuth access token is the API key.
    "xai-oauth": "xai",
    # A ChatGPT sign-in. The Codex backend speaks only the Responses API, and
    # LiteLLM sends a model it does not list to Chat Completions unless told.
    "openai-codex": "chatgpt/responses",
}


def _litellm_target(
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    *,
    auth_mode: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """LiteLLM's model string and connection arguments for a configured provider."""
    if litellm is None:
        raise RuntimeError("The litellm package is required (pip install litellm).")
    prefix = _LITELLM_PREFIX.get(provider)
    if prefix is None:
        raise _unsupported_provider(provider)
    kwargs: dict[str, Any] = {}
    if provider == "openai-codex":
        # LiteLLM reads the token and account from its own file, which
        # _subscription_key has just refreshed; nothing is passed here.
        return f"{prefix}/{model}", {}
    if provider == "bedrock":
        # A Bedrock API key if given; otherwise the AWS credential chain
        # (AWS_* environment variables, profile, or the instance role).
        kwargs["aws_region_name"] = (endpoint or "").strip() or DEFAULT_BEDROCK_REGION
        if api_key:
            kwargs["api_key"] = api_key
    elif provider == "vertex":
        project, location = parse_vertex_endpoint(endpoint)
        if not project:
            raise RuntimeError("Vertex AI needs a Google Cloud project ID.")
        kwargs["vertex_project"] = project
        kwargs["vertex_location"] = location
        if api_key:
            # A pasted service-account key; without one, Application Default Credentials.
            kwargs["vertex_credentials"] = api_key
    elif provider == "azure_openai":
        # Azure's v1 API under the resource URL, with the deployment as the model.
        base = (endpoint or "").strip().rstrip("/")
        if base.endswith("/openai/v1"):
            base = base[: -len("/openai/v1")]
        kwargs.update(api_base=base or None, api_version="v1", api_key=api_key)
    elif provider == "github-copilot":
        from core.auth.github_copilot import COPILOT_REQUEST_HEADERS

        kwargs.update(api_base=endpoint, api_key=api_key, extra_headers=dict(COPILOT_REQUEST_HEADERS))
    elif provider == "anthropic" and auth_mode == "setup-token":
        # A Claude Code setup token (sk-ant-oat…): LiteLLM sends it as Bearer
        # auth with the OAuth beta; the rest of the Claude Code identity goes
        # in these headers and the system preamble (_litellm_messages).
        kwargs.update(api_base=endpoint, api_key=api_key, extra_headers=dict(_CLAUDE_CODE_HEADERS))
    else:
        if endpoint:
            kwargs["api_base"] = endpoint
        # Local OpenAI-compatible servers (vLLM, SGLang, llama.cpp) often need
        # no key, but the client refuses to send a request without one.
        kwargs["api_key"] = api_key or ("not-needed" if endpoint and prefix == "openai" else None)
    return f"{prefix}/{model}", {k: v for k, v in kwargs.items() if v is not None}


async def _subscription_key(provider: str, api_key: str | None) -> str | None:
    """Refresh a subscription sign-in under its cross-process lock before a call."""
    if provider == "openai-codex":
        from core.auth.openai_codex import ensure_fresh_openai_codex_credentials
        await ensure_fresh_openai_codex_credentials()
        return None
    if provider == "xai-oauth":
        from core.auth.xai_oauth import ensure_fresh_xai_credentials
        return (await ensure_fresh_xai_credentials()).access
    return api_key


async def _emit(callback: Any | None, text: str) -> None:
    if callback and text:
        import asyncio

        result = callback(text)
        if asyncio.iscoroutine(result):
            await result


def _code_assist_credentials(api_key: str | None) -> tuple[str, str]:
    """(access token, project id) packed by core.llm_config for Code Assist."""
    data: dict[str, Any] = {}
    if api_key:
        try:
            parsed = json.loads(api_key)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            pass
    return str(data.get("token") or api_key or ""), str(data.get("projectId") or "")


def _build_request(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
    auth_mode: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """The litellm.acompletion keyword arguments, and the tool-name restore map."""
    target, connection = _litellm_target(provider, model, endpoint, api_key, auth_mode=auth_mode)
    restore: dict[str, str] = {}
    if provider == "anthropic" and auth_mode == "setup-token":
        tools, restore = _oauth_tools(tools)
    request: dict[str, Any] = {
        "model": target,
        "messages": _litellm_messages(messages, provider=provider, model=model, auth_mode=auth_mode),
        "temperature": temperature,
        "max_tokens": max_tokens,
        # A parameter this model does not take (temperature on a reasoning
        # model, say) is left out rather than failing the call.
        "drop_params": True,
        **connection,
    }
    if tools:
        request["tools"] = tools
        request["tool_choice"] = "auto"
    return request, restore


async def chat_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    response_format: dict[str, Any] | None = None,
    auth_mode: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """One model turn. Returns ``{content, tool_calls, raw, finish_reason}``."""
    provider = normalize_provider(provider)
    endpoint = normalize_endpoint(provider, endpoint)

    if provider in CODE_ASSIST_PROVIDERS:
        from core.providers.google_code_assist import google_code_assist_completion

        access_token, project_id = _code_assist_credentials(api_key)
        result = await _retry_on_transient(lambda: google_code_assist_completion(
            endpoint=endpoint or "https://cloudcode-pa.googleapis.com",
            access_token=access_token,
            project_id=project_id,
            model=model,
            messages=messages,
            tools=tools,
            is_antigravity=(provider == "google-antigravity"),
        ))
        result["content"] = strip_reasoning(result.get("content") or "")
        result.setdefault("finish_reason", "tool_calls" if result.get("tool_calls") else "stop")
        return result

    reasoning_effort = _usable_reasoning_effort(endpoint, model, reasoning_effort)
    api_key = await _subscription_key(provider, api_key)
    request, restore = _build_request(
        provider=provider, model=model, endpoint=endpoint, api_key=api_key, messages=messages,
        tools=tools, temperature=temperature, max_tokens=max_tokens, auth_mode=auth_mode,
    )
    if response_format and _schema_support_known_missing(endpoint, model):
        response_format = None
    if response_format:
        request["response_format"] = response_format
    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort

    async def _do_completion():
        response = await litellm.acompletion(**request)
        choice = response.choices[0]
        message = choice.message
        return {
            "content": strip_reasoning(getattr(message, "content", None) or ""),
            "tool_calls": _restore_tool_names(_openai_tool_calls(getattr(message, "tool_calls", None) or []), restore),
            "raw": response,
            "finish_reason": _finish_reason(getattr(choice, "finish_reason", None)),
        }

    schema_rejection: Exception | None = None
    for _attempt in range(3):
        try:
            result = await _retry_on_transient(_do_completion)
        except Exception as exc:
            if request.get("reasoning_effort") and _is_reasoning_unsupported_error(exc):
                logger.warning("LLM %s at %s rejected reasoning effort; continuing without it: %s", model, endpoint, exc)
                _reasoning_rejected.add(_reasoning_key(endpoint, model))
                request.pop("reasoning_effort", None)
                continue
            if request.get("response_format") and _is_request_rejected(exc):
                schema_rejection = exc
                request.pop("response_format", None)
                continue
            raise
        if schema_rejection is not None:
            _remember_schema_unsupported(endpoint, model, schema_rejection)
        return result
    raise RuntimeError(f"LLM endpoint {endpoint} rejected the request")


async def stream_chat_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    on_text_delta: Any | None = None,
    on_reasoning_delta: Any | None = None,
    auth_mode: str | None = None,
) -> dict[str, Any]:
    """
    Streaming chat completion that supports tools.

    Accumulates the full response while optionally calling ``on_text_delta(text)``
    for each answer token and ``on_reasoning_delta(text)`` for provider-native
    reasoning. Reasoning never enters the returned content. Returns the same
    shape as ``chat_completion()``: ``{content, tool_calls, raw, finish_reason}``.

    ``on_text_delta`` can be a sync or async callable accepting a single str
    argument. It's called for each text token as it arrives.
    """
    provider = normalize_provider(provider)
    endpoint = normalize_endpoint(provider, endpoint)
    delivered = False

    async def _deliver_text(text: str) -> None:
        nonlocal delivered
        if text:
            delivered = True
        await _emit(on_text_delta, text)

    async def _deliver_reasoning(text: str) -> None:
        nonlocal delivered
        if text:
            delivered = True
        await _emit(on_reasoning_delta, text)

    # Once text has reached the caller, a retry would repeat it.
    def _can_retry() -> bool:
        return not delivered

    if provider in CODE_ASSIST_PROVIDERS:
        from core.providers.google_code_assist import stream_google_code_assist_completion

        access_token, project_id = _code_assist_credentials(api_key)
        result = await _retry_on_transient(
            lambda: stream_google_code_assist_completion(
                endpoint=endpoint or "https://cloudcode-pa.googleapis.com",
                access_token=access_token,
                project_id=project_id,
                model=model,
                messages=messages,
                tools=tools,
                is_antigravity=(provider == "google-antigravity"),
                on_text_delta=_deliver_text if on_text_delta is not None else None,
            ),
            should_retry=_can_retry,
        )
        result["content"] = strip_reasoning(result.get("content") or "")
        result.setdefault("finish_reason", "tool_calls" if result.get("tool_calls") else "stop")
        return result

    api_key = await _subscription_key(provider, api_key)
    request, restore = _build_request(
        provider=provider, model=model, endpoint=endpoint, api_key=api_key, messages=messages,
        tools=tools, temperature=temperature, max_tokens=max_tokens, auth_mode=auth_mode,
    )
    request["stream"] = True
    # Ask for the token counts on the final chunk, so a streamed turn is
    # metered like a plain one. Dropped for providers that do not take it.
    request["stream_options"] = {"include_usage": True}

    async def _do_stream():
        response = await litellm.acompletion(**request)
        content_parts: list[str] = []
        finish: Any = None
        usage: Any = None
        # Tool calls arrive in pieces: index -> {id, name, arguments_parts}
        tc_accum: dict[int, dict[str, Any]] = {}
        async for chunk in response:
            usage = getattr(chunk, "usage", None) or usage
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            finish = getattr(choice, "finish_reason", None) or finish
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if isinstance(reasoning, str) and reasoning:
                await _deliver_reasoning(reasoning)
            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                await _deliver_text(delta.content)
            for tc_delta in getattr(delta, "tool_calls", None) or []:
                idx = getattr(tc_delta, "index", None) or 0
                entry = tc_accum.setdefault(idx, {"id": None, "name": None, "arguments_parts": []})
                if getattr(tc_delta, "id", None):
                    entry["id"] = tc_delta.id
                fn = getattr(tc_delta, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        entry["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        entry["arguments_parts"].append(fn.arguments)
        tool_calls: list[dict[str, Any]] = []
        for idx in sorted(tc_accum):
            entry = tc_accum[idx]
            raw_args = "".join(entry["arguments_parts"])
            try:
                args = json.loads(raw_args) if raw_args else {}
            except Exception:
                logger.debug("Failed to parse tool arguments: %r", raw_args[:200])
                args = {}
            tool_calls.append({"id": entry["id"], "name": entry["name"], "arguments": args})
        return {
            "content": strip_reasoning("".join(content_parts)),
            "tool_calls": _restore_tool_names(tool_calls, restore),
            # Token counts, when the provider reported them, in the shape
            # core.usage.extract_usage reads.
            "raw": SimpleNamespace(usage=usage) if usage is not None else None,
            "finish_reason": _finish_reason(finish or ("tool_calls" if tool_calls else "stop")),
        }

    return await _retry_on_transient(_do_stream, should_retry=_can_retry)


async def stream_text_completion(
    *,
    provider: str,
    model: str,
    endpoint: str | None,
    api_key: str | None,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 1400,
    auth_mode: str | None = None,
) -> AsyncIterator[str]:
    import asyncio

    # One streaming path for every provider: stream_chat_completion feeds a
    # queue, and this generator hands the text on as it arrives.
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def produce():
        try:
            await stream_chat_completion(
                provider=provider, model=model, endpoint=endpoint, api_key=api_key,
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                on_text_delta=queue.put, auth_mode=auth_mode,
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        while (chunk := await queue.get()) is not None:
            yield chunk
        await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
