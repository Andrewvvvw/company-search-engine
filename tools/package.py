#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
DIST.mkdir(exist_ok=True)
SKILL = ROOT / 'skills/company-search-agent'


def allowed(path):
    return path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix != '.pyc'


def write_zip(path, files):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for source, target in sorted(files, key=lambda x: x[1]):
            info = zipfile.ZipInfo(target, date_time=(2026, 11, 21, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert all(not name.startswith('/') and '..' not in Path(name).parts for name in archive.namelist())


skill_zip = DIST / 'company-search-agent.zip'
skill_files = [(p, 'company-search-agent/' + p.relative_to(SKILL).as_posix()) for p in SKILL.rglob('*') if allowed(p)]
assert any(name == 'company-search-agent/SKILL.md' for _, name in skill_files)
write_zip(skill_zip, skill_files)
files = [(ROOT / 'README.md', 'README.md'), (ROOT / '.gitignore', '.gitignore')]
for directory in ('skills', 'docs', 'examples', 'tests', 'tools'):
    files += [(p, p.relative_to(ROOT).as_posix()) for p in (ROOT / directory).rglob('*') if allowed(p)]
for name in ('CSA-DEMO-50-20261121.xlsx', 'CSA-DEMO-50-20261121.json', 'CSA-ALMATY-FIELD-20261121.json',
             'demo-data.json', 'demo-validation.json', 'independent-workbook-read.json', 'unit-tests.log', 'compatibility.json', 'page-readback.json'):
    p = ROOT / 'outputs/search-agent-01' / name
    if not p.exists():
        raise SystemExit('Missing deliverable: ' + name)
    files.append((p, p.relative_to(ROOT).as_posix()))
files.append((skill_zip, 'dist/company-search-agent.zip'))
manifest = {name: hashlib.sha256(p.read_bytes()).hexdigest() for p, name in sorted(files, key=lambda x: x[1])}
manifest_path = DIST / 'MANIFEST.json'
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
files.append((manifest_path, 'dist/MANIFEST.json'))
delivery = DIST / 'search-agent-delivery.zip'
write_zip(delivery, files)
(DIST / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in (skill_zip, delivery)))
print(json.dumps({'skill': str(skill_zip), 'skill_files': len(skill_files), 'delivery': str(delivery), 'delivery_files': len(files)}, ensure_ascii=False))
