"""The DB serves prompts from db/40_seed_prompt_modules.sql, compiled from
services/prompts/*.md. A prompt edited without regenerating the seed never
reaches a fresh database (the heartbeat's fade-review guidance went missing
that way).
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_prompt_seed_matches_prompt_files():
    spec = importlib.util.spec_from_file_location("gen_prompt_seed", ROOT / "scripts" / "gen_prompt_seed.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    expected, _ = gen.render()
    assert gen.OUT.read_text(encoding="utf-8") == expected, (
        "db/40_seed_prompt_modules.sql is out of date: run python scripts/gen_prompt_seed.py"
    )
