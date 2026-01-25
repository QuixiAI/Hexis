#!/usr/bin/env python3
"""
Hexis Universal Ingestion Pipeline

Implements the ingestion flow described in ToDo/ingest.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import UUID

try:
    import requests
except ImportError:
    print("Installing requests...")
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "--break-system-packages", "-q"])
    import requests

from core.cognitive_memory_api import (
    CognitiveMemorySync,
    MemoryInput as ApiMemoryInput,
    MemoryType as ApiMemoryType,
    RelationshipType,
)

# =========================================================================
# CONFIGURATION
# =========================================================================


class IngestionMode(str, Enum):
    AUTO = "auto"
    DEEP = "deep"
    STANDARD = "standard"
    SHALLOW = "shallow"
    ARCHIVE = "archive"


@dataclass
class Config:
    """Pipeline configuration."""

    # LLM Settings
    llm_endpoint: str = "http://localhost:11434/v1"
    llm_model: str = "llama3.2"
    llm_api_key: str = "not-needed"

    # Database Settings
    db_host: str = "localhost"
    db_port: int = 43815
    db_name: str = "hexis_memory"
    db_user: str = "postgres"
    db_password: str = "password"

    # Mode
    mode: IngestionMode = IngestionMode.AUTO

    # Mode thresholds (word counts)
    deep_max_words: int = 2000
    standard_max_words: int = 20000

    # Chunking
    max_section_chars: int = 2000
    chunk_overlap: int = 200

    # Extraction
    max_facts_per_section: int = 20
    min_confidence_threshold: float = 0.6
    skip_sections: list[str] = field(
        default_factory=lambda: ["references", "bibliography", "acknowledgments", "appendix"]
    )

    # Persistence overrides
    min_importance_floor: float | None = None
    permanent: bool = False

    # Source trust override
    base_trust: float | None = None

    # Processing
    verbose: bool = True
    log: Optional[Callable[[str], None]] = None
    cancel_check: Optional[Callable[[], bool]] = None


# =========================================================================
# DATA STRUCTURES
# =========================================================================


@dataclass
class Section:
    title: str
    content: str
    index: int


@dataclass
class DocumentInfo:
    title: str
    source_type: str
    content_hash: str
    word_count: int
    path: str
    file_type: str


@dataclass
class Appraisal:
    valence: float = 0.0
    arousal: float = 0.3
    primary_emotion: str = "neutral"
    intensity: float = 0.0
    goal_relevance: list[dict[str, Any]] = field(default_factory=list)
    worldview_tension: float = 0.0
    curiosity: float = 0.0
    summary: str = ""

    def to_state_payload(self, source: str = "ingest") -> dict[str, Any]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "primary_emotion": self.primary_emotion,
            "intensity": self.intensity,
            "source": source,
        }


@dataclass
class Extraction:
    content: str
    category: str
    confidence: float
    importance: float
    why: str | None = None
    connections: list[str] = field(default_factory=list)
    supports: str | None = None
    contradicts: str | None = None
    concepts: list[str] = field(default_factory=list)


@dataclass
class IngestionMetrics:
    """Metrics collected during ingestion for observability."""

    source_type: str = ""
    source_size_bytes: int = 0
    word_count: int = 0
    mode: str = ""
    appraisal_valence: float = 0.0
    appraisal_arousal: float = 0.0
    appraisal_emotion: str = "neutral"
    appraisal_intensity: float = 0.0
    extraction_count: int = 0
    dedup_count: int = 0
    memory_count: int = 0
    llm_calls: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=lambda: 0.0)


# =========================================================================
# HELPERS
# =========================================================================


def _emit(config: Config, message: str) -> None:
    if config.log:
        config.log(message)
    else:
        print(message)


def _should_cancel(config: Config) -> bool:
    if config.cancel_check:
        try:
            return bool(config.cancel_check())
        except Exception:
            return False
    return False


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _normalize_mode(mode: IngestionMode | str | None) -> IngestionMode:
    if isinstance(mode, IngestionMode):
        return mode
    raw = str(mode or "auto").strip().lower()
    for item in IngestionMode:
        if raw == item.value:
            return item
    return IngestionMode.AUTO


def _select_mode(config: Config, words: int) -> IngestionMode:
    if config.mode != IngestionMode.AUTO:
        return config.mode
    if words <= config.deep_max_words:
        return IngestionMode.DEEP
    if words <= config.standard_max_words:
        return IngestionMode.STANDARD
    return IngestionMode.ARCHIVE


def _decay_rate_for_intensity(intensity: float, base: float = 0.01) -> float:
    if intensity < 0.1:
        return base * 3.0
    if intensity < 0.3:
        return base * 1.5
    if intensity > 0.6:
        return base * 0.5
    return base


def _infer_source_type(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in {".pdf", ".md", ".markdown", ".txt", ".text", ".rtf", ".docx"}:
        return "document"
    if suffix in CodeReader.LANGUAGE_MAP:
        return "code"
    if suffix in {".json", ".yaml", ".yml", ".csv", ".xml"}:
        return "data"
    if suffix in ImageReader.IMAGE_EXTENSIONS:
        return "image"
    if suffix in AudioReader.AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VideoReader.VIDEO_EXTENSIONS:
        return "video"
    return "document"


def _extract_title(content: str, file_path: Path) -> str:
    # Try markdown header
    header_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if header_match:
        return header_match.group(1).strip()
    # Try first non-empty line
    for line in content.splitlines():
        if line.strip():
            return line.strip()[:120]
    return file_path.stem


# =========================================================================
# DOCUMENT READERS
# =========================================================================


class DocumentReader:
    @staticmethod
    def read(file_path: Path) -> str:
        raise NotImplementedError


class MarkdownReader(DocumentReader):
    @staticmethod
    def read(file_path: Path) -> str:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


class TextReader(DocumentReader):
    @staticmethod
    def read(file_path: Path) -> str:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


class CodeReader(DocumentReader):
    LANGUAGE_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript-react",
        ".tsx": "typescript-react",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c-header",
        ".hpp": "cpp-header",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".r": "r",
        ".sql": "sql",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "zsh",
        ".ps1": "powershell",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
    }

    @classmethod
    def read(cls, file_path: Path) -> str:
        language = cls.LANGUAGE_MAP.get(file_path.suffix.lower(), "unknown")
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return f"[Language: {language}]\n[File: {file_path.name}]\n\n{content}"


class WebReader(DocumentReader):
    """Reader for web content via URL."""

    @staticmethod
    def read(url: str) -> str:
        try:
            import trafilatura
        except ImportError:
            import subprocess

            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "trafilatura", "--break-system-packages", "-q"]
            )
            import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise RuntimeError(f"Failed to fetch URL: {url}")

        content = trafilatura.extract(downloaded, include_tables=True, include_links=True)
        if not content:
            raise RuntimeError(f"Failed to extract content from URL: {url}")

        # Try to get metadata
        metadata = trafilatura.extract_metadata(downloaded)
        title = getattr(metadata, "title", None) if metadata else None
        author = getattr(metadata, "author", None) if metadata else None
        date = getattr(metadata, "date", None) if metadata else None

        header_parts = [f"[Source: {url}]"]
        if title:
            header_parts.append(f"[Title: {title}]")
        if author:
            header_parts.append(f"[Author: {author}]")
        if date:
            header_parts.append(f"[Date: {date}]")

        return "\n".join(header_parts) + "\n\n" + content


class DataReader(DocumentReader):
    """Reader for structured data files (JSON, YAML, CSV, XML)."""

    DATA_EXTENSIONS = {".json", ".yaml", ".yml", ".csv", ".xml"}

    @classmethod
    def read(cls, file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        format_name = {
            ".json": "JSON",
            ".yaml": "YAML",
            ".yml": "YAML",
            ".csv": "CSV",
            ".xml": "XML",
        }.get(suffix, "Data")

        structure_desc = cls._describe_structure(content, suffix)
        return f"[Format: {format_name}]\n[File: {file_path.name}]\n\n{structure_desc}\n\n--- Raw Content ---\n{content}"

    @classmethod
    def _describe_structure(cls, content: str, suffix: str) -> str:
        """Generate a structural description of the data."""
        try:
            if suffix == ".json":
                import json

                data = json.loads(content)
                return cls._describe_json_structure(data)
            elif suffix in {".yaml", ".yml"}:
                try:
                    import yaml

                    data = yaml.safe_load(content)
                    return cls._describe_json_structure(data)
                except ImportError:
                    return "[YAML parsing unavailable]"
            elif suffix == ".csv":
                return cls._describe_csv_structure(content)
            elif suffix == ".xml":
                return cls._describe_xml_structure(content)
        except Exception as e:
            return f"[Structure analysis failed: {e}]"
        return ""

    @classmethod
    def _describe_json_structure(cls, data: Any, depth: int = 0, max_depth: int = 3) -> str:
        """Describe the structure of JSON/YAML data."""
        indent = "  " * depth
        if depth >= max_depth:
            return f"{indent}..."

        if isinstance(data, dict):
            if not data:
                return f"{indent}{{}}"
            lines = [f"{indent}Object with {len(data)} keys:"]
            for key, value in list(data.items())[:10]:
                value_type = type(value).__name__
                if isinstance(value, dict):
                    lines.append(f"{indent}  - {key}: object ({len(value)} keys)")
                elif isinstance(value, list):
                    lines.append(f"{indent}  - {key}: array ({len(value)} items)")
                else:
                    lines.append(f"{indent}  - {key}: {value_type}")
            if len(data) > 10:
                lines.append(f"{indent}  ... and {len(data) - 10} more keys")
            return "\n".join(lines)
        elif isinstance(data, list):
            if not data:
                return f"{indent}[]"
            lines = [f"{indent}Array with {len(data)} items"]
            if data:
                first = data[0]
                if isinstance(first, dict):
                    lines.append(f"{indent}  Item type: object with keys: {list(first.keys())[:5]}")
                else:
                    lines.append(f"{indent}  Item type: {type(first).__name__}")
            return "\n".join(lines)
        else:
            return f"{indent}{type(data).__name__}: {str(data)[:100]}"

    @classmethod
    def _describe_csv_structure(cls, content: str) -> str:
        """Describe CSV structure."""
        lines = content.strip().split("\n")
        if not lines:
            return "[Empty CSV]"

        header_line = lines[0]
        columns = [col.strip().strip('"').strip("'") for col in header_line.split(",")]

        desc = [f"CSV with {len(columns)} columns and {len(lines) - 1} data rows"]
        desc.append(f"Columns: {', '.join(columns[:10])}")
        if len(columns) > 10:
            desc.append(f"  ... and {len(columns) - 10} more columns")

        return "\n".join(desc)

    @classmethod
    def _describe_xml_structure(cls, content: str) -> str:
        """Describe XML structure (basic)."""
        import re

        root_match = re.search(r"<(\w+)[>\s]", content)
        root_tag = root_match.group(1) if root_match else "unknown"

        tag_counts: dict[str, int] = {}
        for tag in re.findall(r"<(\w+)[>\s]", content):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:10]

        desc = [f"XML document with root element: <{root_tag}>"]
        desc.append(f"Top elements: {', '.join(f'{t}({c})' for t, c in top_tags)}")

        return "\n".join(desc)


class PDFReader(DocumentReader):
    @staticmethod
    def read(file_path: Path) -> str:
        try:
            import pdfplumber
        except ImportError:
            import subprocess

            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "pdfplumber", "--break-system-packages", "-q"]
            )
            import pdfplumber

        text_parts: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"[Page {i + 1}]\n{page_text}")
        return "\n\n".join(text_parts)


class ImageReader(DocumentReader):
    """Reader for images using OCR."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}

    @classmethod
    def read(cls, file_path: Path) -> str:
        try:
            from PIL import Image
        except ImportError:
            import subprocess

            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "Pillow", "--break-system-packages", "-q"]
            )
            from PIL import Image

        try:
            import pytesseract
        except ImportError:
            import subprocess

            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "pytesseract", "--break-system-packages", "-q"]
            )
            import pytesseract

        try:
            image = Image.open(file_path)
            text = pytesseract.image_to_string(image)
            if not text.strip():
                return f"[Image: {file_path.name}]\n[No text detected via OCR]"
            return f"[Image: {file_path.name}]\n[OCR Extracted Text]\n\n{text}"
        except Exception as e:
            return f"[Image: {file_path.name}]\n[OCR failed: {e}]"


