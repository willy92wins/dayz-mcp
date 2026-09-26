"""Static census: parse sources without importing or executing the test suite."""
from pathlib import Path
import ast
from collections import Counter
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
patterns = {
    'cwd_calls': r'\b(?:os\.getcwd|Path\.cwd|os\.chdir)\s*\(',
    'sys_path': r'\bsys\.path\b',
    'file_roots': r'__file__.*(?:parents?\b|dirname)|(?:parents?\b|dirname).*__file__',
    'dynamic_imports': r'\b(?:import_module|__import__|spec_from_file_location)\s*\(',
    'literal_relative_paths': r'\b(?:Path|open)\(\s*[\x27\x22](?![A-Za-z]:|/|\\)',
}
data = {name: [] for name in patterns}
data.update(files=[], imports=[], parse_errors=[])
for path in sorted((ROOT / 'tools/tests').rglob('*.py')):
    raw = path.read_bytes()
    source = raw.decode('utf-8-sig')
    rel = path.relative_to(ROOT).as_posix()
    data['files'].append({'path': rel, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        data['parse_errors'].append({'path': rel, 'error': str(exc)})
        continue
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else [('.' * node.level) + (node.module or '')]
            for name in names:
                data['imports'].append({'path': rel, 'line': node.lineno, 'name': name, 'text': ast.get_source_segment(source, node)})
    for line, text in enumerate(source.splitlines(), 1):
        for category, pattern in patterns.items():
            if re.search(pattern, text):
                data[category].append({'path': rel, 'line': line, 'text': text.strip()})
raw = (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
path = OUT / 'census-data.json'
path.write_bytes(raw)
assert path.read_bytes() == raw and path.stat().st_size == len(raw)
print('Python files:', len(data['files']))
print('Top-level test_*.py:', sum(Path(f['path']).name.startswith('test_') and len(Path(f['path']).parts) == 3 for f in data['files']))
print('Parse errors:', len(data['parse_errors']))
print('Import statements/entries:', len(data['imports']))
print('tools absolute imports:', [i for i in data['imports'] if i['name'].split('.')[0] == 'tools'])
print('Local absolute import roots:', dict(sorted(Counter(i['name'].split('.')[0] for i in data['imports'] if (ROOT / 'tools' / (i['name'].split('.')[0]+'.py')).exists() or (ROOT / 'tools' / i['name'].split('.')[0]).is_dir()).items())))
for category in patterns:
    print(category, 'lines:', len(data[category]), 'files:', len({i['path'] for i in data[category]}))
    if category in ['cwd_calls', 'literal_relative_paths']: print(data[category])
assert not data['parse_errors']
