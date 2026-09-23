"""Start the local FastAPI application from the repository root."""

import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# Import app.main from backend/app. This path is inherited by Uvicorn's
# Windows reload subprocess, unlike relying on the current directory.
sys.path.insert(0, str(BACKEND))


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(BACKEND)],
    )
