# Revisión adversarial (familia Anthropic) del lote f45d — DayZ-MCP

Eres el revisor de OTRA familia de una entrega hecha por Grok (Cursor). No tienes que arreglar nada: tu
entregable es un dictamen escrito. Escribe el fichero `REVIEW-ANTHROPIC-F45D.md` en la carpeta que se te indica
al final, y nada más. No modifiques ningún fichero del repositorio ni del workspace del implementador.

## Qué se arregla

Ficha `fb-20260906-193626-f45d`: cuando el daemon rechaza un `/enqueue` con un código de valla de run
(`run_not_owned`, `run_state_unavailable`, `enqueue_cancelled`), el cliente MCP lo convierte en el texto desnudo
`remote_error` y tira el `hint` que el daemon adjunta. Diagnóstico completo con evidencia (audit del daemon,
control positivo, `path:line`): `DECISION-F45D.md`. Contrato exacto del cambio: `BRIEF-F45D.txt` (secciones 3 y 4).

## Carga inicial (léelo todo antes de opinar)

1. `DECISION-F45D.md` — el diagnóstico y el alcance decidido (qué NO se decide en este lote).
2. `BRIEF-F45D.txt` — lo que se le pidió al implementador, con `path:line` verificados.
3. `DIFF-F45D-product.patch` — diff LF-normalizado de `tools/dayz_mcp/server.py` contra la rama.
4. `DIFF-F45D-test_wait_for.patch` — diff de `tools/tests/test_wait_for.py`.
5. `NEW-test_enqueue_refusal_reaches_the_caller.py` — el módulo de tests nuevo, entero.
6. `STATE-F45D-grok.md` — lo que el implementador dice haber hecho y verificado (NO lo tomes como verdad:
   `RUN-F45D.txt` tiene las ejecuciones hechas por el receptor, no por el implementador).
7. El árbol de la rama (solo lectura) para abrir cualquier `path:line`:
   `C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\wt-f45d`
   (HEAD `4e264bb`; el diff aún NO está aplicado ahí). El workspace con el diff aplicado, también solo lectura:
   `...\scratchpad\lote-f45d\ws`. Intérprete para ejecutar tests en el workspace, desde `<ws>\tools` con
   `PYTHONPATH=.`: `"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\.venv-mcp\Scripts\python.exe"`.
   No corras la suite completa (`discover`): levanta daemons. Módulos sueltos sí.

## Preguntas que el dictamen tiene que contestar, una a una, con `path:line`

1. **Invariante de redacción.** Con el cambio, ¿puede llegar al llamante texto de un payload remoto que NO sea
   un código de la lista blanca? Recorre `_remote_error_code` (`server.py:361-366`), el nuevo helper del hint y
   la cola de `_public_enqueue_error`. Construye mentalmente (o ejecuta) tres payloads hostiles: código
   desconocido con hint, código conocido con hint de 10 000 caracteres, código conocido con hint que contiene
   un token de lease. ¿Sobrevive `tests/test_client_mode.py:331-370` byte a byte y verde?
2. **Procedencia del hint.** El hint es prosa del daemon. ¿Por qué canal llega (`control_client.py` /
   `accredited_daemon_transport.py` / `ClientRuntime._call`, `server.py:1442-1490`) y qué garantiza que el
   emisor es el daemon acreditado y no otro proceso en el puerto? Si la garantía no está en el cambio sino en el
   transporte, cítala; si no existe, dilo.
3. **Comparaciones exactas rotas.** El texto del error de enqueue cambia de forma (`"code"` → `"code: hint"`) para
   los códigos con hint. Busca TODOS los consumidores que comparan ese texto por igualdad o `startswith`
   (`server.py:1527`, `:1618`, `execute_wait_for` `:2617-2642`, `playbook_tool.py:116/224`,
   `ui_dialog.py:203-209`, `dayz_test_tool.py:255`, y lo que tú encuentres con grep). ¿Alguno deja de funcionar?
   ¿La sustitución de `error in _STALE_LEASE_ERRORS` por `_remote_error_code(payload) in _STALE_LEASE_ERRORS`
   conserva la semántica para `lease_expired`/`lease_invalid` desnudos?
4. **El censo por AST** (`EnqueueCodeCensusTest`). ¿Recorre de verdad las funciones que dice? ¿Recoge los
   `return "<str>"` y las tuplas `(str, None, None)` de `_enqueue_fence_target` (`loopback.py:1331-1388`)? ¿Qué
   códigos encuentra y cuáles NO puede ver por construcción (por ejemplo los que devuelve
   `session_coordination.authorize`, `loopback.py:1591-1605`, o `validate_command_args`, `:781-796`)? ¿Está el
   test vacío o tautológico (LL: control de vacuidad)?
5. **La descripción de `wait_for`** (`server.py:4853-4856`). Dice que `session_acquire_wait` adopta el run. ¿Es
   verdad? Cita `loopback.py:3119-3140` (`_adopt_on_grant`, P-J2) o lo que corresponda. Si la receta es
   incorrecta o incompleta (por ejemplo, si adopta solo cuando el run ocioso es ÚNICO), dilo y propón la frase
   exacta.
6. **Alcance.** ¿El diff toca algo fuera del write-set (`server.py`, `test_wait_for.py`, módulo nuevo)? ¿Cambia
   alguna rama con receta (`retail_quarantine`, `lease_required`, `version_blocked`), `execute_wait_for`,
   `_remote_error_code`, `_game_not_ready_reason` o `_bridge_error`? Cualquier cambio fuera del contrato del
   brief es hallazgo, aunque parezca mejora.
7. **Tests: ¿miden lo que dicen?** Para cada test nuevo, ¿qué mutante lo mataría? ¿Hay alguno que pase con el
   producto viejo Y con el nuevo (tautología)? Los tres mutantes del brief: ¿los resultados de `STATE-F45D-grok.md`
   son plausibles con el diff que ves?
8. **Lo que el implementador dice no haber podido verificar.** Léelo y di si alguno de esos huecos bloquea la
   integración.

## Formato del dictamen

- Cabecera: veredicto en una línea, uno de `SEGURO INTEGRAR`, `INTEGRAR CON CAMBIOS` (lista cerrada de cambios,
  cada uno con `path:line` y el texto exacto), `NO INTEGRAR` (motivo con repro).
- Hallazgos numerados H1..Hn, cada uno con severidad (BLOQUEA / DEBE / PUEDE), `path:line`, qué está mal, cómo
  lo verificaste (grep, lectura, ejecución) y la corrección propuesta como diff mínimo.
- Sección «Lo que verifiqué ejecutando» (comandos y salidas literales) y «Lo que NO verifiqué».
- Español; código e identificadores en inglés. Sin cortesías, sin resumen del diff: dictamen.

Escribe el dictamen en:
`C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\2adef081-e055-49c6-9d06-f5c167808be8\scratchpad\lote-f45d\review\REVIEW-ANTHROPIC-F45D.md`
