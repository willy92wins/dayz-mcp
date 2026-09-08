#Requires -Version 5.1
<#
.SYNOPSIS
Regenerate PROJECT-MAP.md on demand, relative to this script (not the cwd).
.DESCRIPTION
Uses the project's Python interpreter and standard library only. The embedded
source is ASCII so Windows PowerShell 5.1 cannot corrupt Spanish text through
its legacy code page. Python writes UTF-8 without BOM and verifies the bytes.
.PARAMETER PythonExecutable
Optional interpreter path; defaults to tools/.venv-mcp/Scripts/python.exe.
#>
param([string]$PythonExecutable = "")

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $PSScriptRoot ".venv-mcp\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "Python not found: $PythonExecutable. Pass -PythonExecutable with an installed Python 3 interpreter."
}

$program = @'
import datetime
import hashlib
import os
from pathlib import Path
import stat
import sys

repo = Path(sys.argv[1])
tools = repo / 'tools'
target = repo / 'PROJECT-MAP.md'
skipped = set()


def metadata(path):
    # lstat, including for scan roots: never dereference a junction to count it.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (getattr(info, 'st_file_attributes', 0) & 0x400
            or stat.S_ISLNK(info.st_mode)):
        skipped.add(path)
        return None
    return info


def files(directory, recursive=False):
    info = metadata(directory)
    if info is None or not stat.S_ISDIR(info.st_mode):
        return
    with os.scandir(directory) as entries:
        paths = sorted((Path(e.path) for e in entries),
                       key=lambda p: (p.name.casefold(), p.name))
    for path in paths:
        info = metadata(path)
        if info is None:
            continue
        if stat.S_ISREG(info.st_mode):
            yield path, info
        elif recursive and stat.S_ISDIR(info.st_mode):
            yield from files(path, recursive=True)


def size_text(size):
    # Historical map notation: KB means KiB. Integer nearest-KiB rounding.
    return f'{(size + 512) // 1024} KB' if size >= 1024 else f'{size} B'


def order(path):
    text = path.as_posix()
    return text.casefold(), text


def location(path):
    return f'`{path}`' + ('' if path.exists() else ' (ausente en este checkout)')


# Preserve the development map's sibling mod anchor; clones fall back to addon/.
mod = repo / 'addon'
if repo.name.endswith('_dev'):
    sibling = repo.with_name(repo.name[:-4])
    if sibling.is_dir():
        mod = sibling
scripts = [(p, s) for p, s in files(mod / 'scripts', recursive=True)
           if p.suffix.lower() == '.c']
scripts.sort(key=lambda item: order(item[0]))

# These are the project's script entry-point directories, not dependencies,
# virtualenv activation scripts, test fixtures, or archived build outputs.
entry_directories = ('_session_coordination', 'publish', 'spike0')
entry_files = list(files(tools))
for name in entry_directories:
    entry_files.extend(files(tools / name, recursive=True))
entries = sorted((p.relative_to(tools) for p, _ in entry_files
                  if p.suffix.lower() in ('.ps1', '.bat', '.cmd')), key=order)
if not entries:
    raise RuntimeError('No build/test script entry points found under tools/')

# The map cannot inventory its own size. HANDOFF is deliberately marker-only.
docs = [(p, s) for p, s in files(repo)
        if p.suffix.lower() == '.md'
        and p.name.casefold() not in ('project-map.md', 'handoff.md')]
docs.sort(key=lambda item: order(item[0]))

lines = [
    '# DayZ_MCP - location map', '',
    'Generated ' + datetime.datetime.now(datetime.timezone.utc).strftime(
        '%Y-%m-%d %H:%M:%S UTC') + ' by `tools\\gen-project-map.ps1`.',
    'Este mapa indica D\u00d3NDE est\u00e1n las cosas. El estado actual vive en `HANDOFF.md`.',
    'Regenerar tras mover o cambiar archivos: `powershell -NoProfile -ExecutionPolicy Bypass -File tools\\gen-project-map.ps1`.',
    'Tama\u00f1os tomados al generar; KB = KiB (1024 bytes), redondeados al entero m\u00e1s cercano.',
    '', '## Anchors', '', '| What | Path |', '|---|---|',
]
for label, path in (
    ('Dev / docs', repo), ('Mod source (censo Enforce)', mod),
    ('Tools', tools), ('Tests', tools / 'tests'), ('Plans', repo / 'plans'),
    ('Reviews', repo / 'reviews'), ('Decisions', repo / 'decisions'),
    ('Server logs (RPT + script.log)', repo / '_server' / 'profiles'),
    ('Client logs (RPT + script.log)', repo / '_client' / 'profiles'),
):
    lines.append(f'| {label} | {location(path)} |')