class AudioReader(DocumentReader):
    """Reader for audio files using speech-to-text."""

    AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}

    @classmethod
    def read(cls, file_path: Path) -> str:
        try:
            import whisper
        except ImportError:
            import subprocess

            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "openai-whisper", "--break-system-packages", "-q"]
            )
            import whisper

        try:
            model = whisper.load_model("base")
            result = model.transcribe(str(file_path))
            text = result.get("text", "")
            if not text.strip():
                return f"[Audio: {file_path.name}]\n[No speech detected]"
            return f"[Audio: {file_path.name}]\n[Transcription]\n\n{text}"
        except Exception as e:
            return f"[Audio: {file_path.name}]\n[Transcription failed: {e}]"


class VideoReader(DocumentReader):
    """Reader for video files - extracts audio and transcribes."""

    VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".wmv", ".flv"}

    @classmethod
    def read(cls, file_path: Path) -> str:
        try:
            from moviepy.editor import VideoFileClip
        except ImportError:
            import subprocess

            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "moviepy", "--break-system-packages", "-q"]
            )
            from moviepy.editor import VideoFileClip

        try:
            import whisper
        except ImportError:
            import subprocess

            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "openai-whisper", "--break-system-packages", "-q"]
            )
            import whisper

        import tempfile

        try:
            # Extract audio from video
            video = VideoFileClip(str(file_path))
            duration = video.duration

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_audio:
                audio_path = tmp_audio.name
                video.audio.write_audiofile(audio_path, verbose=False, logger=None)
                video.close()

            # Transcribe the audio
            model = whisper.load_model("base")
            result = model.transcribe(audio_path)
            text = result.get("text", "")

            # Clean up temp file
            import os
            os.unlink(audio_path)

            if not text.strip():
                return f"[Video: {file_path.name}]\n[Duration: {duration:.1f}s]\n[No speech detected]"

            return f"[Video: {file_path.name}]\n[Duration: {duration:.1f}s]\n[Transcription]\n\n{text}"

        except Exception as e:
            return f"[Video: {file_path.name}]\n[Transcription failed: {e}]"


def get_reader(file_path: Path) -> DocumentReader:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return PDFReader()
    if suffix in [".md", ".markdown"]:
        return MarkdownReader()
    if suffix in DataReader.DATA_EXTENSIONS:
        return DataReader()
    if suffix in ImageReader.IMAGE_EXTENSIONS:
        return ImageReader()
    if suffix in AudioReader.AUDIO_EXTENSIONS:
        return AudioReader()
    if suffix in VideoReader.VIDEO_EXTENSIONS:
        return VideoReader()
    if suffix in CodeReader.LANGUAGE_MAP:
        return CodeReader()
    return TextReader()


# =========================================================================
# SECTIONING
# =========================================================================


