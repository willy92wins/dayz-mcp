"""Consolidate already recorded evidence; do not run more tests or touch the index."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess


OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
BRIDGE = ROOT / "addon/scripts/5_Mission/MCPClientBridge.c"
TEST = ROOT / "tools/tests/test_camera_native_crash.py"
PYTHON = ROOT / "tools/.venv-mcp/Scripts/python.exe"


def save(path, text):
    data = text.encode("utf-8") if isinstance(text, str) else text
    path.write_bytes(data)
    assert path.read_bytes() == data and path.stat().st_size == len(data), path
    return len(data)


before = (OUT / "MCPClientBridge.c.BEFORE").read_bytes()
after = BRIDGE.read_bytes()
assert hashlib.sha256(before).hexdigest() == "e41bb9ea05cd214da44639ccc7ffb1e7186ae13c113b12994971004f63f7c47b"
assert hashlib.sha256(after).hexdigest() == "9ce3ec0bb1430dea4f91e15e5efa4fffb0d77d95570da259b8463ec4c5017a19"
assert hashlib.sha256(TEST.read_bytes()).hexdigest() == "99ef8d47a695b1fbad273745a6eac7fdcac935663eceb7ce93d33e5a431eb115"

# Compare all bytes outside the two camera methods and the new helper/comment.
proc = b"\tprotected bool ProcessCameraSetJob(MCPJob job)"
following = b"\tprotected bool ProcessDriveProbeClientJob(MCPJob job)"
build = b"\tprotected MCPCamera BuildCameraResult(string mode)"
tail = b"\tprotected bool ArrayToVector("
helper = b"\t// GetCurrentCamera crashes inside the native getter before it can return"
assert before[:before.index(proc)] == after[:after.index(proc)]
assert before[before.index(following):before.index(build)] == after[after.index(following):after.index(helper)]
assert before[before.index(tail):] == after[after.index(tail):]

green = json.loads((OUT / "green-results.json").read_text())
red = json.loads((OUT / "red-results.json").read_text())
validators = json.loads((OUT / "validator-results.json").read_text())
assert all(row["exit_code"] == 0 for row in green)
assert red[0]["exit_code"] == 1
expected_modules = {"tests." + path.stem for path in (ROOT / "tools/tests").glob("*.py")
                    if "MCPClientBridge" in path.read_text(encoding="utf-8-sig")}
assert {row["module"] for row in green} == expected_modules
count = sum(int(re.search(r"Ran (\d+) tests", "\n".join(row["summary"]))[1]) for row in green)
assert count == 327 and len(green) == 21
for dump in json.loads((OUT / "minidumps.json").read_text()):
    data = Path(dump["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == dump["sha256"]
    assert dump["parameters"] == ["0x0", "0x68"] and dump["rax"] == "0x0"

checks = "PASS: bytes outside camera methods/helper unchanged (guards, ownership, cleanup preserved).\n"
checks += "PASS: source and test SHA-256 match the tested revision.\n"
checks += "PASS: all 21 source-related modules named individually; 327 tests, every exit code 0.\n"
checks += "PASS: both original minidumps reread with unchanged SHA-256.\n"
checks += "PASS: test-tmp is empty; tests created no retained temporary artifacts.\n"
assert not list((OUT / "test-tmp").rglob("*"))
for argv in (["git", "diff", "--check", "--", "addon/scripts/5_Mission/MCPClientBridge.c"],
             ["git", "diff", "--cached", "--name-status"]):
    result = subprocess.run(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    checks += "\nCOMMAND: " + subprocess.list2cmdline(argv) + "\n"
    checks += result.stdout.decode("utf-8", errors="replace")
    checks += f"EXIT CODE: {result.returncode}\n"
    assert result.returncode == 0
save(OUT / "verification.log", checks)

descriptions = {
    "MCPClientBridge.c.BEFORE": "Copia exacta previa a esta lane; 94.919 bytes, verificada por hash.",
    "DIAGNOSIS.md": "Causa nativa, citas reabiertas, hipótesis refutada, cambio y límites.",
    "red.log": "Salida literal del módulo nuevo contra la copia previa: 9 fallos esperados.",
    "green.log": "Salidas literales de los 21 módulos, ejecutados en serie.",
    "STATE.md": "Este informe A-E, con comandos, tamaños y pendientes concretos.",
    "DONE": "Marcador vacío de entrega escrita y verificada.",
    "red-results.json": "Índice de comando, exit code y resumen literal del control rojo.",
    "green-results.json": "Índice de comandos, exit codes y resúmenes literales de los módulos verdes.",
    "minidumps.json": "Extracción acotada de excepciones, registros, módulo, bytes y hashes originales.",
    "inspect_minidumps.py": "Parser reproducible offline; no adjunta un debugger ni modifica evidencia.",
    "run_checks.py": "Runner serial con módulos explícitos, temporales confinados y relectura por tramo.",
    "apply_camera_fix.py": "Edición de una pasada; rechaza cambios concurrentes y verifica bytes.",
    "run_validator.py": "Captura offline de baseline, bridge final y addon completo.",
    "validator-before/MCPClientBridge.c": "Copia .c del baseline para que el validador acepte la extensión.",
    "validator-before.json": "Salida literal del validador sobre el baseline.",
    "validator-after.json": "Salida literal del validador sobre el bridge corregido.",
    "validator-addon.json": "Salida literal del validador sobre el addon completo, incluidos hallazgos ajenos.",
    "validator-results.json": "Comandos y exit codes del validador; el JSON completo contiene info.files_scanned.",
    "validator.log": "Comandos, salidas completas y exit codes de las tres validaciones.",
    "verification.log": "Auditoría final de alcance, hashes, cobertura, dumps e índice solo leído.",
    "finalize_report.py": "Consolida evidencia por una pasada, calcula tamaño propio de STATE y escribe DONE al final.",
}
paths = [BRIDGE, TEST] + sorted({p for p in OUT.rglob("*") if p.is_file()} | {OUT / "DONE"})
assert all(str(p.relative_to(OUT)).replace("\\", "/") in descriptions for p in paths[2:])

def build_report(state_size):
    lines = ["## A - Ficheros creados/modificados", "",
             "Entrega: llamada nativa identificada y eliminada del bridge. Corrección en árbol de trabajo; ejecución Enforce todavía INCONCLUSA.", "",
             "Tamaños respecto al inicio de esta lane; 0 antes indica archivo inexistente. Las carpetas de salida y test-tmp eran nuevas; test-tmp ha quedado vacía.", "",
             "| Ruta absoluta | Bytes antes -> después | Cambio |", "|---|---:|---|"]
    for path in paths:
        old_size = 94919 if path == BRIDGE else 0
        if path == OUT / "STATE.md":
            size = state_size
        elif path == OUT / "DONE":
            size = 0
        else:
            size = path.stat().st_size
        if path == BRIDGE:
            description = "Elimina GetCurrentCamera; disponibilidad común, lectura de m_ActiveCam y rechazo explícito también en SETTLE."
        elif path == TEST:
            description = "12 tests nuevos: contrato de fuente y consumidor real de restore con transporte simulado."
        else:
            description = descriptions[path.relative_to(OUT).as_posix()]
        lines.append(f"| `{path}` | {old_size} -> {size} | {description} |")
    lines += ["", "No se hizo git add, commit, stash, despliegue, resellado ni gestión de procesos. El índice sigue mostrando únicamente decisions/decision-log.md; no se ha escrito. Los cambios ajenos iniciales en native-launchers se dejaron intactos.",
              "", "## B - Resultado de las pruebas", "",
              "Comandos literales de los procesos de test. Los wrappers run_checks.py red/green los ejecutaron uno por uno; no hubo discover ni suite completa. Salida íntegra en red.log y green.log.", "",
              "Entorno común de cada módulo:", "```text", f"cwd={ROOT / 'tools'}", f"PYTHONPATH={ROOT}",
              "PYTHONDONTWRITEBYTECODE=1", "PYTHONUTF8=1", f"TEMP={OUT / 'test-tmp'}", f"TMP={OUT / 'test-tmp'}", "```", "",
              "Control rojo: MCP_CAMERA_SOURCE apunta a .BEFORE; no se revirtió el archivo compartido.",
              "```text", f"MCP_CAMERA_SOURCE={OUT / 'MCPClientBridge.c.BEFORE'}"]
    for row in red:
        lines.append(row["command"])
        lines.extend(line for line in row["summary"] if line.startswith(("Ran ", "FAILED", "OK")))
        lines.append(f"EXIT CODE: {row['exit_code']}")
    lines += ["```", "", "Las 9 trazas completas del rojo están en red.log; una de ellas prohíbe expresamente el getter que figura en las dos pilas. Las otras incluyen guards ausentes y el fallback que declaraba cámara del jugador.", "",
              "Fuente final: MCP_CAMERA_SOURCE eliminado del entorno. Los resúmenes siguientes son literales, sin normalizar duraciones."]
    for row in green:
        lines += ["", "```text", row["command"], *row["summary"], f"EXIT CODE: {row['exit_code']}", "```"]
    lines += ["", f"Total aritmético: {len(green)} módulos, {count} tests, todos exit 0. Se comprobó que la lista coincide con TODOS los módulos que nombran MCPClientBridge en tools/tests/*.py.", "",
              "Validador estructural: comandos literales y fragmento literal del veredicto JSON; salida completa en validator.log y validator-*.json. Cwd = raíz del repo, con bytecode desactivado. No es un compilador Enforce."]
    for row in validators:
        data = json.loads((OUT / f"validator-{row['label']}.json").read_text())
        status_line = next(line for line in (OUT / f"validator-{row['label']}.json").read_text().splitlines() if '"status":' in line)
        lines += ["", "```text", row["command"], status_line, f"EXIT CODE: {row['exit_code']}", "```",
                  f"{data['info']['files_scanned']} fichero(s) leído(s); {len(data['errors'])} errores, {len(data['warnings'])} avisos."]
    lines += ["", "El bridge conserva los mismos 2 WARN ES-GETTYPE-EXACT-MATCH (2026 y 2739 -> 2026 y 2746). El addon completo da FAIL por dos rutas de hot layout de MCPDialogController.c:39-40, más 5 WARN GetType. Son hallazgos fuera del diff de cámara; no se declara PASS global.",
              "", "Comprobación final de whitespace (salida literal vacía):", "```text",
              "git diff --check -- addon/scripts/5_Mission/MCPClientBridge.c", "EXIT CODE: 0", "```", "",
              "Auditoría adicional: verification.log compara byte por byte todo lo que queda fuera de las dos funciones de cámara y el helper nuevo, verifica el hash de la revisión probada y relee ambos minidumps sin cambios.",
              "", "## C - Hallazgos y decisiones", "",
              "- Causa identificada: Camera.GetCurrentCamera() falla dentro del nativo, antes de devolver al script. Ambos RPT completos contienen BuildCameraResult:3664 y DispatchCameraGet:902; en reviews/guards-2026-09-08/MCPClientBridge.c.BEFORE:3664 está el getter. Los dos dumps dan RAX=0, lectura de 0x68 en DayZDiag_x64.exe+0x4f7910 (mov rcx,[rax+0x68]). La hipótesis de GetTransform sobre un puntero devuelto obsoleto no corresponde a estas pilas.",
              "- Corrección al brief: la primera ocurrencia también pasó por DispatchCameraGet. Ambas incluyen un frame más profundo que el fragmento transcrito. El RPT registra ACCESS_VIOLATION nativa con ENGINE Crashed y minidump; no es solo una excepción VM recuperable. Las citas del código actual y los 93.901 bytes del snapshot desplegado sí coincidieron.",
              "- IsClientInGame solo comprueba GetGame y GetPlayer (bridge:676-678). Un jugador sentado satisface ese predicado. Vanilla camera.c:3-7 documenta null para cámara del jugador, pero el getter murió antes de retornar; añadir if (!current) después no evita nada.",
              "- Se usa m_ActiveCam ya declarado, más IsActive documentado y utilizado en vanilla con comprobación previa de referencia. El estado con comando de vehículo se rechaza; también cualquier padre, por separado, sin atribuirle un tipo no observado. Esto cubre el desacuerdo command/parent sin intervenir vehicle_get_in_client. La eliminación del getter es general y no depende de acertar si había un asiento.",
              "- Error explícito y arrays vacíos para client_not_in_game, camera_unavailable_player, camera_unavailable_vehicle, camera_unavailable_parented_player, camera_unavailable_no_scripted_camera y camera_unavailable_inactive. Los valores escalares quedan por defecto en un bloque ok=false; no son mediciones válidas. Ambos llamantes mantienen el sobre protocolario existente result.ok=true y el fallo anidado camera.ok=false.",
              "- El helper también evita la consulta global de interpolación durante SETTLE en estados rechazados, y deja que REPORT produzca el mismo bloque camera.ok=false. Esa llamada adicional no se atribuye como causa medida de los dos crashes. No se añadieron logs de tick ni nuevos miembros/mensajes.",
              "- Degradación deliberada: restore_gameplay ejecuta cleanup, pero después de ReleaseCamera no hay referencia MCP verificable. El consumidor Python real devuelve restore_unverified también en pie. Reintentar no recuperará una observación ausente; no se ha falseado player_camera_active para hacerlo verde. También deja de observarse una cámara perteneciente a otro mod o la vista del jugador mediante camera_get.",
              "- camera_set conserva sus efectos anteriores a REPORT (puede suprimir controles antes de informar cámara no verificable). Este parche no reconstruye ni toma el control de la cámara del vehículo. ReleaseCamera/RestoreGameplay conservan todos sus bytes, igual que guards y ownership de hoy; no hubo conflicto con da3b75f/0818ebd.",
              "- Validador global: ES-LAYOUT-PATH-PBOPREFIX-MISMATCH y ES-LAYOUT-FILE-MISSING en MCPDialogController.c:39-40. Son rutas de sondeo opcional, comprobadas con FileExist antes de CreateWidgets en :169-173. Corregir el validador o esa UI está FUERA DE MI ALCANCE; no se tocó el archivo ni se atribuyó el FAIL a cámara.",
              "- Corrección propia durante cite-then-verify: GetTransform admite arrays de 1 a 4 vectores (enentity.c:274-288); se usa 4 para matriz completa. El primer tramo del diagnóstico decía que siempre exigía 4 y quedó corregido. Una comprobación auxiliar python -c perdió comillas por el transporte de PowerShell; se repitió por stdin, verificando tamaño, contenido y hash. No hubo truncamiento observado en esta lane.",
              "- No hace falta resellar el launcher: no se tocó ningún PACKAGED_MODULES de tools/build_native_launcher.py:53-72. La corrección está solo en fuente; no se ha sincronizado al árbol desplegado ni empaquetado PBO.",
              "- Memoria durable: DIAGNOSIS.md y este STATE.md son el handoff autorizado al receptor Claude. Vault/30_Sessions, HANDOFF.md del proyecto, bug-ledger y pipeline_feedback están FUERA DE MI ALCANCE por la lista exclusiva del brief. No se escribieron ni se invocaron tools MCP para actualizarlos. Tampoco se hizo lease/session_status por la prohibición expresa de esta corrida.",
              "", "Observaciones para integrar en la prueba conjunta ya prevista (sin solicitar otra corrida):", "",
              "1. Verificar que el .c desplegado coincide con SHA-256 9ce3ec0bb1430dea4f91e15e5efa4fffb0d77d95570da259b8463ec4c5017a19 y que el motor acepta el módulo Mission.",
              "2. Con cámara MCP activa en pie, comprobar camera_set/lookat y camera_get: snapshot utilizable y coincidencia con el viewport real. Incluir free/orient/matrix en el smoke si ya estaban previstos.",
              "3. Sentado antes de la petición, y entrando en vehículo durante SETTLE: esperar camera.ok=false con camera_unavailable_vehicle o camera_unavailable_parented_player, viewport_moved=false y arrays vacíos. Ningún camera.ok=true solo por existir m_ActiveCam.",
              "4. Repetir el orden camera_set -> restore_gameplay -> camera_get de ambos runs y confirmar supervivencia del cliente, sin nuevo ACCESS_VIOLATION/minidump ni timeout causado por desaparición del proceso.",
              "5. Tras restore, comprobar manualmente vista, controles, HUD y simulación; esperar restore_unverified (el cleanup se ejecuta pero no se certifica). Anotar si el resultado es no_scripted_camera o vehículo/parentado según el estado.",
              "6. Comprobar cámara MCP desactivada/reemplazada y camera_get sin haber creado una: fallo explícito, sin datos fabricados. Esto valida los nativos restantes y el supuesto operativo de IsActive; no quedó probado offline.",
              "", "## D - QUE PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO", "",
              "El problema real está confirmado, pero no queda probado que sea exclusivo de estar sentado: ambos fallos ocurren en una llamada nativa que debería poder devolver null según vanilla, y restore_gameplay libera la cámara justo antes de consultarla. Puede ser la ausencia del objeto de cámara tras ReleaseCamera, una transición de cámara de jugador, o un efecto de la combinación de mods/versión 1.29.163709. Dos runs con la misma combinación y secuencia no aíslan esos factores. El tipo y el dueño del objeto interno nulo no se deducen de RAX=0.", "",
              "El brief orientaba hacia una referencia obsoleta devuelta al script; esa orientación habría colocado el guard demasiado tarde. La evidencia más fuerte es el frame completo y los registros, que permiten eliminar la llamada concreta sin adivinar ese ciclo de vida. El rechazo adicional de vehículo/parentado es una decisión conservadora frente a la observación de viewport sobreescrito, no prueba de que el asiento causase el acceso nulo.", "",
              "El arreglo tiene un coste funcional serio: renuncia a observar cámaras ajenas/al jugador y deja restore_gameplay sin verificación positiva tras liberar. Puede que el producto prefiera recuperar esa capacidad mediante otra vía nativa validada en motor; eso no justifica conservar el getter que murió ni considerar ausencia como observación. Esta entrega corrige la dependencia peligrosa y documenta la degradación; no afirma equivalencia funcional completa ni crash-free demostrado.",
              "", "## E - LO QUE NO PUDE VERIFICAR", "",
              "- Compilación Enforce: no hay compilador disponible según este brief, y el validador es textual; no resuelve todos los miembros/tipos ni ejecuta código. Se abrieron declaraciones y herencias reales de los símbolos usados, pero eso no es una compilación.",
              "- Reproducción en motor y supervivencia del cliente con el diff: restricción explícita de ESTE brief, que prohíbe lanzar DayZ/DayZDiag, tocar la sesión o usar tools MCP. No se ha ejecutado una reproducción nueva ni se pide una aquí.",
              "- Seguridad universal de Camera.IsActive, GetCurrentFOV, IsInterpolationComplete y getters de instancia restantes: el C++ no está disponible con símbolos ni se hizo experimento en motor. La llamada que aparece en las dos pilas sí ha desaparecido del código del bridge.",
              "- Tipo/ciclo de vida exacto del objeto interno nulo: los dumps y bytes muestran el acceso, no el nombre del campo +0x68 ni quién lo retiró. No se presenta como hecho la cámara obsoleta ni la sobreescritura por vehículo como causa aislada.",
              "- Que el usuario estuviera sentado en ambos instantes y que el archivo desplegado sea byte a byte el snapshot: la observación procede de la otra sesión; el mapeo exacto de frames respalda la versión pero los dumps no incluyen un hash de scripts. No se inspeccionó el juego vivo por la prohibición del brief.",
              "- Paridad de los scripts vanilla locales con el ejecutable de los dumps: firmas y usos locales sí abiertos; no hay prueba de correspondencia exacta con 1.29.163709.",
              "- Retorno efectivo de controles, HUD, simulación y cámara tras restore: el consumidor solo confirma lo que puede observar; tras este cambio devuelve restore_unverified. No se reparó una API de verificación nueva ni la cámara de vehículos.",
              "- Suite completa, daemon en 8765, despliegue, PBO y bundle: no ejecutados por restricciones explícitas de ESTE brief. Solo tests nombrados y procesos temporales offline; ningún proceso de producción gestionado. No se necesita resellado por los ficheros modificados.",
              "- Certificación global del addon: el validador devuelve dos FAIL fuera del cambio de cámara. Resolverlos y editar sus archivos excede la lista de ficheros escribibles de ESTE brief.",
              "- Registro de memoria/commit y revisión de otra familia: no realizados por esta lane; el brief reserva el commit al receptor Claude y prohíbe subagentes. La evidencia queda en este directorio para su revisión.", ""]
    return "\n".join(lines)


size = 0
for _ in range(10):
    text = build_report(size)
    actual = len(text.encode("utf-8"))
    if actual == size:
        break
    size = actual
else:
    raise AssertionError("STATE size did not converge")
save(OUT / "STATE.md", text)
assert BRIDGE.read_bytes() == after
save(OUT / "DONE", b"")
for path in (BRIDGE, TEST, OUT / "DIAGNOSIS.md", OUT / "red.log", OUT / "green.log", OUT / "STATE.md", OUT / "DONE"):
    data = path.read_bytes()
    assert len(data) == path.stat().st_size
    print(f"VERIFIED {path.name}: {len(data)} bytes, sha256={hashlib.sha256(data).hexdigest()}")
print(f"DELIVERY COMPLETE: {OUT}")
