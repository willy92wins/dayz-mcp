# Claude Sonnet 5 — revisión de plan — grupo B (calibrada)

- Proveedor: `anthropic`
- Modelo: `claude-sonnet-5`
- Sesión calibrada: `/tmp/dayz-inbox-sonnet-b-cal-20260830/01a053b5-be1b-718e-8415-a37683795110.jsonl`
- Historial padre: `/tmp/dayz-inbox-sonnet-b-r2-20260830/01a053a4-0bf3-7115-a771-c1c4d5f46e83.jsonl`
- Continuidad: fork byte-exacto porque el worker daemon de `--resume` devolvió `ENOENT` antes de entregar el prompt.
- Cierre del turno: `stop`
- Esta salida calibrada sustituye íntegramente la primera pasada.

## Salida del revisor
ID: 8f8c
PLAN: plans/inbox-20260830/05-fb-20260829-023649-8f8c.md
SHA256: 412b627bd4254cc5a82f3cba15cec1ef72770d93ed8627ae6e741232b398e1c3
VERDICT: PASS
FINDINGS:
- NONE
WHY: Reverifiqué adversarialmente la firma real de `execute_wait_for` (`server.py:2036-2045`): parámetros son `runtime, condition, value, pattern, timeout_s, poll_interval_s, lookback_lines, lookback_from, marker` — sin `run_id` en ningún nivel, incluido el wrapper público `wait_for` en `server.py:4149-4173` que tampoco lo acepta y delega igual. `compute_bridge_ready` (`server.py:355-398`) separa `no_run/server_poll_stale/client_not_polling/version_mismatch`; `dayz_test_tool.py:402-458` confirma liveness solo-PID vs publicación `server_alive/client_alive`; `execute_wait_for` en `:2164-2180` solo re-lanza `version_blocked/game_not_ready/daemon_unavailable` sin retry; `MCPClientBridge.c:1802-1812` confirma `candidate.Type().ToString()`; `entities_query` en `:1640-1666` confirma `nearest_player_m=None`/`remote_unverified` sin razón. DPF H13/C3/E5 verificados literalmente en `product-spec.md:67,90,148`; el DAG congelado (`00-execution-dag.md:144-146`) reproduce el mismo contrato de reintento y de `no_player_connected`, resolviendo la tensión obligatoria sin ambigüedad.

ID: 4d66
PLAN: plans/inbox-20260830/16-fb-20260829-104608-4d66.md
SHA256: d5a0602e20b501f9d8142108a95f10ee1c11771fbb42308c81c61cfa9654eda7
VERDICT: PASS
FINDINGS:
- NONE
WHY: Reverifiqué `Binding.run_id` y el enqueue bajo lock: `Binding` (`instance_fence.py:129-140`) tiene `run_id: str` no-opcional, poblado en la construcción real (`loopback.py:897`, no en el helper de test `install_bound_peer`). Tanto el enqueue normal (`loopback.py:1466-1506`) como `exec_enforce` (`:1567-1596`) obtienen `binding = self._bindings.get(fence_instance)` dentro del mismo `with self._lock:` y solo en la rama de éxito (`queue.append(command)` ya ejecutado, nunca en ramas `fence_lost/capacity_lost/commit_failed`), confirmando que "capturar run_id+comando bajo lock... sin contar rechazos" es implementable tal como está descrito. `JsonlAuditWriter._rotate_locked` (`runtime_state.py:26-27,351-362`) confirma rotación real acotada: `AUDIT_MAX_BYTES=5MB`, `AUDIT_BACKUPS=5`, desplazamiento `.1..5` con purga del más viejo — no es una afirmación vacía. `_run_all_dead`/`_reap_run_locked`/`_run_age_s`/`box_occupancy` siguen verificados exactamente en las líneas citadas. El DAG (`00-execution-dag.md:149-153`) reproduce el diseño casi palabra por palabra. Ningún contraejemplo de preemption o de pérdida de `run_id` bajo condiciones de carrera.

