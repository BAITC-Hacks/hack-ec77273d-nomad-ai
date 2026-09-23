"""Create the local state journal; never seed private or invented source data."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.repository import Repository
from backend.app.engine.data_loader import load_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.environ.get('DATABASE_PATH', 'var/nomad.sqlite3'))
    parser.add_argument('--data-dir', help='Validate and register a private starter-kit directory')
    args = parser.parse_args()
    path = Path(args.database)
    if not path.is_absolute():
        path = ROOT / path
    repository = Repository(path)
    if args.data_dir:
        data_path = Path(args.data_dir)
        if not data_path.is_absolute():
            project_candidate = ROOT / data_path
            backend_candidate = ROOT / 'backend' / data_path
            data_path = project_candidate if project_candidate.exists() else backend_candidate
        required = ('employees.json', 'events.json', 'skills.json', 'activity_history.csv')
        # `data/` is the documented default. In this checkout the organizer kit
        # may instead be present in backend/Dataset; select it when `data/` is empty.
        if not all((data_path / name).is_file() for name in required):
            fallback = ROOT / 'backend' / 'Dataset'
            if all((fallback / name).is_file() for name in required):
                data_path = fallback
            else:
                missing = [name for name in required if not (data_path / name).is_file()]
                raise SystemExit(
                    f'Dataset not found in {data_path}. Missing: {", ".join(missing)}. '
                    'Place the four starter-kit files there or use --data-dir backend/Dataset.'
                )
        dataset = load_dataset(data_path)
    else:
        dataset = None
    repository.initialize()
    if dataset:
        imported = repository.register_import(as_of_date=dataset.as_of_date.isoformat(),
                                              manifest=dataset.manifest, counts=dataset.counts)
        print(f"Registered import: {imported['import_id']}; snapshot: {dataset.as_of_date}")
        print(dataset.counts)
    print(f'Database ready: {path}')


if __name__ == '__main__':
    main()