class Sectioner:
    def __init__(self, max_chars: int = 2000, overlap: int = 200):
        self.max_chars = max_chars
        self.overlap = overlap

    def split(self, content: str, file_path: Path) -> list[Section]:
        suffix = file_path.suffix.lower()
        if suffix in [".md", ".markdown"]:
            return self._split_markdown(content)
        return self._split_text(content)

    def _split_markdown(self, content: str) -> list[Section]:
        header_pattern = r"^(#{1,6}\s+.+)$"
        parts = re.split(header_pattern, content, flags=re.MULTILINE)
        sections: list[Section] = []
        current_title = "Introduction"
        current_content = ""
        for part in parts:
            if re.match(header_pattern, part or ""):
                if current_content.strip():
                    sections.append(Section(title=current_title, content=current_content.strip(), index=len(sections)))
                current_title = part.strip().lstrip("# ").strip() or current_title
                current_content = ""
            else:
                current_content += part
        if current_content.strip():
            sections.append(Section(title=current_title, content=current_content.strip(), index=len(sections)))
        if not sections:
            return [Section(title="Document", content=content, index=0)]
        return sections

    def _split_text(self, content: str) -> list[Section]:
        if len(content) <= self.max_chars:
            return [Section(title="Section 1", content=content, index=0)]
        chunks: list[str] = []
        paragraphs = content.split("\n\n")
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 <= self.max_chars:
                current += para + "\n\n"
                continue
            if current:
                chunks.append(current.strip())
            if len(para) > self.max_chars:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current = ""
                for sentence in sentences:
                    if len(current) + len(sentence) <= self.max_chars:
                        current += sentence + " "
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = sentence + " "
            else:
                current = para + "\n\n"
        if current.strip():
            chunks.append(current.strip())
        if self.overlap > 0 and len(chunks) > 1:
            overlapped: list[str] = []
            for i, chunk in enumerate(chunks):
                if i > 0:
                    prev_overlap = chunks[i - 1][-self.overlap :]
                    chunk = f"...{prev_overlap}\n\n{chunk}"
                overlapped.append(chunk)
            chunks = overlapped
        return [Section(title=f"Section {i + 1}", content=chunk, index=i) for i, chunk in enumerate(chunks)]


# =========================================================================
# LLM CLIENT
# =========================================================================


class LLMClient:
    def __init__(self, config: Config):
        self.config = config
        self.endpoint = config.llm_endpoint.rstrip("/")
        self.call_count = 0

    def complete(self, messages: list[dict[str, str]], temperature: float = 0.3) -> str:
        self.call_count += 1
        payload = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.llm_api_key and self.config.llm_api_key != "not-needed":
            headers["Authorization"] = f"Bearer {self.config.llm_api_key}"
        resp = requests.post(
            f"{self.endpoint}/chat/completions",
            json=payload,
            headers=headers,
            timeout=180,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"LLM request failed: {resp.status_code} - {resp.text}")
        return resp.json()["choices"][0]["message"]["content"]

    def complete_json(self, messages: list[dict[str, str]], temperature: float = 0.2) -> dict[str, Any]:
        text = self.complete(messages, temperature=temperature)
        json_text = text.strip()
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", json_text, re.DOTALL)
        if match:
            json_text = match.group(1).strip()
        # Try to find object
        if not json_text.startswith("{"):
            start = json_text.find("{")
            if start != -1:
                json_text = json_text[start:]
        try:
            data = json.loads(json_text)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}


# =========================================================================
# APPRAISAL + EXTRACTION
# =========================================================================


class Appraiser:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def appraise(self, *, content: str, context: dict[str, Any], mode: IngestionMode) -> Appraisal:
        system = (
            "You are Hexis' subconscious appraisal system."
            " Provide a brief, honest emotional assessment of the content."
            " If you feel nothing, say so and keep intensity low."
            " Return STRICT JSON only."
        )
        user = (
            "CONTENT SAMPLE:\n"
            f"{content}\n\n"
            "CONTEXT (JSON):\n"
            f"{json.dumps(context)[:8000]}\n\n"
            "Return JSON with keys:"
            " valence (-1..1), arousal (0..1), primary_emotion (string), intensity (0..1),"
            " goal_relevance (array of {goal, strength}), worldview_tension (0..1), curiosity (0..1),"
            " summary (2-3 sentences)."
        )
        raw = self.llm.complete_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
        )
        return Appraisal(
            valence=float(raw.get("valence", 0.0) or 0.0),
            arousal=float(raw.get("arousal", 0.3) or 0.3),
            primary_emotion=str(raw.get("primary_emotion", "neutral") or "neutral"),
            intensity=float(raw.get("intensity", 0.0) or 0.0),
            goal_relevance=list(raw.get("goal_relevance", []) or []),
            worldview_tension=float(raw.get("worldview_tension", 0.0) or 0.0),
            curiosity=float(raw.get("curiosity", 0.0) or 0.0),
            summary=str(raw.get("summary", "") or ""),
        )


class KnowledgeExtractor:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def extract(
        self,
        *,
        section: Section,
        doc: DocumentInfo,
        appraisal: Appraisal,
        mode: IngestionMode,
        max_items: int,
    ) -> list[Extraction]:
        system = (
            "You extract standalone knowledge worth remembering."
            " Be selective. Return STRICT JSON only."
        )
        guidance = ""
        if doc.source_type == "code":
            guidance = (
                "Focus on what the code does, key interfaces, behaviors, patterns,"
                " and any important constraints or dependencies."
            )
        elif doc.source_type == "data":
            guidance = (
                "Describe the schema, key fields, relationships, and notable values or patterns."
            )
        else:
            guidance = (
                "Extract facts, claims, definitions, procedures, insights, and statistics."
            )
        user = (
            f"DOCUMENT: {doc.title}\n"
            f"SECTION: {section.title}\n"
            f"MODE: {mode.value}\n\n"
            "APPRAISAL:\n"
            f"{json.dumps(appraisal.__dict__, ensure_ascii=False)}\n\n"
            "CONTENT:\n"
            f"{section.content}\n\n"
            f"{guidance}\n\n"
            "Return JSON with key 'items' as an array of objects:\n"
            "  {content, category, confidence, importance, why, connections, supports, contradicts, concepts}\n"
            "  - concepts: array of key concept/entity names this knowledge is an instance of\n"
            "Keep at most "
            + str(max_items)
            + " items."
        )
        raw = self.llm.complete_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.3,
        )
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        out: list[Extraction] = []
        for item in items[:max_items]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            out.append(
                Extraction(
                    content=content,
                    category=str(item.get("category", "fact") or "fact"),
                    confidence=float(item.get("confidence", 0.5) or 0.5),
                    importance=float(item.get("importance", 0.5) or 0.5),
                    why=str(item.get("why", "") or "") or None,
                    connections=[str(c).strip() for c in (item.get("connections") or []) if str(c).strip()],
                    supports=item.get("supports"),
                    contradicts=item.get("contradicts"),
                    concepts=[str(c).strip() for c in (item.get("concepts") or []) if str(c).strip()],
                )
            )
        return out


# =========================================================================
# STORAGE
# =========================================================================


