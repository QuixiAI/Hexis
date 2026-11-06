"""A .zip of notes or exported mail is read entry by entry."""
from __future__ import annotations

import zipfile

from services.ingest.pipeline import IngestionPipeline
from services.ingest.readers import ZipArchiveReader, get_reader

EML = (
    b"From: Mia <mia@example.com>\r\nTo: me@example.com\r\nSubject: Lisbon dates\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\nWe land on the 12th.\r\n"
)


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_reads_text_entries_and_mail_and_skips_the_rest(tmp_path):
    archive = _zip(tmp_path / "notes.zip", {
        "trip/plan.md": "# Plan\nPack light.",
        "trip/budget.csv": "item,cost\nflight,400",
        "mail/dates.eml": EML,
        "photo.jpg": b"\xff\xd8\xff",
        "__MACOSX/trip/._plan.md": b"junk",
        ".DS_Store": b"junk",
    })
    assert isinstance(get_reader(archive), ZipArchiveReader)
    assert ".zip" in IngestionPipeline.SUPPORTED_EXTENSIONS
    text = ZipArchiveReader.read(archive)
    assert "--- Entry: trip/plan.md ---\n# Plan\nPack light." in text
    assert "flight,400" in text
    assert "Lisbon dates" in text and "We land on the 12th." in text
    assert "Skipped 1 entries" in text
    assert "junk" not in text


def test_stops_at_the_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(ZipArchiveReader, "MAX_TOTAL_BYTES", 10)
    archive = _zip(tmp_path / "big.zip", {"a.txt": "x" * 8, "b.txt": "y" * 8})
    text = ZipArchiveReader.read(archive)
    assert "x" * 8 in text and "y" * 8 not in text
    assert "50 MB" in text


def test_an_archive_with_nothing_readable_says_so(tmp_path):
    text = ZipArchiveReader.read(_zip(tmp_path / "pics.zip", {"a.png": b"\x89PNG"}))
    assert "[No readable entries found]" in text
