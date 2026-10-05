import sys
from pathlib import Path

# Make `src` importable when running `pytest module-2-skill-gap/tests/` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