class MemoryStore:
    def __init__(self, config: Config):
        self.config = config
        self.client: CognitiveMemorySync | None = None

    def connect(self) -> None:
        if self.client is not None:
            return
        dsn = (
            f"postgresql://{self.config.db_user}:{self.config.db_password}"
            f"@{self.config.db_host}:{self.config.db_port}/{self.config.db_name}"
        )
        self.client = CognitiveMemorySync.connect(dsn, min_size=1, max_size=5)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def _exec(self, sql: str, *params: Any) -> Any:
        assert self.client is not None
        async def _run():
            async with self.client._async._pool.acquire() as conn:
                return await conn.execute(sql, *params)

        return self.client._loop.run_until_complete(_run())

    def _fetchval(self, sql: str, *params: Any) -> Any:
        assert self.client is not None
        async def _run():
            async with self.client._async._pool.acquire() as conn:
                return await conn.fetchval(sql, *params)

        return self.client._loop.run_until_complete(_run())

    def has_receipt(self, content_hash: str) -> bool:
        if self.client is None:
            self.connect()
        assert self.client is not None
        try:
            receipts = self.client.get_ingestion_receipts(content_hash, [content_hash])
        except Exception:
            return False
        return bool(receipts)

    def set_affective_state(self, appraisal: Appraisal) -> None:
        if self.client is None:
            self.connect()
        payload = json.dumps(appraisal.to_state_payload(source="ingest"))
        try:
            self._fetchval("SELECT set_current_affective_state($1::jsonb)", payload)
        except Exception:
            pass

    def create_encounter_memory(
        self,
        *,
        text: str,
        source: dict[str, Any],
        emotional_valence: float,
        context: dict[str, Any] | None,
        importance: float,
    ) -> str:
        if self.client is None:
            self.connect()
        assert self.client is not None
        memory_id = self.client.remember(
            text,
            type=ApiMemoryType.EPISODIC,
            importance=importance,
            emotional_valence=emotional_valence,
            context=context,
            source_attribution=source,
        )
        return str(memory_id)

    def create_semantic_memory(
        self,
        *,
        content: str,
        confidence: float,
        category: str,
        related_concepts: list[str],
        source: dict[str, Any],
        importance: float,
        trust: float | None,
    ) -> str:
        if self.client is None:
            self.connect()
        payload_sources = json.dumps([source])
        return str(
            self._fetchval(
                "SELECT create_semantic_memory($1::text,$2::float,$3::text[],$4::text[],$5::jsonb,$6::float,$7::jsonb,$8::float)",
                content,
                confidence,
                [category],
                related_concepts,
                payload_sources,
                importance,
                json.dumps(source),
                trust,
            )
        )

    def add_source(self, memory_id: str, source: dict[str, Any]) -> None:
        if self.client is None:
            self.connect()
        assert self.client is not None
        self.client._loop.run_until_complete(
            self.client._async.add_source(UUID(memory_id), source)
        )

    def boost_confidence(self, memory_id: str, boost: float = 0.05) -> None:
        """Boost confidence of a memory when it's corroborated by a new source."""
        if self.client is None:
            self.connect()
        self._exec(
            """
            UPDATE memories SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{confidence}',
                to_jsonb(LEAST(1.0, COALESCE((metadata->>'confidence')::float, 0.5) + $2))
            )
            WHERE id = $1::uuid
            """,
            memory_id,
            boost,
        )

    def link_concept(self, memory_id: str, concept: str, strength: float = 1.0) -> None:
        """Link a memory to a concept in the knowledge graph."""
        if self.client is None:
            self.connect()
        self._fetchval(
            "SELECT link_memory_to_concept($1::uuid, $2::text, $3::float)",
            memory_id,
            concept,
            strength,
        )

    def recall_similar_semantic(self, query: str, limit: int = 5):
        if self.client is None:
            self.connect()
        assert self.client is not None
        return self.client.recall(
            query,
            limit=limit,
            memory_types=[ApiMemoryType.SEMANTIC],
        ).memories

    def connect_memories(self, from_id: str, to_id: str, relationship: RelationshipType, confidence: float = 0.8) -> None:
        if self.client is None:
            self.connect()
        assert self.client is not None
        self.client.connect_memories(
            from_id,
            to_id,
            relationship,
            confidence=confidence,
        )

    def update_decay_rate(self, memory_id: str, decay_rate: float) -> None:
        if self.client is None:
            self.connect()
        try:
            self._exec("UPDATE memories SET decay_rate = $1 WHERE id = $2::uuid", decay_rate, memory_id)
        except Exception:
            pass

    def fetch_appraisal_context(self) -> dict[str, Any]:
        if self.client is None:
            self.connect()
        try:
            raw = self._fetchval(
                """
                SELECT jsonb_build_object(
                    'emotional_state', get_current_affective_state(),
                    'goals', get_goals_snapshot(),
                    'worldview', get_worldview_context(),
                    'recent_memories', get_recent_context(5)
                )
                """
            )
            if isinstance(raw, str):
                return json.loads(raw)
            if isinstance(raw, dict):
                return raw
        except Exception:
            return {}
        return {}

    def store_metrics(self, metrics: "IngestionMetrics") -> None:
        """Store ingestion metrics for observability."""
        if self.client is None:
            self.connect()
        try:
            self._exec(
                """
                INSERT INTO ingestion_metrics (
                    source_type, source_size_bytes, word_count, mode,
                    appraisal_valence, appraisal_arousal, appraisal_emotion, appraisal_intensity,
                    extraction_count, dedup_count, memory_count, llm_calls,
                    duration_seconds, errors
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb
                )
                """,
                metrics.source_type,
                metrics.source_size_bytes,
                metrics.word_count,
                metrics.mode,
                metrics.appraisal_valence,
                metrics.appraisal_arousal,
                metrics.appraisal_emotion,
                metrics.appraisal_intensity,
                metrics.extraction_count,
                metrics.dedup_count,
                metrics.memory_count,
                metrics.llm_calls,
                metrics.duration_seconds,
                json.dumps(metrics.errors),
            )
        except Exception:
            pass  # Don't fail ingestion due to metrics storage

    def check_archived_for_query(self, query: str, threshold: float = 0.75, limit: int = 5) -> list[dict[str, Any]]:
        """Check if archived content matches a query."""
        if self.client is None:
            self.connect()
        try:
            rows = self._fetchval(
                """
                SELECT jsonb_agg(jsonb_build_object(
                    'memory_id', memory_id,
                    'content_hash', content_hash,
                    'title', title,
                    'similarity', similarity,
                    'source_path', source_path
                ))
                FROM check_archived_for_query($1, $2, $3)
                """,
                query,
                threshold,
                limit,
            )
            if not rows:
                return []
            result = json.loads(rows) if isinstance(rows, str) else rows
            return result if result else []
        except Exception:
            return []

    def mark_archived_processed(self, memory_id: str) -> bool:
        """Mark an archived memory as processed."""
        if self.client is None:
            self.connect()
        try:
            result = self._fetchval(
                "SELECT mark_archived_as_processed($1::uuid)",
                memory_id,
            )
            return bool(result)
        except Exception:
            return False


# =========================================================================
# INGESTION PIPELINE
# =========================================================================


