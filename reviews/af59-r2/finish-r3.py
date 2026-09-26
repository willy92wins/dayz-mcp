"""Write the final lane report only after all seven mutation logs exist and pass."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
baseline = json.loads((ROOT / 'baseline-r3.json').read_text())


def read(name):
    return (ROOT / name).read_bytes().decode('cp1252').replace('\r\n', '\n')


def write(path, data):
    path.write_bytes(data)
    assert path.stat().st_size == len(data) and path.read_bytes() == data, path


gate = read('gate-r3.log')
h1 = read('H1-r3.log')
mutations = read('mutants-r3.log')
assert 'PASS=16 FAIL=0' in gate and 'EXIT_CODE=0' in gate
assert 'PASS=6 FAIL=0' in h1 and 'EXIT_CODE=0' in h1
assert mutations.count('ISOLATED=true') == 7 and 'MUTATIONS_ISOLATED' in mutations
assert not list((ROOT / '_work').rglob('*')), 'fixture cleanup incomplete'

descriptions = {
    'dayz-test.ps1': 'Errores tipados del lector y conteo de .c desde toda la raiz; af59/H1 intactos.',
    'test-af59.ps1': 'S10-S16, fixture real fijada por hash, junction sin override y escrituras de fixtures verificadas.',
    'pbo-counter-notes.md': 'Contrato de errores, alcance real del contador, procedencia y limites.',
    'STATE.md': 'Informe final A-E con resultados y premisas discutidas; conserva R2 en backup separado.',
    'baseline-r3.json': 'Inventario SHA-256 y bytes de todos los ficheros existentes al empezar.',
    'dayz-test.ps1.BEFORE': 'Copia exacta del sujeto ANTES de modificarlo.',
    'test-af59.ps1.BEFORE': 'Copia exacta del arnes original de nueve escenarios.',
    'STATE-r2.BEFORE.md': 'Informe anterior preservado byte a byte.',
    'pbo-counter-notes-r2.BEFORE.md': 'Notas anteriores preservadas byte a byte.',
    'GATES.md': 'Criterios y lista cerrada de siete mutaciones escritos antes de implementar.',
    'stub-addonbuilder.cmd': 'Dependencia ausente reconstruida: registra argumentos, copia payload e imprime texto/exit solicitados.',
    'run-r3.py': 'Driver serial: gate, H1, siete mutaciones aisladas; valida IDs, causas y sujeto sin cambios.',
    'finish-r3.py': 'Generador reproducible del informe y DONE, condicionado a la evidencia completa.',
    'gate-r3.log': 'Salida literal del arnes final y exit code.',
    'H1-r3.log': 'Salida literal de H1 final y exit code.',
    'mutants-r3.log': 'Las siete corridas completas, causa/objetivo y aislamiento comprobado.',
    'gate-r3-initial.log': 'Primer ampliado: dos fallos de preparacion de fixtures; se conserva evidencia del fallo.',
    'test-af59-baseline-r3.log': 'Base restaurada: nueve escenarios, antes del cambio al sujeto.',
    'test-H1-baseline-r3.log': 'Base H1 restaurada: seis escenarios, antes del cambio al sujeto.',
    'scope-r3.log': 'Comparacion de cambios permitidos, invariantes y SHA-256 de backups.',
    'probe-r2-real-pbo.ps1': 'Extrae solo el contador BEFORE para probar el PBO real, sin codigo de launcher.',
    'probe-r2-real-pbo.log': 'El contador anterior tambien obtiene nueve scripts del PBO real.',
    'dayz-test.ps1.r3.diff': 'Diff del sujeto contra BEFORE, normalizado a LF.',
    'test-af59.ps1.r3.diff': 'Diff del arnes contra BEFORE, normalizado a LF.',
    'fixtures/DayZ_MCP_fence_DCC8730F.pbo': 'Copia byte-identica del PBO real archivado; solo se lee en los tests.',
    'fixtures/real-pbo-inventory.json': 'Inventario independiente de 13 entradas, nueve scripts, hash y SHA-1 del trailer.',
    'fixtures/README.md': 'Procedencia documental del PBO y limites de la muestra packonly.',
    'DONE': 'Marcador vacio de entrega; escrito despues del informe.',
}
for n in range(3, 10):
    descriptions[f'M{n}-r3.log'] = f'Salida literal de M{n}: cae solamente S{n + 7}.'
    descriptions[f'mutants-r3/M{n}.ps1'] = f'Copia del sujeto con una sola mutacion M{n}; no se despliega.'

rows = []
for p in sorted(ROOT.rglob('*')):
    if not p.is_file():
        continue
    rel = p.relative_to(ROOT).as_posix()
    previous = baseline.get(rel)
    if previous and hashlib.sha256(p.read_bytes()).hexdigest() == previous['sha256']:
        continue
    assert rel in descriptions, f'unexpected output: {rel}'
    rows.append((rel, previous['bytes'] if previous else 0, p.stat().st_size))
if not (ROOT / 'DONE').exists():
    rows.append(('DONE', 0, 0))

command = "& '.\\tools\\.venv-mcp\\Scripts\\python.exe' 'reviews/af59-r2/run-r3.py' --mutations"
section_b = f'''## B - Resultado de las pruebas

Entorno: Windows PowerShell 5.1.22621.6133; Python del venv del repositorio.
Solo dos arneses PS offline, ejecutados en serie. No se ejecuto la suite Python.
Comando del driver desde la raiz del repo:

```powershell
{command}
```

Salida final literal del driver:
```text
MUTATIONS_ISOLATED
R3_GATES_VERIFIED
```
Exit code del driver: 0. El driver imprime y guarda los comandos siguientes.

Gate final (comando y salida literales):
```text
{gate.strip()}
```

H1 final (comando y salida literales):
```text
{h1.strip()}
```

Mutaciones: comandos literales y lineas de resumen; las salidas completas estan
en mutants-r3.log y en cada M*-r3.log. Cada una recorre S1-S16; todas conservan
quince PASS y exactamente un FAIL. El driver exige tambien la causa prevista,
no acepta un fallo de setup como calibracion.
'''
for n in range(3, 10):
    log = read(f'M{n}-r3.log')
    lines = [line for line in log.splitlines()
             if line.startswith(('COMMAND:', 'PASS=', 'EXIT_CODE=', 'ISOLATED='))]
    section_b += '\n```text\n' + '\n'.join(lines) + '\n```\n'

section_b += '''
Linea base, antes de modificar el sujeto y despues de restaurar el stub ausente:
los mismos comandos absolutos de test-af59.ps1 y test-H1.ps1 citados arriba.
Sus salidas literales de resumen (logs *-baseline-r3.log):
```text
PASS=9 FAIL=0
EXIT_CODE=0
PASS=6 FAIL=0
EXIT_CODE=0
```

Primer intento del arnes ampliado, mismo comando del gate (gate-r3-initial.log):
```text
PASS=14 FAIL=2
EXIT_CODE=1
```
S8 y S16 no llegaron a iniciar el sujeto: la concatenacion de la linea del stub
sin parentesis convertia el argumento Encoding en String. Se agrupo la expresion
en los dos sitios y se repitio el gate completo; no se cambiaron expectativas.

Sondeo contra la premisa, solo funcion BEFORE y PBO real:
```text
''' + read('probe-r2-real-pbo.log').strip() + '\n```\n'

sections_ce = '''## C - Hallazgos y decisiones

- Cite-then-verify: las citas 342-343, 423 y 603-608 del sujeto inicial coinciden
  con el brief. Fuente preservada: dayz-test.ps1.BEFORE. Los cambios actuales
  estan en dayz-test.ps1:427 (politica), :437/:458/:496/:502 (corrupcion), :472/:485
  (no soportado) y :614-632 (alcance del conteo y tratamiento de excepciones).
- R1: se rechaza la corrupcion estructural demostrada con InvalidDataException.
  NotSupportedException y fallos no clasificados del lector producen un Warn
  INCONCLUSIVE, no un conteo cero ni un marcador de chequeo correcto. Se conserva
  el resultado del build con su advertencia; esto sigue la distincion solicitada
  entre un archivo roto y un lector que no sabe leerlo. S10/M3 y S11/M4 miden los
  dos sentidos. La cabecera de S10 es una sonda sintetica deliberada de formato
  no soportado, no una afirmacion de que AddonBuilder la produzca legitimamente.
- R2: $srcC enumera toda $src (:616), igual que el lado PBO recorre toda la tabla.
  S12/M5 prueba un script adicional fuera de scripts/; S13/M6 prueba la ausencia
  total de scripts/. M5 y M6 representan dos arreglos parciales plausibles.
- R3: el PBO real se encontro en reviews/2026-08-19-lane-fence, 205008 bytes,
  SHA-256 dcc8730feb98ff2a4f7c203075d799428c53a669360e3499311aa3b4d31afed3.
  Es copia exacta, con nueve scripts entre trece entradas, propiedades product y
  prefix y trailer SHA-1 comprobado independientemente. Procedencia:
  PROJECT-MAP.md:17, reviews/2026-08-19-lane-fence/MANIFEST.md:22,
  TANDA-INGAME.md:22-23 y tools/build_fence_pbo.ps1:15/:56 bajo esa carpeta.
  S14 exige conteo 9/9 del archivo completo (:179 y :323 del arnes), y M7 demuestra
  que avisar INCONCLUSIVE para ese PBO real no basta para pasar su escenario.
- R4: la implementacion del guard ya era correcta; no se cambio para este riesgo.
  S15 elimina la variable y exige rechazo antes del stub con bytes anteriores
  intactos. S16 crea previamente un junction local real, elimina la variable,
  exige exito y observa que el stub hijo tampoco la recibe; comprueba los bytes
  en el destino del mapping. M8/S15 y M9/S16 discriminan rechazo y aceptacion.
  La limpieza retira solo el junction comprobado antes del borrado recursivo.
- Dependencia ausente: no existia stub-addonbuilder.cmd ni se encontro otra copia
  en la busqueda local. Se reconstruyo segun el contrato y variables leidas en
  ambos arneses. El gate original de nueve escenarios y H1 se reprodujeron verdes
  antes de tocar el sujeto. No se afirma identidad con el stub original perdido.
- Correccion al brief: pbo-counter-notes.md describia siete fixtures de bytes
  arbitrarios, pero test-af59.ps1:21-39 original ya generaba tablas sinteticas PBO
  en nueve escenarios. Se reemplazaron esas notas; se conserva su copia original.
- Las nueve definiciones originales y sus comprobaciones se conservan; los
  cambios comunes son readback de fixtures y limpieza al ultimo caso. El sujeto
  conserva los bloques de af59, mtime/hash y destino H1; test-H1.ps1 no cambio ni
  un byte. Evidencia: scope-r3.log, diffs y hashes en baseline-r3.json.
- Cierre con skill post-session, acotado por el brief: memoria durable en este
  STATE y pbo-counter-notes.md. FUERA DE MI ALCANCE: HANDOFF.md global, vault,
  pipeline_feedback, CONTRACT.md historico y pbo-counter.txt historico. No se
  escribieron. El receptor puede promover la evidencia tras su revision.
- Sin git add, commit ni stash por prohibicion expresa; solo arbol de trabajo.
  No se tocaron tools/ ni las copias de mods. No hace falta resellado nativo:
  ningun fichero de PACKAGED_MODULES (tools/build_native_launcher.py:53-72) cambio.

## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO

El encargo mezcla dos defectos potenciales de implementacion con dos huecos de
medicion. El riesgo 2 es observable: al preferir scripts/ se reduce el umbral y
al exigir su existencia se omite por completo la comprobacion; M5 y M6 reproducen
ambas consecuencias con un build que imprime exito. En cambio, R3 y R4 no pedian
necesariamente cambiar codigo: el lector anterior YA devuelve nueve scripts del
PBO real y el guard anterior ya admite junction/rechaza carpeta normal. Sus cierres
son evidencia nueva y mutaciones, no arreglos de un fallo runtime demostrado.

No he encontrado un PBO legitimo de AddonBuilder rechazado por el lector anterior.
La consecuencia de R1 era una hipotesis de compatibilidad razonable por el catch
indiscriminado, no un incidente reproducido con un archivo valido. S10 demuestra
la politica solicitada con metadatos no soportados; S14 demuestra compatibilidad
con una muestra real distinta. No deben combinarse para afirmar que S10 es valida.

Un contador no es un validador general ni una comparacion de identidad: un script
equivocado puede sustituir a otro sin variar el numero. Tambien un proyecto con
.c auxiliares intencionalmente excluidos podria disparar un falso rechazo al
contar toda la raiz. Se adopto el alcance pedido (y el antiguo) sin inventar una
allowlist de exclusiones. El formato desconocido ahora puede continuar con un
aviso incluso si en realidad esta roto: es la consecuencia deliberada de no
confundir falta de conocimiento del lector con prueba de invalidez. No se afirma
que este chequeo baste para autorizar una publicacion de cualquier PBO.

El junction es una convencion del host, no una prueba de que el motor lea ese
destino. S16 acredita el mapping local y que no necesita el override; no acredita
un despliegue en el juego. Estos limites permanecen aunque todos los gates den verde.

## E - LO QUE NO PUDE VERIFICAR

- Build nuevo con AddonBuilder: prohibido explicitamente por ESTE brief; se uso
  el PBO real ya archivado con procedencia documental, no se genero uno nuevo.
- PBO real binarizado/comprimido y todas sus variantes: la muestra disponible
  seleccionada procede de -packonly y guarda sus entradas sin compresion. No se
  ejecuto AddonBuilder para ampliar esa cobertura por restriccion de ESTE brief.
- Ejecucion/carga del contenido en DayZ, mapping real P:, deploy de un mod,
  daemon vivo y session_status: prohibidos por ESTE brief. No se adquirio lease
  porque no hubo interaccion con la sesion compartida; solo procesos de fixtures.
- Suite Python completa: prohibida por ESTE brief debido a contencion entre lanes;
  no es necesaria para estos dos scripts de reviews y la correra el receptor.
- Revision independiente de otra familia: la hara Claude receptor; no se abrieron
  subagentes ni fan-out por prohibicion explicita de ESTE brief.
- Integridad semantica de scripts, checksums desde el contador de produccion,
  identidad de cada script, y variantes de formato desconocidas: este contador
  solo mide entradas y algunas cotas estructurales; no son propiedades verificadas
  por su salida. El SHA-1 de la fixture fue comprobado fuera del contador.
- No se verifico el universo de cambios ajenos de las otras lanes; scope-r3.log
  comprueba los ficheros iniciales de esta carpeta y el alcance de nuestras writes,
  no atribuye al resto del arbol un estado global verde.
'''

size = 0
for _ in range(10):
    section_a = '## A - Ficheros creados/modificados\n\n'
    section_a += 'Ronda 3 L2 completada: gate 16/16, H1 6/6, siete mutaciones aisladas.\n'
    section_a += '0 bytes antes significa fichero nuevo. Rutas absolutas; tamano en bytes.\n\n'
    section_a += '| Ruta absoluta | Antes -> despues | Cambio |\n|---|---:|---|\n'
    for rel, old, new in rows:
        if rel == 'STATE.md':
            new = size
        section_a += f'| {ROOT / rel} | {old} -> {new} | {descriptions[rel]} |\n'
    section_a += '\nSin modificar: test-H1.ps1, BRIEF.txt, CONTRACT.md, pbo-counter.txt y logs de R2.\n\n'
    report = (section_a + section_b + '\n' + sections_ce).encode('utf-8')
    if len(report) == size:
        break
    size = len(report)
else:
    raise AssertionError('self-size did not converge')
write(ROOT / 'STATE.md', report)
write(ROOT / 'DONE', b'')
print(f'STATE_WRITTEN bytes={len(report)} sha256={hashlib.sha256(report).hexdigest()}')
print('DONE bytes=0')