lines.extend([
    '| Destino PBO | `tools/pack-addon.ps1`: par\u00e1metro `-Destination`; evidencias de builds en `reviews/` |',
    '', '## HANDOFF: cu\u00e1nto leer', '',
    'En `HANDOFF.md`, leer el bloque entre `<!-- LIVE-STATE:START -->` y `<!-- LIVE-STATE:END -->`.',
    'Detener la lectura inicial en `LIVE-STATE:END`; buscar despu\u00e9s solo las secciones necesarias.',
    'No fijar n\u00fameros de l\u00ednea, l\u00edmites de lectura ni tama\u00f1os: el bloque cambia en cada cierre.',
    'El hist\u00f3rico separado, si existe, est\u00e1 en `HANDOFF-ARCHIVE.md`.',
    '', f'## Enforce scripts ({len(scripts)} files, {size_text(sum(s.st_size for _, s in scripts))})',
    '', f'Rutas relativas a la fuente del mod: {location(mod)}.',
    'Censo de `scripts/**/*.c`; no se atraviesan ni se cuentan reparse points (incluidos junctions).',
])
previous_group = None
for path, info in scripts:
    rel = path.relative_to(mod)
    group = rel.parent.relative_to('scripts').as_posix()
    if group != previous_group:
        lines.extend(['', f'**{group}/**', ''])
        previous_group = group
    lines.append(f'- `{rel.as_posix()}` - {size_text(info.st_size)}')
config = mod / 'config.cpp'
config_info = metadata(config)
if config_info is not None and stat.S_ISREG(config_info.st_mode):
    lines.extend(['', f'- **config.cpp** -> `{config}` ({size_text(config_info.st_size)})'])
lines.extend([
    '', '## Build / test entry points', '',
    'Rutas relativas a `tools/`. Scripts `.ps1`, `.bat` y `.cmd` en su ra\u00edz y en',
    '`_session_coordination/`, `publish/` y `spike0/` (recursivos, sin reparse points).',
    'Se excluyen dependencias, entornos virtuales, fixtures y archivos de trabajo de otras carpetas.',
    'Los m\u00f3dulos unittest est\u00e1n en `tools/tests/`; el gate de este mapa es `tests.test_docs_truth`.', '',
])
lines.extend(f'- `{path.as_posix()}`' for path in entries)
lines.extend([
    '', '## Docs in this project', '',
    'Documentos `.md` de la ra\u00edz; el mapa se excluye del inventario para evitar autorreferencia.',
    '- `HANDOFF.md` - estado vivo; leer hasta `LIVE-STATE:END`.',
])
for path, info in docs:
    touched = datetime.datetime.fromtimestamp(
        info.st_mtime, datetime.timezone.utc).strftime('%Y-%m-%d')
    lines.append(f'- `{path.name}` - {size_text(info.st_size)}, touched {touched} (UTC)')
lines.extend(['', '## Reparse points omitidos', '',
              'Se comprueba `FILE_ATTRIBUTE_REPARSE_POINT` (`0x400`) antes de descender o contar.'])
if skipped:
    lines.extend(f'- `{path}`' for path in sorted(skipped, key=order))
else:
    lines.append('Ninguno encontrado dentro de los directorios censados.')

# One complete binary write, with an immediate byte/size check on the host.
# Do not read a previous map: deleting it must not affect generation.
data = ('\n'.join(lines) + '\n').encode('utf-8')
target.write_bytes(data)
if target.read_bytes() != data or target.stat().st_size != len(data):
    raise RuntimeError(f'PROJECT-MAP write verification failed: {target}')
print(f'Generated and verified {target}: {len(data)} bytes; SHA256 {hashlib.sha256(data).hexdigest()}')
'@

# ASCII source over stdin avoids native argument quoting and code-page losses.
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $program | & $PythonExecutable -B -X utf8 - $repoRoot
    $generatorExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousPreference
}
if ($generatorExitCode -ne 0) {
    throw "Project map generation failed (Python exit code $generatorExitCode)."
}