class IngestionPipeline:
    SUPPORTED_EXTENSIONS = (
        # Documents
        {".md", ".markdown", ".txt", ".text", ".pdf"}
        # Code
        | set(CodeReader.LANGUAGE_MAP.keys())
        # Data
        | DataReader.DATA_EXTENSIONS
        # Media
        | ImageReader.IMAGE_EXTENSIONS
        | AudioReader.AUDIO_EXTENSIONS
        | VideoReader.VIDEO_EXTENSIONS
    )

    def __init__(self, config: Config):
        self.config = config
        self.config.mode = _normalize_mode(self.config.mode)
        self.sectioner = Sectioner(config.max_section_chars, config.chunk_overlap)
        self.llm = LLMClient(config)
        self.appraiser = Appraiser(self.llm)
        self.extractor = KnowledgeExtractor(self.llm)
        self.store = MemoryStore(config)
        self.stats = {"files_processed": 0, "memories_created": 0, "errors": 0}

    def ingest_file(self, file_path: Path) -> int:
        # Initialize metrics tracking
        metrics = IngestionMetrics(start_time=time.time())

        if _should_cancel(self.config):
            raise RuntimeError("Ingestion cancelled")
        if not file_path.exists():
            _emit(self.config, f"File not found: {file_path}")
            return 0
        if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            _emit(self.config, f"Unsupported file type: {file_path.suffix}")
            return 0

        if self.config.verbose:
            _emit(self.config, f"\nProcessing: {file_path}")

        # Track LLM calls at start
        llm_calls_start = self.llm.call_count

        reader = get_reader(file_path)
        try:
            content = reader.read(file_path)
            metrics.source_size_bytes = len(content.encode("utf-8"))
        except Exception as exc:
            _emit(self.config, f"  Error reading file: {exc}")
            self.stats["errors"] += 1
            metrics.errors.append(str(exc))
            return 0

        title = _extract_title(content, file_path)
        words = _word_count(content)
        mode = _select_mode(self.config, words)
        source_type = _infer_source_type(file_path)
        content_hash = _hash_text(content)

        # Update metrics
        metrics.word_count = words
        metrics.mode = mode.value
        metrics.source_type = source_type

        doc = DocumentInfo(
            title=title,
            source_type=source_type,
            content_hash=content_hash,
            word_count=words,
            path=str(file_path),
            file_type=file_path.suffix.lower(),
        )

        if self.store.has_receipt(content_hash):
            if self.config.verbose:
                _emit(self.config, f"  Already ingested (hash={content_hash[:8]}...). Skipping.")
            return 0

        sections = self.sectioner.split(content, file_path)

        if self.config.verbose:
            _emit(self.config, f"  Mode: {mode.value} | Words: {words} | Sections: {len(sections)}")

        # Archive mode: register encounter only
        if mode == IngestionMode.ARCHIVE:
            encounter_id = self._create_archive_encounter(doc)
            self.stats["files_processed"] += 1
            self.stats["memories_created"] += 1 if encounter_id else 0

            # Store metrics for archive mode
            metrics.memory_count = 1 if encounter_id else 0
            metrics.llm_calls = self.llm.call_count - llm_calls_start
            metrics.duration_seconds = time.time() - metrics.start_time
            self.store.store_metrics(metrics)

            return 1 if encounter_id else 0

        # Appraise (overall for standard/shallow; per section for deep)
        base_context = self._build_appraisal_context(doc)
        overall_appraisal = None
        if mode in (IngestionMode.STANDARD, IngestionMode.SHALLOW):
            sample = self._sample_content(content)
            overall_appraisal = self.appraiser.appraise(content=sample, context=base_context, mode=mode)
            self.store.set_affective_state(overall_appraisal)
            # Update metrics with appraisal
            metrics.appraisal_valence = overall_appraisal.valence
            metrics.appraisal_arousal = overall_appraisal.arousal
            metrics.appraisal_emotion = overall_appraisal.primary_emotion
            metrics.appraisal_intensity = overall_appraisal.intensity

        encounter_id = self._create_encounter_memory(doc, overall_appraisal, mode)

        created_ids: list[str] = []
        total_extractions = 0
        dedup_count = 0

        for section in sections:
            if _should_cancel(self.config):
                raise RuntimeError("Ingestion cancelled")
            if self._skip_section(section.title):
                continue
            appraisal = overall_appraisal
            if mode == IngestionMode.DEEP:
                sample = self._sample_content(section.content)
                appraisal = self.appraiser.appraise(content=sample, context=base_context, mode=mode)
                self.store.set_affective_state(appraisal)
                # Track last appraisal for deep mode
                metrics.appraisal_valence = appraisal.valence
                metrics.appraisal_arousal = appraisal.arousal
                metrics.appraisal_emotion = appraisal.primary_emotion
                metrics.appraisal_intensity = appraisal.intensity
            if mode == IngestionMode.SHALLOW:
                # Only use the first section for shallow extraction
                if section.index > 0:
                    break
            if appraisal is None:
                appraisal = Appraisal()
            max_items = self.config.max_facts_per_section
            if mode == IngestionMode.SHALLOW:
                max_items = max(3, min(5, max_items))
            extractions = self.extractor.extract(
                section=section,
                doc=doc,
                appraisal=appraisal,
                mode=mode,
                max_items=max_items,
            )
            if not extractions:
                continue
            total_extractions += len(extractions)
            new_memories = self._create_semantic_memories(doc, encounter_id, appraisal, extractions)
            dedup_count += len(extractions) - len(new_memories)
            created_ids.extend(new_memories)

        if self.config.verbose:
            _emit(self.config, f"  Created {len(created_ids)} semantic memories")

        self.stats["files_processed"] += 1
        self.stats["memories_created"] += len(created_ids) + (1 if encounter_id else 0)

        # Store metrics
        metrics.extraction_count = total_extractions
        metrics.dedup_count = dedup_count
        metrics.memory_count = len(created_ids) + (1 if encounter_id else 0)
        metrics.llm_calls = self.llm.call_count - llm_calls_start
        metrics.duration_seconds = time.time() - metrics.start_time
        self.store.store_metrics(metrics)

        return len(created_ids)

    def ingest_directory(self, dir_path: Path, recursive: bool = True) -> int:
        if _should_cancel(self.config):
            raise RuntimeError("Ingestion cancelled")
        if not dir_path.exists() or not dir_path.is_dir():
            _emit(self.config, f"Directory not found: {dir_path}")
            return 0
        pattern = "**/*" if recursive else "*"
        files = [f for f in dir_path.glob(pattern) if f.is_file() and f.suffix.lower() in self.SUPPORTED_EXTENSIONS]
        if self.config.verbose:
            _emit(self.config, f"Found {len(files)} files to process")
        total = 0
        for file_path in files:
            total += self.ingest_file(file_path)
        return total

    def ingest_url(self, url: str, title: str | None = None) -> int:
        """Ingest content from a URL."""
        metrics = IngestionMetrics(start_time=time.time())
        llm_calls_start = self.llm.call_count

        if self.config.verbose:
            _emit(self.config, f"\nFetching: {url}")

        try:
            content = WebReader.read(url)
            metrics.source_size_bytes = len(content.encode("utf-8"))
        except Exception as exc:
            _emit(self.config, f"  Error fetching URL: {exc}")
            self.stats["errors"] += 1
            metrics.errors.append(str(exc))
            return 0

        content_hash = _hash_text(content)
        words = _word_count(content)
        mode = _select_mode(self.config, words)

        # Extract title from content header if not provided
        if not title:
            import re
            title_match = re.search(r"\[Title: (.+?)\]", content)
            if title_match:
                title = title_match.group(1)
            else:
                title = url.split("/")[-1] or url

        metrics.word_count = words
        metrics.mode = mode.value
        metrics.source_type = "web"

        doc = DocumentInfo(
            title=title,
            source_type="web",
            content_hash=content_hash,
            word_count=words,
            path=url,
            file_type=".html",
        )

        if self.store.has_receipt(content_hash):
            if self.config.verbose:
                _emit(self.config, f"  Already ingested (hash={content_hash[:8]}...)")
            return 0

        virtual_path = Path("web_content.md")
        sections = self.sectioner.split(content, virtual_path)

        if self.config.verbose:
            _emit(self.config, f"  Mode: {mode.value} | Words: {words} | Sections: {len(sections)}")

        if mode == IngestionMode.ARCHIVE:
            encounter_id = self._create_archive_encounter(doc)
            self.stats["files_processed"] += 1
            self.stats["memories_created"] += 1 if encounter_id else 0
            metrics.memory_count = 1 if encounter_id else 0
            metrics.llm_calls = self.llm.call_count - llm_calls_start
            metrics.duration_seconds = time.time() - metrics.start_time
            self.store.store_metrics(metrics)
            return 1 if encounter_id else 0

        base_context = self._build_appraisal_context(doc)
        sample = self._sample_content(content)
        appraisal = self.appraiser.appraise(content=sample, context=base_context, mode=mode)
        self.store.set_affective_state(appraisal)

        metrics.appraisal_valence = appraisal.valence
        metrics.appraisal_arousal = appraisal.arousal
        metrics.appraisal_emotion = appraisal.primary_emotion
        metrics.appraisal_intensity = appraisal.intensity

        encounter_id = self._create_encounter_memory(doc, appraisal, mode)

        created_ids: list[str] = []
        total_extractions = 0
        dedup_count = 0

        for section in sections:
            if self._skip_section(section.title):
                continue
            section_appraisal = appraisal
            if mode == IngestionMode.DEEP:
                sample = self._sample_content(section.content)
                section_appraisal = self.appraiser.appraise(content=sample, context=base_context, mode=mode)
                self.store.set_affective_state(section_appraisal)
            if mode == IngestionMode.SHALLOW and section.index > 0:
                break

            max_items = self.config.max_facts_per_section
            if mode == IngestionMode.SHALLOW:
                max_items = max(3, min(5, max_items))

            extractions = self.extractor.extract(
                section=section,
                doc=doc,
                appraisal=section_appraisal,
                mode=mode,
                max_items=max_items,
            )
            if extractions:
                total_extractions += len(extractions)
                new_memories = self._create_semantic_memories(doc, encounter_id, section_appraisal, extractions)
                dedup_count += len(extractions) - len(new_memories)
                created_ids.extend(new_memories)

        if self.config.verbose:
            _emit(self.config, f"  Created {len(created_ids)} semantic memories")

        self.stats["files_processed"] += 1
        self.stats["memories_created"] += len(created_ids) + (1 if encounter_id else 0)

        metrics.extraction_count = total_extractions
        metrics.dedup_count = dedup_count
        metrics.memory_count = len(created_ids) + (1 if encounter_id else 0)
        metrics.llm_calls = self.llm.call_count - llm_calls_start
        metrics.duration_seconds = time.time() - metrics.start_time
        self.store.store_metrics(metrics)

        return len(created_ids)

    def _sample_content(self, content: str, limit: int = 2000) -> str:
        if len(content) <= limit:
            return content
        head = content[:limit]
        tail = content[-limit:]
        return f"{head}\n\n...\n\n{tail}"

    def _build_appraisal_context(self, doc: DocumentInfo) -> dict[str, Any]:
        ctx = {
            "document": {
                "title": doc.title,
                "source_type": doc.source_type,
                "word_count": doc.word_count,
            }
        }
        try:
            ctx.update(self.store.fetch_appraisal_context())
        except Exception:
            pass
        return ctx

    def _source_payload(self, doc: DocumentInfo) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "kind": doc.source_type,
            "ref": doc.content_hash,
            "label": doc.title,
            "observed_at": now,
            "content_hash": doc.content_hash,
            "path": doc.path,
        }
        if self.config.base_trust is not None:
            payload["trust"] = float(self.config.base_trust)
        return payload

    def _create_archive_encounter(self, doc: DocumentInfo) -> str | None:
        source = self._source_payload(doc)
        text = f"I have access to '{doc.title}' but haven't engaged with it yet."
        context = {
            "activity": "archived",
            "source_type": doc.source_type,
            "source_ref": doc.content_hash,
            "word_count": doc.word_count,
            "mode": IngestionMode.ARCHIVE.value,
            "awaiting_processing": True,
        }
        importance = max(self.config.min_importance_floor or 0.0, 0.2)
        encounter_id = self.store.create_encounter_memory(
            text=text,
            source=source,
            emotional_valence=0.0,
            context=context,
            importance=importance,
        )
        self._apply_decay(encounter_id, intensity=0.0)
        return encounter_id

    def _create_encounter_memory(self, doc: DocumentInfo, appraisal: Appraisal | None, mode: IngestionMode) -> str | None:
        source = self._source_payload(doc)
        appraisal = appraisal or Appraisal()
        summary = appraisal.summary or ""
        if not summary:
            summary = f"It felt {appraisal.primary_emotion} with intensity {appraisal.intensity:.2f}."
        text = f"I read '{doc.title}'. {summary}"
        context = {
            "activity": "reading",
            "source_type": doc.source_type,
            "source_ref": doc.content_hash,
            "word_count": doc.word_count,
            "mode": mode.value,
            "appraisal": appraisal.__dict__,
        }
        importance = max(self.config.min_importance_floor or 0.0, 0.4 + appraisal.intensity * 0.4)
        encounter_id = self.store.create_encounter_memory(
            text=text,
            source=source,
            emotional_valence=appraisal.valence,
            context=context,
            importance=importance,
        )
        self._apply_decay(encounter_id, intensity=appraisal.intensity)
        return encounter_id

    def _apply_decay(self, memory_id: str, intensity: float) -> None:
        if self.config.permanent:
            self.store.update_decay_rate(memory_id, 0.0)
            return
        decay = _decay_rate_for_intensity(intensity)
        self.store.update_decay_rate(memory_id, decay)

    def _create_semantic_memories(
        self,
        doc: DocumentInfo,
        encounter_id: str | None,
        appraisal: Appraisal,
        extractions: list[Extraction],
    ) -> list[str]:
        created: list[str] = []
        source = self._source_payload(doc)
        for ext in extractions:
            if ext.confidence < self.config.min_confidence_threshold:
                continue
            importance = ext.importance
            if self.config.min_importance_floor is not None:
                importance = max(importance, self.config.min_importance_floor)
            trust = self.config.base_trust
            # Dedup check
            similar = self.store.recall_similar_semantic(ext.content, limit=5)
            match = None
            for mem in similar:
                if mem.similarity is None:
                    continue
                if mem.similarity >= 0.92:
                    match = (mem, "duplicate")
                    break
                if mem.similarity >= 0.8:
                    match = (mem, "related")
            if match and match[1] == "duplicate":
                try:
                    self.store.add_source(str(match[0].id), source)
                    self.store.boost_confidence(str(match[0].id), 0.05)
                except Exception:
                    pass
                continue

            memory_id = self.store.create_semantic_memory(
                content=ext.content,
                confidence=ext.confidence,
                category=ext.category,
                related_concepts=ext.connections,
                source=source,
                importance=importance,
                trust=trust,
            )
            created.append(memory_id)

            # Link extracted concepts to the knowledge graph
            for concept in ext.concepts:
                try:
                    self.store.link_concept(memory_id, concept.strip())
                except Exception:
                    pass

            # Create supports/contradicts edges to worldview memories
            if ext.supports:
                worldview_id = self._find_worldview_by_content(ext.supports)
                if worldview_id:
                    try:
                        self.store.connect_memories(
                            memory_id, worldview_id, RelationshipType.SUPPORTS, confidence=ext.confidence
                        )
                    except Exception:
                        pass

            if ext.contradicts:
                worldview_id = self._find_worldview_by_content(ext.contradicts)
                if worldview_id:
                    try:
                        self.store.connect_memories(
                            memory_id, worldview_id, RelationshipType.CONTRADICTS, confidence=ext.confidence
                        )
                    except Exception:
                        pass

            if encounter_id:
                try:
                    self.store.connect_memories(memory_id, encounter_id, RelationshipType.DERIVED_FROM, confidence=0.9)
                except Exception:
                    pass
            if match and match[1] == "related":
                try:
                    self.store.connect_memories(memory_id, str(match[0].id), RelationshipType.ASSOCIATED, confidence=0.6)
                except Exception:
                    pass
            self._apply_decay(memory_id, intensity=appraisal.intensity)
        return created

    def _find_worldview_by_content(self, hint: str) -> str | None:
        """Find a worldview memory matching the given hint."""
        if not hint or not hint.strip():
            return None
        try:
            results = self.store.client.recall(
                hint.strip(),
                limit=3,
                memory_types=[ApiMemoryType.WORLDVIEW],
            )
            for mem in results.memories:
                if mem.similarity is not None and mem.similarity >= 0.7:
                    return str(mem.id)
        except Exception:
            pass
        return None

    def _skip_section(self, title: str) -> bool:
        lowered = title.strip().lower()
        return any(skip in lowered for skip in self.config.skip_sections)

    def check_and_process_archived(self, query: str, threshold: float = 0.75) -> list[str]:
        """
        Check if any archived content matches the query and process it.

        This implements retrieval-triggered processing: when a query surfaces
        archived content that hasn't been fully processed, we upgrade it now.

        Returns list of content hashes that were processed.
        """
        if self.store.client is None:
            self.store.connect()

        # Find archived content matching the query
        rows = self.store._fetchval(
            """
            SELECT jsonb_agg(jsonb_build_object(
                'memory_id', memory_id,
                'content_hash', content_hash,
                'title', title,
                'similarity', similarity,
                'source_path', source_path
            ))
            FROM check_archived_for_query($1, $2, 5)
            """,
            query,
            threshold,
        )

        if not rows:
            return []

        archived = json.loads(rows) if isinstance(rows, str) else rows
        if not archived:
            return []

        processed_hashes: list[str] = []

        for item in archived:
            if not item:
                continue

            content_hash = item.get("content_hash")
            source_path = item.get("source_path")
            title = item.get("title")
            memory_id = item.get("memory_id")

            if not content_hash:
                continue

            _emit(self.config, f"Processing archived content triggered by query: {title}")

            # Attempt to re-read the source file if it exists
            if source_path and source_path != "stdin" and not source_path.startswith("http"):
                path = Path(source_path)
                if path.exists():
                    # Re-ingest the file with the current mode (not archive)
                    original_mode = self.config.mode
                    self.config.mode = IngestionMode.STANDARD
                    try:
                        # Mark as processed first to avoid duplicate detection
                        self.store._fetchval(
                            "SELECT mark_archived_as_processed($1::uuid)",
                            memory_id,
                        )
                        self.ingest_file(path)
                        processed_hashes.append(content_hash)
                    finally:
                        self.config.mode = original_mode
                    continue

            # If source file not available, just mark as processed
            self.store._fetchval(
                "SELECT mark_archived_as_processed($1::uuid)",
                memory_id,
            )
            processed_hashes.append(content_hash)

        return processed_hashes

    def print_stats(self) -> None:
        _emit(self.config, "\n" + "=" * 50)
        _emit(self.config, "INGESTION COMPLETE")
        _emit(self.config, "=" * 50)
        _emit(self.config, f"Files processed:   {self.stats['files_processed']}")
        _emit(self.config, f"Memories created:  {self.stats['memories_created']}")
        _emit(self.config, f"Errors:            {self.stats['errors']}")
        _emit(self.config, "=" * 50)

    def close(self) -> None:
        self.store.close()