ID: 7c88
PLAN: plans/inbox-20260830/17-fb-20260829-104625-7c88.md
SHA256: 22c4e413e752f23e8dd4de99d52bd0659c477e24218d9d0e21742cbd1fa6daad
VERDICT: PASS
FINDINGS:
- NONE
WHY: `dayz_test_request.py:337-344` confirma exactamente el par de restricciones (`client` exige `run_id`, `server/all` lo prohíbe); `dayz_test_worker.py:587-601` confirma adopción con `existing_run_id=run_id` en el start de cliente; `dayz_test_tool.py:30-47` confirma `_TERMINAL_KEYS` exacto y `:461-472` confirma `_validate_terminal_context` validando `expected_run_id`. Sin overlap real de OWNS: M20/M21 compartidos con 4407 están co-asignados explícitamente en la fila del DAG, no es solape indebido. DPF H11/H13 verificados en `product-spec.md:146,148`.

ID: 344d
PLAN: plans/inbox-20260830/19-fb-20260829-111016-344d.md
SHA256: 101eeafbaf4d4ab44d776e514606fd6293f1bb6e5f27d5f3afa7384680bb3245
VERDICT: PASS
FINDINGS:
- NONE
WHY: Reverifiqué la independencia del mutante E6: `check_rel_version()` (`verify_corrective.py:472-483`) compara `config.cpp`/`LFPG_Defines.c` contra un `expected_version` externo hardcodeado por el caller, no derivado del propio artefacto; `validate_pbo()` (`:794-901`) desempaqueta con PboViewer (herramienta externa), compara cada `.c` byte a byte contra el árbol source real (`collect_source_scripts`) y convierte `config.bin` con CfgConvert antes de `validate_debinarized_config(debinarized, expected_version)` — verificación genuinamente independiente, no circular. `config.cpp:208` y `LFPG_Defines.c:527` confirmados `"1.2.4"`; `--expected-version` default `1.2.1` confirmado en línea 1028; `version_gate()`/`VERSION_SOURCES` y el fixture drift/coherente confirmados en `build_guarded.py:152-190` y `test_version_gate.py:37-56`. La cita `product-spec.md:91` es E6 ("Integridad de artefactos auxiliares"), no E3 (línea 88, Packaging/install): el challenge de la tensión obligatoria queda resuelto correctamente contra el Intent real de E6.

ID: 4407
PLAN: plans/inbox-20260830/20-fb-20260829-115147-4407.md
SHA256: 6644ed6696b7f2285853fe49d0ec40ffe9c67810bce11c010ce785f3c9c62f6d
VERDICT: PASS
FINDINGS:
- NONE
WHY: Recorrí adversarialmente pre-pass/digest/A-F/fallos parciales buscando huecos lógicos y no encontré ninguno: la matriz A–F es la partición exhaustiva y no solapada de {storage ausente/presente} × {sidecar ausente/válido/inválido} = exactamente 6 combinaciones, coincidiendo con las 6 declaradas. El pre-pass compensa en orden inverso `prepared→storage_moved→marker_moved` (deshacer renames en orden contrario al de aplicación) y trata `marker_published` como caso de finalización (no rollback) sin borrar `rotation_pending`, evitando descartar una rotación ya efectiva; una caída tras D/F con storage aún ausente converge correctamente a B conservando backup+aviso, consistente con la regla de "nunca overwrite/delete". El digest incluye tamaño+SHA por fichero y una entrada por directorio (incluidos vacíos), evitando el punto ciego de ignorar cambios de estructura sin contenido. `[EXACT]` de código (modset order, mission/profiles como raíces distintas, allowlist duplicada builder/native_bundle, terminal estricto) siguen verificados byte a byte. DPF H11 coincide literalmente con `product-spec.md:146`.

