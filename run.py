"""One-command local setup and launch: python run.py."""
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent

def main():
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    check = subprocess.run([str(python), '-c', 'import fastapi,pydantic,openai,uvicorn'], capture_output=True)
    if check.returncode:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    print('Open http://127.0.0.1:8000/employee.html', flush=True)
    try:
        return subprocess.call([str(python), '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'], cwd=ROOT)
    except KeyboardInterrupt:
        return 0

if __name__ == '__main__':
    raise SystemExit(main())