# =========================================================================
# ARCHIVED CONTENT PROCESSOR
# =========================================================================


class ArchivedContentProcessor:
    """
    Processor for upgrading archived content to full memories.

    This can be used:
    1. During recall - when a query surfaces relevant archived content
    2. By maintenance workers - batch processing of pending archives
    3. By CLI - manual processing of specific content
    """

    def __init__(self, config: Config):
        self.config = config
        self.pipeline = IngestionPipeline(config)

    def process_for_query(self, query: str, threshold: float = 0.75) -> list[str]:
        """
        Check if archived content matches a query and process it.

        Returns list of content hashes that were processed.
        """
        return self.pipeline.check_and_process_archived(query, threshold)

    def process_by_hash(self, content_hash: str) -> bool:
        """Process a specific archived item by content hash."""
        archived = self.pipeline.store.check_archived_for_query(
            content_hash, threshold=0.0, limit=1
        )

        if not archived:
            # Try direct lookup
            row = self.pipeline.store._fetchval(
                """
                SELECT jsonb_build_object(
                    'memory_id', id,
                    'content_hash', source_attribution->>'content_hash',
                    'title', source_attribution->>'label',
                    'source_path', source_attribution->>'path'
                )
                FROM memories
                WHERE type = 'episodic'
                  AND source_attribution->>'content_hash' = $1
                  AND metadata->>'awaiting_processing' = 'true'
                LIMIT 1
                """,
                content_hash,
            )
            if not row:
                return False
            archived = [json.loads(row) if isinstance(row, str) else row]

        for item in archived:
            if not item:
                continue

            source_path = item.get("source_path")
            memory_id = item.get("memory_id")

            if source_path and source_path != "stdin" and not source_path.startswith("http"):
                path = Path(source_path)
                if path.exists():
                    self.pipeline.store.mark_archived_processed(memory_id)
                    original_mode = self.config.mode
                    self.config.mode = IngestionMode.STANDARD
                    try:
                        self.pipeline.ingest_file(path)
                    finally:
                        self.config.mode = original_mode
                    return True

            # Mark as processed even if file not found
            return self.pipeline.store.mark_archived_processed(memory_id)

        return False

    def process_batch(self, limit: int = 10) -> int:
        """Process a batch of archived items."""
        rows = self.pipeline.store._fetchval(
            """
            SELECT ARRAY_AGG(source_attribution->>'content_hash')
            FROM memories
            WHERE type = 'episodic'
              AND metadata->>'awaiting_processing' = 'true'
            ORDER BY importance DESC, created_at ASC
            LIMIT $1
            """,
            limit,
        )

        if not rows:
            return 0

        hashes = list(rows) if rows else []
        count = 0
        for h in hashes:
            if h and self.process_by_hash(h):
                count += 1

        return count

    def close(self) -> None:
        self.pipeline.close()