ID: a396
PLAN: plans/inbox-20260830/21-fb-20260829-133459-a396.md
SHA256: 68602dc76c9a29358d995a19e4489d0ad3d8e560731ec97cafc3637ee1740390
VERDICT: PASS
FINDINGS:
- NONE
WHY: Reverifiqué la fachada de `mission`: `dayz_test_tool.py:15,134-135` confirma `_MISSION_ALIASES = frozenset({"chernarus","livonia","sakhal"})` y `_fail("bad_mission")` para cualquier otro valor, sin bypass — tracé el único punto de entrada público (`execute_dayz_test_run` → `build_run_request`, `dayz_test_tool.py:592-630`) y confirmé que no existe una ruta alterna que evite este precheck. El parser (`dayz_test_request.py:261-285`) ya acepta alias o ruta absoluta dentro de `mission_roots`, confirmando la divergencia real que M19 debe cerrar. El research item citado confirma PID 25484 vs Steam vivo 13856 y el discriminador exacto. `client_alive` PID-only y `compute_bridge_ready` con causas separadas confirmados de nuevo. DPF H6/H11 coinciden con `product-spec.md:141,146`.

ID: cc2d
PLAN: plans/inbox-20260830/22-fb-20260829-135408-cc2d.md
SHA256: 68da15699cbbe6766dfebd8e2932b2f901416ce3dd911ffdca7ca82e6a37246f
VERDICT: PASS
FINDINGS:
- NONE
WHY: `_run_all_dead` (`process_lifecycle.py:2240-2254`) confirma `process_alive` para owned vivo; `reap_dead_runs` es exactamente `:2304-2320` (línea 2304 `def reap_dead_runs`, línea 2320 `return self._reap_dead_runs_locked()`) y nunca llama `terminate`; `box_occupancy` (`:2415-2474`) confirma `age_s` y owner truncado `[:12]`. DPF H6/H13 verificados en `product-spec.md:141,148` ("nunca preempta"). Ficha es solo fixture/evidencia sobre comportamiento ya existente; comparte módulo M17 con 4d66 según la fila del DAG, sin solape de código nuevo.

ID: 668f
PLAN: plans/inbox-20260830/32-fb-20260830-011217-668f.md
SHA256: 1b6e89fdc3880a9561076dc474f5a97b7c7455e34a632fbbfd9f1d03164742ca
VERDICT: REVISE
FINDINGS:
- C:/Users/guill/.agents/skills/dayz-test-ingame/templates/dayz-test.ps1:534-540 (idéntico en C:/Users/guill/.claude/skills/dayz-test-ingame/templates/dayz-test.ps1:534-540): el hecho `[EXACT]` afirma que la plantilla "solo falla por ExitCode y ausencia/tamaño del PBO", pero el código muestra `Die` solo para `ExitCode!=0` (línea 534) y ausencia del PBO (línea 536); el chequeo de tamaño insuficiente (`(Get-Item $pbo).Length -lt 4096`, líneas 538-539) usa `Warn`, no `Die`, y no detiene el build. Corrección concreta: reescribir el hecho como "falla por ExitCode y ausencia del PBO; el tamaño insuficiente solo emite un `Warn` no bloqueante".
WHY: Reverifiqué la independencia de los mutantes de la Enmienda calibrada: la validación de procedencia (extractor acreditado + SHA byte a byte contra source, `dayz-pbo-build/SKILL.md:551,590-610`) y el gate de LFQuad2 actual (`dayz-test.ps1:355-382`, solo tokens/sufijos) son genuinamente independientes del artefacto bajo prueba, no circulares — el diseño evita correctamente el punto ciego de "hash igual = stale" al comparar contra source real, no contra una copia previa del PBO. `product-spec.md:91` (E6) y `00-execution-dag.md:185-189` confirmados exactos, y el challenge E6-no-E3 de la tensión obligatoria queda resuelto. Pero la imprecisión detectada en el primer pase en un hecho marcado `[EXACT]` persiste tras la relectura adversarial (la Enmienda calibrada vinculante ya usa la formulación correcta sin mencionar tamaño, confirmando que solo el bloque "Hechos verificados" original necesita esa corrección menor); esto basta para no otorgar PASS bajo el contrato de precisión exigido.

GROUP_VERDICT: REVISE (7 PASS, 1 REVISE — 668f requiere la corrección puntual del hecho `[EXACT]` sobre el fallo por tamaño de PBO; ninguna refutación adicional sobrevivió a la calibración adversarial en `execute_wait_for`, parser/fachada de mission, `Binding.run_id`/enqueue, `JsonlAuditWriter`, pre-pass/A-F/digest de storage, ni en la independencia de los mutantes E6)
