"""Serialize validated raw snapshots; uploaded ZIP files are read without extraction."""
import csv
import io
import json
import zipfile

from .engine.data_loader import FILES

MAX_FILE_BYTES = 12 * 1024 * 1024
HISTORY_COLUMNS = ('record_id', 'employee_id', 'event_id', 'date', 'due_date', 'status',
                   'completion_pct', 'score', 'feedback_rating', 'assigned_by')


def dataset_files(dataset):
    meta = dict(dataset.meta, as_of_date=dataset.as_of_date.isoformat())
    wrappers = {
        'employees.json': {'meta': meta, 'employees': list(dataset.employees_by_id.values())},
        'events.json': {'meta': meta, 'events': list(dataset.events_by_id.values())},
        'skills.json': {'meta': meta, 'skills': list(dataset.skills_by_id.values()),
                        'proficiency_scale': dataset.proficiency_scale,
                        'role_profiles': list(dataset.role_profiles_by_key.values())},
    }
    raw = {name: json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
           for name, value in wrappers.items()}
    csv_buffer = io.StringIO(newline='')
    writer = csv.DictWriter(csv_buffer, fieldnames=HISTORY_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for employee_id in sorted(dataset.history_by_employee):
        writer.writerows(dataset.history_by_employee[employee_id])
    raw['activity_history.csv'] = csv_buffer.getvalue().encode('utf-8')
    return raw


def read_zip(blob):
    raw = {}
    if not zipfile.is_zipfile(io.BytesIO(blob)):
        raise ValueError('Ожидается корректный ZIP-архив')
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if len(archive.infolist()) > 100:
            raise ValueError('Слишком много файлов в ZIP')
        for item in archive.infolist():
            name = item.filename.replace('\\', '/').split('/')[-1]
            if name not in FILES:
                continue
            if name in raw or item.file_size > MAX_FILE_BYTES:
                raise ValueError('Повторное имя файла или превышен размер ZIP')
            with archive.open(item) as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError('Файл слишком большой')
            raw[name] = content
    return raw