# =========================================================================
# CLI
# =========================================================================


def _get_db_env_defaults() -> dict[str, Any]:
    """Get database configuration from environment variables."""
    env_db_port_raw = os.getenv("POSTGRES_PORT")
    try:
        env_db_port = int(env_db_port_raw) if env_db_port_raw else 43815
    except ValueError:
        env_db_port = 43815
    return {
        "db_host": os.getenv("POSTGRES_HOST", "localhost"),
        "db_port": env_db_port,
        "db_name": os.getenv("POSTGRES_DB", "hexis_memory"),
        "db_user": os.getenv("POSTGRES_USER", "postgres"),
        "db_password": os.getenv("POSTGRES_PASSWORD", "password"),
    }


def _add_common_args(parser: argparse.ArgumentParser, env_defaults: dict[str, Any]) -> None:
    """Add common arguments shared across subcommands."""
    parser.add_argument("--endpoint", "-e", default="http://localhost:11434/v1", help="LLM endpoint")
    parser.add_argument("--model", "-m", default="llama3.2", help="LLM model name")
    parser.add_argument("--api-key", default="not-needed", help="LLM API key")

    parser.add_argument("--db-host", default=env_defaults["db_host"], help="Database host")
    parser.add_argument("--db-port", type=int, default=env_defaults["db_port"], help="Database port")
    parser.add_argument("--db-name", default=env_defaults["db_name"], help="Database name")
    parser.add_argument("--db-user", default=env_defaults["db_user"], help="Database user")
    parser.add_argument("--db-password", default=env_defaults["db_password"], help="Database password")

    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress verbose output")


def _build_config_from_args(args: argparse.Namespace) -> Config:
    """Build Config from parsed arguments."""
    return Config(
        llm_endpoint=getattr(args, "endpoint", "http://localhost:11434/v1"),
        llm_model=getattr(args, "model", "llama3.2"),
        llm_api_key=getattr(args, "api_key", "not-needed"),
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_password=args.db_password,
        mode=_normalize_mode(getattr(args, "mode", "auto")),
        min_importance_floor=getattr(args, "min_importance", None),
        permanent=getattr(args, "permanent", False),
        base_trust=getattr(args, "base_trust", None),
        verbose=not getattr(args, "quiet", False),
    )


def _cmd_ingest(args: argparse.Namespace) -> None:
    """Handle the ingest subcommand."""
    config = _build_config_from_args(args)
    pipeline = IngestionPipeline(config)

    try:
        if args.stdin:
            count = _ingest_stdin(pipeline, args)
        elif args.url:
            count = _ingest_url(pipeline, args)
        elif args.file:
            count = pipeline.ingest_file(args.file)
        elif args.input:
            count = pipeline.ingest_directory(args.input, recursive=not args.no_recursive)
        else:
            print("Error: No input source specified")
            return
        pipeline.print_stats()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        pipeline.close()


def _ingest_stdin(pipeline: IngestionPipeline, args: argparse.Namespace) -> int:
    """Ingest content from stdin."""
    content = sys.stdin.read()
    if not content.strip():
        _emit(pipeline.config, "No content received from stdin")
        return 0

    content_type = getattr(args, "stdin_type", "text") or "text"
    title = getattr(args, "stdin_title", None) or f"stdin-{_hash_text(content)[:8]}"

    # Create a virtual DocumentInfo
    content_hash = _hash_text(content)
    words = _word_count(content)
    mode = _select_mode(pipeline.config, words)

    source_type_map = {
        "text": "document",
        "markdown": "document",
        "code": "code",
        "json": "data",
        "yaml": "data",
        "data": "data",
    }
    source_type = source_type_map.get(content_type, "document")

    doc = DocumentInfo(
        title=title,
        source_type=source_type,
        content_hash=content_hash,
        word_count=words,
        path="stdin",
        file_type=f".{content_type}",
    )

    if pipeline.store.has_receipt(content_hash):
        _emit(pipeline.config, f"Content already ingested (hash={content_hash[:8]}...)")
        return 0

    # Create virtual path for sectioning
    virtual_path = Path(f"stdin.{content_type}")
    sections = pipeline.sectioner.split(content, virtual_path)

    _emit(pipeline.config, f"Processing stdin: {title}")
    _emit(pipeline.config, f"  Mode: {mode.value} | Words: {words} | Sections: {len(sections)}")

    if mode == IngestionMode.ARCHIVE:
        encounter_id = pipeline._create_archive_encounter(doc)
        pipeline.stats["files_processed"] += 1
        pipeline.stats["memories_created"] += 1 if encounter_id else 0
        return 1 if encounter_id else 0

    # Appraise and process
    base_context = pipeline._build_appraisal_context(doc)
    sample = pipeline._sample_content(content)
    appraisal = pipeline.appraiser.appraise(content=sample, context=base_context, mode=mode)
    pipeline.store.set_affective_state(appraisal)

    encounter_id = pipeline._create_encounter_memory(doc, appraisal, mode)

    created_ids: list[str] = []
    for section in sections:
        if pipeline._skip_section(section.title):
            continue
        section_appraisal = appraisal
        if mode == IngestionMode.DEEP:
            sample = pipeline._sample_content(section.content)
            section_appraisal = pipeline.appraiser.appraise(content=sample, context=base_context, mode=mode)
            pipeline.store.set_affective_state(section_appraisal)
        if mode == IngestionMode.SHALLOW and section.index > 0:
            break

        max_items = pipeline.config.max_facts_per_section
        if mode == IngestionMode.SHALLOW:
            max_items = max(3, min(5, max_items))

        extractions = pipeline.extractor.extract(
            section=section,
            doc=doc,
            appraisal=section_appraisal,
            mode=mode,
            max_items=max_items,
        )
        if extractions:
            created_ids.extend(pipeline._create_semantic_memories(doc, encounter_id, section_appraisal, extractions))

    _emit(pipeline.config, f"  Created {len(created_ids)} semantic memories")
    pipeline.stats["files_processed"] += 1
    pipeline.stats["memories_created"] += len(created_ids) + (1 if encounter_id else 0)
    return len(created_ids)


