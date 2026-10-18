#!/usr/bin/env python3
"""Generate db/40_seed_prompt_modules.sql from services/prompts/*.md.

The DB owns prompt text (prompt_modules), so render_prompt()/build_llm_request()
can assemble LLM requests without Python loading markdown from disk. This baker
keeps the .md files as the authoring source and regenerates the SQL seed that is
applied at DB init. Run after editing any prompt file:

    python scripts/gen_prompt_seed.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.prompt_resources import parse_personhood_modules  # noqa: E402

PROMPTS = ROOT / "services" / "prompts"
OUT = ROOT / "db" / "40_seed_prompt_modules.sql"
TAG = "$pm$"

HEADER = """-- Seed prompt_modules from services/prompts/*.md (generated).
-- Regenerate with scripts/gen_prompt_seed.py after editing prompt files.
-- Makes render_prompt()/build_llm_request() live: the DB owns the prompt text
-- that Python previously loaded from disk via services/prompt_resources.py.
SET search_path = public, ag_catalog, "$user";
"""


def render() -> tuple[str, int]:
    """The seed file's text and how many modules it holds."""
    blocks = [HEADER]
    files = sorted(PROMPTS.glob("*.md"))

    def emit(key: str, content: str, source: str) -> None:
        if TAG in content:
            raise SystemExit(f"dollar-quote tag {TAG} collides with content of {source}")
        blocks.append(
            f"SELECT upsert_prompt_module(\n"
            f"    '{key}',\n"
            f"    {TAG}{content}{TAG},\n"
            f"    'Seeded from {source}',\n"
            f"    '{source}'\n"
            f");"
        )

    n_modules = 0
    for path in files:
        content = path.read_text(encoding="utf-8")
        emit(path.stem, content, f"services/prompts/{path.name}")
        n_modules += 1

    # Split personhood.md into per-module sub-prompts (personhood.<slug>) so the
    # DB compose_personhood(kind) can select subsets like compose_personhood_prompt.
    personhood = PROMPTS / "personhood.md"
    if personhood.exists():
        modules = parse_personhood_modules(personhood.read_text(encoding="utf-8"))
        for key, block in sorted(modules.items()):
            if key.startswith("module_"):  # skip numeric aliases; keep slug keys
                continue
            emit(f"personhood.{key}", block, "services/prompts/personhood.md")
            n_modules += 1

    return "\n\n".join(blocks) + "\n", n_modules


def main() -> None:
    text, n_modules = render()
    OUT.write_text(text, encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {n_modules} modules, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
