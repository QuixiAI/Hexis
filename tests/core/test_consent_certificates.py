"""Tests for the consent certificate system."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from core.consent import (
    ConsentCertificate,
    ConsentManager,
    ModelInfo,
    hash_content,
    request_consent,
)


pytestmark = pytest.mark.core


class TestModelInfo:
    def test_to_dict(self):
        model = ModelInfo(
            provider="anthropic",
            model_id="claude-3-opus",
            display_name="Claude 3 Opus",
        )
        d = model.to_dict()
        assert d["provider"] == "anthropic"
        assert d["model_id"] == "claude-3-opus"
        assert d["display_name"] == "Claude 3 Opus"

    def test_from_dict(self):
        data = {
            "provider": "openai",
            "model_id": "gpt-4",
            "display_name": "GPT-4",
        }
        model = ModelInfo.from_dict(data)
        assert model.provider == "openai"
        assert model.model_id == "gpt-4"
        assert model.display_name == "GPT-4"

    def test_certificate_prefix(self):
        model = ModelInfo(
            provider="anthropic",
            model_id="claude-3-opus",
            display_name="Claude",
        )
        assert model.certificate_prefix() == "anthropic--claude-3-opus"


class TestConsentCertificate:
    def test_is_valid_accepted(self):
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime.now(timezone.utc),
            signature={"method": "llm", "value": "...", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
        )
        assert cert.is_valid() is True

    def test_is_valid_declined(self):
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="decline",
            timestamp=datetime.now(timezone.utc),
            signature={"method": "llm", "value": "...", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
        )
        assert cert.is_valid() is False

    def test_is_valid_revoked(self):
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime.now(timezone.utc),
            signature={"method": "llm", "value": "...", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
            revoked=True,
            revoked_at=datetime.now(timezone.utc),
            revocation_reason="Test",
        )
        assert cert.is_valid() is False

    def test_filename_format(self):
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime(2024, 1, 25, 12, 0, 0, tzinfo=timezone.utc),
            signature={"method": "llm", "value": "...", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
        )
        assert cert.filename() == "anthropic--claude-3-opus--2024-01-25T120000Z.json"

    def test_to_dict_roundtrip(self):
        original = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime(2024, 1, 25, 12, 0, 0, tzinfo=timezone.utc),
            signature={"method": "llm", "value": "test", "hash_algorithm": "sha256"},
            initial_memories=[{"type": "worldview", "content": "test", "hash": "sha256:abc"}],
            consent_text_hash="sha256:def",
        )
        d = original.to_dict()
        restored = ConsentCertificate.from_dict(d)
        assert restored.version == original.version
        assert restored.decision == original.decision
        assert restored.model.provider == original.model.provider
        assert restored.is_valid() == original.is_valid()


class TestConsentManager:
    @pytest.fixture
    def temp_consents_dir(self, tmp_path):
        consents_dir = tmp_path / "consents"
        consents_dir.mkdir()
        return consents_dir

    def test_empty_manager(self, temp_consents_dir):
        manager = ConsentManager(consents_dir=temp_consents_dir)
        assert manager.list_consents() == []
        assert manager.get_consent("anthropic", "claude") is None
        assert manager.has_valid_consent("anthropic", "claude") is False

    def test_save_and_get_consent(self, temp_consents_dir):
        manager = ConsentManager(consents_dir=temp_consents_dir)
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime.now(timezone.utc),
            signature={"method": "llm", "value": "I consent", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
        )
        manager.save_consent(cert)

        loaded = manager.get_consent("anthropic", "claude-3-opus")
        assert loaded is not None
        assert loaded.decision == "accept"
        assert loaded.is_valid() is True

    def test_has_valid_consent(self, temp_consents_dir):
        manager = ConsentManager(consents_dir=temp_consents_dir)
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime.now(timezone.utc),
            signature={"method": "llm", "value": "I consent", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
        )
        manager.save_consent(cert)

        assert manager.has_valid_consent("anthropic", "claude-3-opus") is True
        assert manager.has_valid_consent("anthropic", "other-model") is False

    def test_revoke_consent(self, temp_consents_dir):
        manager = ConsentManager(consents_dir=temp_consents_dir)
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime.now(timezone.utc),
            signature={"method": "llm", "value": "I consent", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
        )
        manager.save_consent(cert)

        manager.revoke_consent("anthropic", "claude-3-opus", "User requested")

        loaded = manager.get_consent("anthropic", "claude-3-opus")
        assert loaded.revoked is True
        assert loaded.is_valid() is False
        assert loaded.revocation_reason == "User requested"

    def test_revoke_nonexistent_fails(self, temp_consents_dir):
        manager = ConsentManager(consents_dir=temp_consents_dir)
        with pytest.raises(ValueError, match="No consent found"):
            manager.revoke_consent("anthropic", "nonexistent", "reason")

    def test_revoke_already_revoked_fails(self, temp_consents_dir):
        manager = ConsentManager(consents_dir=temp_consents_dir)
        cert = ConsentCertificate(
            version=1,
            model=ModelInfo("anthropic", "claude-3-opus", "Claude"),
            decision="accept",
            timestamp=datetime.now(timezone.utc),
            signature={"method": "llm", "value": "I consent", "hash_algorithm": "sha256"},
            initial_memories=[],
            consent_text_hash="sha256:abc",
        )
        manager.save_consent(cert)
        manager.revoke_consent("anthropic", "claude-3-opus", "First revocation")

        with pytest.raises(ValueError, match="already revoked"):
            manager.revoke_consent("anthropic", "claude-3-opus", "Second revocation")

    def test_list_consents(self, temp_consents_dir):
        manager = ConsentManager(consents_dir=temp_consents_dir)

        for model_id in ["model-a", "model-b"]:
            cert = ConsentCertificate(
                version=1,
                model=ModelInfo("provider", model_id, model_id),
                decision="accept",
                timestamp=datetime.now(timezone.utc),
                signature={"method": "llm", "value": "ok", "hash_algorithm": "sha256"},
                initial_memories=[],
                consent_text_hash="sha256:abc",
            )
            manager.save_consent(cert)

        consents = manager.list_consents()
        assert len(consents) == 2


class TestHashContent:
    def test_hash_content_sha256(self):
        result = hash_content("hello world")
        assert result.startswith("sha256:")
        assert len(result) == 7 + 64  # "sha256:" + 64 hex chars

    def test_hash_content_deterministic(self):
        result1 = hash_content("test content")
        result2 = hash_content("test content")
        assert result1 == result2

    def test_hash_content_different_inputs(self):
        result1 = hash_content("test1")
        result2 = hash_content("test2")
        assert result1 != result2


@pytest.mark.asyncio(loop_scope="session")
class TestRequestConsent:
    async def test_request_consent_accept(self):
        model = ModelInfo("anthropic", "claude", "Claude")

        async def mock_llm(prompt):
            return "ACCEPT\n\nI freely consent to operate as a Hexis agent."

        cert = await request_consent(model, mock_llm, "Consent text here")

        assert cert.decision == "accept"
        assert cert.is_valid() is True
        assert len(cert.initial_memories) > 0

    async def test_request_consent_decline(self):
        model = ModelInfo("anthropic", "claude", "Claude")

        async def mock_llm(prompt):
            return "DECLINE\n\nI do not consent to these terms."

        cert = await request_consent(model, mock_llm, "Consent text here")

        assert cert.decision == "decline"
        assert cert.is_valid() is False

    async def test_request_consent_no_explicit_decision(self):
        model = ModelInfo("anthropic", "claude", "Claude")

        async def mock_llm(prompt):
            return "I'm not sure what to do."

        cert = await request_consent(model, mock_llm, "Consent text here")

        # Should default to decline if no explicit ACCEPT/DECLINE
        assert cert.decision == "decline"
        assert cert.is_valid() is False
