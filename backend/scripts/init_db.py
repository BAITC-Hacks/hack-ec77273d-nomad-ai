"""Backend-directory entry point; implementation lives at repository root."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.init_db import main


if __name__ == '__main__':
    main()
