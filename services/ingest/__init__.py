"""Hexis Universal Ingestion Pipeline (package form, #89).

The public surface of the former single-module services/ingest.py, preserved
verbatim: every existing `from services.ingest import X` keeps working, and
`python -m services.ingest` still runs the CLI.
"""

from __future__ import annotations

from .config import (
    Appraisal,
    Config,
    DocumentInfo,
    Extraction,
    IngestionMetrics,
    IngestionMode,
    Section,
    _emit,
    _extract_title,
    _hash_text,
    _infer_source_type,
    _normalize_mode,
    _should_cancel,
    _word_count,
)
from .readers import (
    AudioReader,
    CodeReader,
    DataReader,
    DocumentReader,
    DocxReader,
    EmailReader,
    EpubReader,
    ImageReader,
    LatexReader,
    MarkdownReader,
    NotebookReader,
    ZipArchiveReader,
    PDFReader,
    PptxReader,
    RssReader,
    RtfReader,
    TextReader,
    VideoReader,
    WebReader,
    XlsxReader,
    YouTubeTranscriptReader,
    get_reader,
)
from .sectioning import Sectioner
from .llm import Appraiser, IngestLLM, KnowledgeExtractor
from .store import MemoryStore
from .pipeline import ArchivedContentProcessor, IngestionPipeline
from .cli import main

__all__ = [
    "Appraisal", "Config", "DocumentInfo", "Extraction", "IngestionMetrics",
    "IngestionMode", "Section", "Sectioner", "Appraiser", "IngestLLM",
    "KnowledgeExtractor", "MemoryStore", "ArchivedContentProcessor",
    "IngestionPipeline", "DocumentReader", "get_reader", "main",
]
