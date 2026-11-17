"""
Hexis Tools System - Image Generation

Allows the agent to generate images using OpenAI's DALL-E 3 API
(or compatible providers).  Returns the image URL or base64 data
for delivery via channels.
"""

from __future__ import annotations

import logging
import os
from typing import Any, TYPE_CHECKING

from .base import (
    ToolCategory,
    ToolErrorType,
    ToolExecutionContext,
    ToolHandler,
    ToolResult,
    ToolSpec,
)

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_VALID_SIZES = {"1024x1024", "1792x1024", "1024x1792", "512x512", "256x256"}
_VALID_STYLES = {"vivid", "natural"}
_VALID_QUALITIES = {"standard", "hd"}


class GenerateImageHandler(ToolHandler):
    """Generate images using DALL-E 3 or a compatible API."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="generate_image",
            description=(
                "Generate an image from a text description using DALL-E 3. "
                "Returns a URL to the generated image. "
                "Use detailed, descriptive prompts for best results."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Detailed description of the image to generate. "
                            "Be specific about style, composition, colors, and mood."
                        ),
                    },
                    "size": {
                        "type": "string",
                        "description": "Image size. Options: 1024x1024 (square), 1792x1024 (landscape), 1024x1792 (portrait).",
                        "default": "1024x1024",
                        "enum": ["1024x1024", "1792x1024", "1024x1792"],
                    },
                    "style": {
                        "type": "string",
                        "description": "Image style. 'vivid' for hyper-real/dramatic, 'natural' for more natural look.",
                        "default": "vivid",
                        "enum": ["vivid", "natural"],
                    },
                    "quality": {
                        "type": "string",
                        "description": "Image quality. 'hd' for finer details, 'standard' for faster generation.",
                        "default": "standard",
                        "enum": ["standard", "hd"],
                    },
                },
                "required": ["prompt"],
            },
            category=ToolCategory.EXTERNAL,
            energy_cost=3,
            is_read_only=True,
        )

    async def execute(
        self, arguments: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        prompt = arguments.get("prompt", "").strip()
        if not prompt:
            return ToolResult.error_result("Prompt is required.")

        size = arguments.get("size", "1024x1024")
        if size not in _VALID_SIZES:
            size = "1024x1024"
        style = arguments.get("style", "vivid")
        if style not in _VALID_STYLES:
            style = "vivid"
        quality = arguments.get("quality", "standard")
        if quality not in _VALID_QUALITIES:
            quality = "standard"

        # Resolve API key from config or environment
        pool: asyncpg.Pool = context.registry.pool
        api_key = await self._resolve_api_key(pool)
        if not api_key:
            return ToolResult.error_result(
                "OpenAI API key not configured. Set OPENAI_API_KEY or configure llm.api_key_env.",
                ToolErrorType.MISSING_API_KEY,
            )

        try:
            result = await self._generate(
                api_key=api_key,
                prompt=prompt,
                size=size,
                style=style,
                quality=quality,
            )

            # Record usage
            from core.usage import record_usage
            import asyncio
            asyncio.ensure_future(record_usage(
                provider="openai",
                model="dall-e-3",
                operation="image",
                source="chat",
                session_key=context.session_id,
                metadata={"size": size, "quality": quality, "style": style},
                cost_usd=self._estimate_cost(size, quality),
                pool=pool,
            ))

            return ToolResult.success_result(
                {
                    "url": result["url"],
                    "revised_prompt": result.get("revised_prompt"),
                    "size": size,
                    "style": style,
                    "quality": quality,
                },
                display_output=f"Generated image ({size}, {quality}): {result['url'][:80]}...",
            )
        except Exception as exc:
            logger.error("Image generation failed: %s", exc)
            return ToolResult.error_result(f"Image generation failed: {exc}")

    async def _resolve_api_key(self, pool: Any) -> str | None:
        """Resolve the OpenAI API key from config or environment."""
        # Try config table first
        try:
            env_name = await pool.fetchval(
                "SELECT value #>> '{}' FROM config WHERE key = 'llm.api_key_env'",
            )
            if env_name:
                key = os.getenv(env_name)
                if key:
                    return key
        except Exception:
            pass

        # Fall back to direct env var
        return os.getenv("OPENAI_API_KEY")

    async def _generate(
        self,
        *,
        api_key: str,
        prompt: str,
        size: str,
        style: str,
        quality: str,
    ) -> dict[str, Any]:
        """Call the OpenAI Images API through LiteLLM."""
        from core.llm import litellm

        if litellm is None:
            raise RuntimeError("The litellm package is required for image generation (pip install litellm).")
        response = await litellm.aimage_generation(
            model="openai/dall-e-3",
            prompt=prompt,
            size=size,
            style=style,
            quality=quality,
            n=1,
            api_key=api_key,
        )
        image = response.data[0]
        return {
            "url": image.url,
            "revised_prompt": image.revised_prompt,
        }

    def _estimate_cost(self, size: str, quality: str) -> float:
        """Estimate cost in USD for a DALL-E 3 image."""
        # DALL-E 3 pricing (Feb 2026)
        if quality == "hd":
            if size == "1024x1024":
                return 0.080
            return 0.120  # 1792x1024 or 1024x1792
        else:
            if size == "1024x1024":
                return 0.040
            return 0.080
        return 0.040


def create_image_gen_tools() -> list[ToolHandler]:
    """Create image generation tool handlers."""
    return [GenerateImageHandler()]
