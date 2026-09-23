"""Start the local FastAPI application from the repository root."""

import sys
import os
import secrets
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# Import app.main from backend/app. This path is inherited by Uvicorn's
# Windows reload subprocess, unlike relying on the current directory.
sys.path.insert(0, str(BACKEND))


if __name__ == "__main__":
    load_dotenv(ROOT / '.env', override=False)
    if not os.getenv('AUTH_SECRET'):
        secret = secrets.token_urlsafe(48)
        with (ROOT / '.env').open('a', encoding='utf-8') as env_file:
            env_file.write('\nAUTH_SECRET=' + secret + '\n')
        os.environ['AUTH_SECRET'] = secret
    print('Career Quest: http://127.0.0.1:8000')
    print('Local demo login codes: var/demo_accounts.json (generated at startup)')
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(BACKEND)],
    )