def _ingest_url(pipeline: IngestionPipeline, args: argparse.Namespace) -> int:
    """Ingest content from a URL."""
    title = getattr(args, "title", None)
    return pipeline.ingest_url(args.url, title=title)


def _cmd_status(args: argparse.Namespace) -> None:
    """Handle the status subcommand."""
    config = _build_config_from_args(args)
    store = MemoryStore(config)
    store.connect()

    try:
        if args.pending:
            # Query for archived/pending memories
            rows = store._fetchval(
                """
                SELECT jsonb_agg(jsonb_build_object(
                    'id', id,
                    'title', source_attribution->>'label',
                    'hash', source_attribution->>'content_hash',
                    'created_at', created_at
                ))
                FROM memories
                WHERE type = 'episodic'
                  AND metadata->>'awaiting_processing' = 'true'
                ORDER BY created_at DESC
                LIMIT 50
                """
            )
            pending = json.loads(rows) if rows else []

            if args.json:
                print(json.dumps(pending, indent=2, default=str))
            else:
                if not pending:
                    print("No pending ingestions")
                else:
                    print(f"Pending ingestions: {len(pending)}")
                    for p in pending:
                        print(f"  - {p.get('title', 'Unknown')} ({p.get('hash', '')[:8]}...)")
        else:
            # General ingestion stats
            stats = store._fetchval(
                """
                SELECT jsonb_build_object(
                    'total_memories', (SELECT COUNT(*) FROM memories),
                    'episodic', (SELECT COUNT(*) FROM memories WHERE type = 'episodic'),
                    'semantic', (SELECT COUNT(*) FROM memories WHERE type = 'semantic'),
                    'pending', (SELECT COUNT(*) FROM memories WHERE type = 'episodic' AND metadata->>'awaiting_processing' = 'true'),
                    'recent_24h', (SELECT COUNT(*) FROM memories WHERE created_at > NOW() - INTERVAL '24 hours')
                )
                """
            )
            stats_data = json.loads(stats) if stats else {}

            if args.json:
                print(json.dumps(stats_data, indent=2))
            else:
                print("Ingestion Status:")
                print(f"  Total memories:     {stats_data.get('total_memories', 0)}")
                print(f"  Episodic memories:  {stats_data.get('episodic', 0)}")
                print(f"  Semantic memories:  {stats_data.get('semantic', 0)}")
                print(f"  Pending processing: {stats_data.get('pending', 0)}")
                print(f"  Last 24 hours:      {stats_data.get('recent_24h', 0)}")
    finally:
        store.close()


def _cmd_process(args: argparse.Namespace) -> None:
    """Handle the process subcommand - upgrade archived content."""
    config = _build_config_from_args(args)
    processor = ArchivedContentProcessor(config)

    try:
        if args.content_hash:
            success = processor.process_by_hash(args.content_hash)
            print(f"Processed: {'Yes' if success else 'No (not found or failed)'}")
        elif args.all_archived:
            count = processor.process_batch(limit=getattr(args, "limit", 10))
            print(f"Processed {count} archived items")
        else:
            print("Error: Specify --content-hash or --all-archived")
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    finally:
        processor.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hexis Universal Ingestion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Subcommands:
  ingest   Ingest files, directories, URLs, or stdin
  status   Show ingestion status and pending items
  process  Process archived content that hasn't been fully engaged

Examples:
  %(prog)s ingest --file doc.md --mode deep
  %(prog)s ingest --input ./docs --mode shallow
  %(prog)s ingest --url https://example.com/article
  echo "Some text" | %(prog)s ingest --stdin --stdin-type text
  %(prog)s status --pending
  %(prog)s process --all-archived
        """,
    )

    env_defaults = _get_db_env_defaults()
    subparsers = parser.add_subparsers(dest="subcommand")

    # Ingest subcommand
    ingest_p = subparsers.add_parser("ingest", help="Ingest content into memory")
    input_group = ingest_p.add_mutually_exclusive_group()
    input_group.add_argument("--file", "-f", type=Path, help="Single file to ingest")
    input_group.add_argument("--input", "-i", type=Path, help="Directory to ingest")
    input_group.add_argument("--url", "-u", type=str, help="URL to fetch and ingest")
    input_group.add_argument("--stdin", action="store_true", help="Read content from stdin")

    ingest_p.add_argument("--stdin-type", choices=["text", "markdown", "code", "json", "yaml", "data"], default="text", help="Content type for stdin input")
    ingest_p.add_argument("--stdin-title", type=str, help="Title for stdin content")
    ingest_p.add_argument("--title", type=str, help="Override document title")

    ingest_p.add_argument("--mode", default="auto", choices=[m.value for m in IngestionMode], help="Ingestion mode")
    ingest_p.add_argument("--no-recursive", action="store_true", help="Don't recurse into subdirectories")
    ingest_p.add_argument("--min-importance", type=float, help="Minimum importance floor")
    ingest_p.add_argument("--permanent", action="store_true", help="Mark memories as permanent (no decay)")
    ingest_p.add_argument("--base-trust", type=float, help="Base trust level for source")

    _add_common_args(ingest_p, env_defaults)

    # Status subcommand
    status_p = subparsers.add_parser("status", help="Show ingestion status")
    status_p.add_argument("--pending", action="store_true", help="Show pending/archived ingestions")
    status_p.add_argument("--json", action="store_true", help="Output as JSON")
    _add_common_args(status_p, env_defaults)

    # Process subcommand
    process_p = subparsers.add_parser("process", help="Process archived content")
    process_p.add_argument("--content-hash", type=str, help="Content hash of specific archived item")
    process_p.add_argument("--all-archived", action="store_true", help="Process all archived items")
    process_p.add_argument("--limit", type=int, default=10, help="Max items to process")
    _add_common_args(process_p, env_defaults)

    args = parser.parse_args()

    # Default to ingest if no subcommand (for backwards compatibility)
    if args.subcommand is None:
        # Check if any ingest-style args were provided
        if hasattr(args, "file") or hasattr(args, "input"):
            parser.print_help()
            print("\nError: Please use 'ingest' subcommand. Example: python -m services.ingest ingest --file doc.md")
        else:
            parser.print_help()
        return

    if args.subcommand == "ingest":
        if not (args.file or args.input or args.url or args.stdin):
            print("Error: One of --file, --input, --url, or --stdin is required")
            return
        _cmd_ingest(args)
    elif args.subcommand == "status":
        _cmd_status(args)
    elif args.subcommand == "process":
        _cmd_process(args)


if __name__ == "__main__":
    main()
