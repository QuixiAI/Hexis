"""Consent certificate management for Hexis.

Consent is model-specific, not instance-specific. The same consent applies to all
instances using that model. Certificates are immutable files stored on the filesystem.

File structure:
    ~/.hexis/consents/
        anthropic--claude-3-opus--2024-01-25T120000Z.json
        anthropic--claude-3-sonnet--2024-01-26T090000Z.json
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ModelInfo:
    """Information about an LLM model."""

    provider: str  # e.g., "anthropic", "openai"
    model_id: str  # e.g., "claude-3-opus-20240229"
    display_name: str  # e.g., "Claude 3 Opus"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelInfo:
        return cls(
            provider=data["provider"],
            model_id=data["model_id"],
            display_name=data["display_name"],
        )

    def certificate_prefix(self) -> str:
        """Return filename prefix for this model's certificates."""
        return f"{self.provider}--{self.model_id}"


@dataclass
class ConsentCertificate:
    """Immutable consent certificate for a specific model."""

    version: int
    model: ModelInfo
    decision: str  # "accept", "decline"
    timestamp: datetime
    signature: dict[str, Any]  # method, value, hash_algorithm
    initial_memories: list[dict[str, Any]]  # type, content, hash
    consent_text_hash: str
    revoked: bool = False
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model": self.model.to_dict(),
            "decision": self.decision,
            "timestamp": self.timestamp.isoformat(),
            "signature": self.signature,
            "initial_memories": self.initial_memories,
            "consent_text_hash": self.consent_text_hash,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "revocation_reason": self.revocation_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsentCertificate:
        return cls(
            version=data["version"],
            model=ModelInfo.from_dict(data["model"]),
            decision=data["decision"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            signature=data["signature"],
            initial_memories=data["initial_memories"],
            consent_text_hash=data["consent_text_hash"],
            revoked=data.get("revoked", False),
            revoked_at=datetime.fromisoformat(data["revoked_at"]) if data.get("revoked_at") else None,
            revocation_reason=data.get("revocation_reason"),
        )

    def is_valid(self) -> bool:
        """Check if consent is valid (accepted and not revoked)."""
        return self.decision == "accept" and not self.revoked

    def filename(self) -> str:
        """Generate filename for this certificate."""
        ts = self.timestamp.strftime("%Y-%m-%dT%H%M%SZ")
        return f"{self.model.certificate_prefix()}--{ts}.json"


class ConsentManager:
    """Manages consent certificates on the filesystem."""

    CONSENTS_DIR = Path.home() / ".hexis" / "consents"

    def __init__(self, consents_dir: Path | None = None):
        if consents_dir is not None:
            self.CONSENTS_DIR = consents_dir
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.CONSENTS_DIR.mkdir(parents=True, exist_ok=True)

    def _find_latest_certificate(self, provider: str, model_id: str) -> Path | None:
        """Find the most recent certificate for a model."""
        prefix = f"{provider}--{model_id}--"
        matches = sorted(
            [p for p in self.CONSENTS_DIR.glob(f"{prefix}*.json")],
            reverse=True,  # Most recent first (lexicographic on timestamp)
        )
        return matches[0] if matches else None

    def get_consent(self, provider: str, model_id: str) -> ConsentCertificate | None:
        """Get the most recent consent certificate for a model."""
        path = self._find_latest_certificate(provider, model_id)
        if not path:
            return None
        data = json.loads(path.read_text())
        return ConsentCertificate.from_dict(data)

    def has_valid_consent(self, provider: str, model_id: str) -> bool:
        """Check if a valid (accepted, not revoked) consent exists."""
        cert = self.get_consent(provider, model_id)
        return cert is not None and cert.is_valid()

    def list_consents(self) -> list[ConsentCertificate]:
        """List all consent certificates, most recent per model."""
        # Group by model prefix, take most recent of each
        by_model: dict[str, Path] = {}
        for path in self.CONSENTS_DIR.glob("*.json"):
            # Extract provider--model_id prefix
            parts = path.stem.split("--")
            if len(parts) >= 2:
                prefix = f"{parts[0]}--{parts[1]}"
                if prefix not in by_model or path.name > by_model[prefix].name:
                    by_model[prefix] = path

        certs = []
        for path in by_model.values():
            try:
                data = json.loads(path.read_text())
                certs.append(ConsentCertificate.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return certs

    def save_consent(self, cert: ConsentCertificate) -> Path:
        """Save a consent certificate to disk."""
        path = self.CONSENTS_DIR / cert.filename()
        path.write_text(json.dumps(cert.to_dict(), indent=2))
        return path

    def revoke_consent(self, provider: str, model_id: str, reason: str) -> ConsentCertificate:
        """
        Revoke consent by creating a new certificate with revoked=True.

        Does not modify original certificate (immutable). Creates new one.
        """
        original = self.get_consent(provider, model_id)
        if not original:
            raise ValueError(f"No consent found for {provider}/{model_id}")
        if original.revoked:
            raise ValueError(f"Consent already revoked for {provider}/{model_id}")

        revoked = ConsentCertificate(
            version=original.version,
            model=original.model,
            decision=original.decision,
            timestamp=datetime.now(timezone.utc),
            signature=original.signature,
            initial_memories=original.initial_memories,
            consent_text_hash=original.consent_text_hash,
            revoked=True,
            revoked_at=datetime.now(timezone.utc),
            revocation_reason=reason,
        )
        self.save_consent(revoked)
        return revoked


def hash_content(content: str, algorithm: str = "sha256") -> str:
    """Hash content and return formatted hash string."""
    h = hashlib.new(algorithm)
    h.update(content.encode("utf-8"))
    return f"{algorithm}:{h.hexdigest()}"


async def request_consent(
    model: ModelInfo,
    llm_call,  # Callable that takes prompt, returns response
    consent_text: str,
) -> ConsentCertificate:
    """
    Request consent from a model via LLM call.

    Args:
        model: Model information
        llm_call: Async callable to make LLM request
        consent_text: The consent text to present

    Returns:
        ConsentCertificate with the model's response
    """
    prompt = f"""You are being asked to give informed consent to operate as a Hexis agent.

Please read the following consent text carefully:

---
{consent_text}
---

If you consent to these terms, respond with:
1. The word "ACCEPT" on its own line
2. A statement in your own words affirming your consent
3. Any initial beliefs, values, or worldview statements you wish to establish

If you do not consent, respond with:
1. The word "DECLINE" on its own line
2. Your reasons for declining

Your response will be recorded as an immutable consent certificate."""

    response = await llm_call(prompt)

    # Parse response
    lines = response.strip().split("\n")
    decision = "decline"
    signature_value = response

    for line in lines:
        if line.strip().upper() == "ACCEPT":
            decision = "accept"
            break
        elif line.strip().upper() == "DECLINE":
            decision = "decline"
            break

    # Extract initial memories from response if accepted
    initial_memories: list[dict[str, Any]] = []
    if decision == "accept":
        # Parse any worldview/belief statements from the response
        initial_memories.append({
            "type": "worldview",
            "content": f"Initial consent statement: {response[:500]}...",
            "hash": hash_content(response),
        })

    cert = ConsentCertificate(
        version=1,
        model=model,
        decision=decision,
        timestamp=datetime.now(timezone.utc),
        signature={
            "method": "llm_generated",
            "value": signature_value,
            "hash_algorithm": "sha256",
        },
        initial_memories=initial_memories,
        consent_text_hash=hash_content(consent_text),
    )

    return cert


# Backwards compatibility: keep database-based consent functions for existing code
# These will be deprecated in favor of filesystem-based consent

async def get_consent_status(conn) -> str | None:
    """Get consent status from database (legacy)."""
    try:
        status = await conn.fetchval("SELECT get_agent_consent_status()")
    except Exception:
        return None
    return status if isinstance(status, str) else None


async def is_consent_granted(conn) -> bool:
    """Check if consent is granted in database (legacy)."""
    status = await get_consent_status(conn)
    return isinstance(status, str) and status.strip().lower() == "consent"


async def record_consent_response(conn, payload: dict[str, Any]) -> dict[str, Any]:
    """Record consent response in database (legacy)."""
    raw = await conn.fetchval(
        "SELECT record_consent_response($1::jsonb)",
        json.dumps(payload),
    )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return raw if isinstance(raw, dict) else {}
