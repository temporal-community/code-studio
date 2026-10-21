"""Host-side design references. Workflows receive only the frozen string files."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'docs' / 'temporal-design'
PREFIX = '/skills/temporal-design/'
POLICY_PATH = PREFIX + 'SKILL.md'


def reference_files():
    return {POLICY_PATH: (ROOT / 'SKILL.md').read_text(),
            PREFIX + 'foundations.md': (ROOT / 'foundations.md').read_text()}
