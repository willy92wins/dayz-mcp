# Claude Sonnet 5 — revisión de plan — grupo D (calibrada)

- Proveedor: `anthropic`
- Modelo: `claude-sonnet-5`
- Sesión calibrada: `/tmp/dayz-inbox-sonnet-d-cal-20260830/01a053d4-12ba-705a-980c-ec2a0c224baf.jsonl`
- Historial padre: `/tmp/dayz-inbox-sonnet-d-r2-20260830/01a053c9-294a-7512-8b07-7f63949621ff.jsonl`
- Continuidad: fork byte-exacto de la primera pasada; la calibración heredó la conversación completa.
- Cierre del turno: `stop`
- Esta salida calibrada sustituye íntegramente la primera pasada.

## Salida del revisor
He releído el contrato, la ficha de grupo, mi primera respuesta y `calibrate-d.md`, y he reabierto el código con foco adversarial en los cuatro puntos pedidos, sin editar nada:

**1) Orden real `ValidateSpawnArgs`/`Dispatch`:** reconfirmado en `MCPBridge.c` — `DispatchWorldSpawn` (540-558) llama `ValidateSpawnArgs` antes de `CreateObjectEx`; dentro de `ValidateSpawnArgs` (2473-2542), `y==0→SurfaceY(x,z)` ocurre antes del chequeo `if (args.flags != 0)` y antes de `validation.pos = Vector(x,y,z)`. El test (`test_world_spawn_ground_contract.py`) lo verifica por índices de substring y pasa (ejecutado: 2/2 OK).

**2) Instante observado del control y!=0:** tracé la cadena completa `validation.pos` (pre-`CreateObjectEx`) → `pos_real` (leído en `IsSpawnReady`/`PostJobSuccess` vía `job.subject.GetPosition()`, en el primer tick de "ready", líneas ~2857/3211-3216) → asentamiento físico posterior (documentado en el propio comentario del gate 2026-08-17 del test: "settled at y=291.97 a frame later"). Confirma que la distinción de tres capas que exige fc6e es real en código, no sólo textual.

**3) DTO/transporte de entidades y telemetría:** releí `result_prune.py` — sólo campos `ref` contenedores se podan si están vacíos; escalares (`found`, `seated`, `has_cargo`) nunca se podan por diseño explícito ("pruning scalars would silently delete real answers"). Esto confirma semánticamente que los campos que 40e4/0de3 proponen añadir sobrevivirán la serialización real hacia Python, no sólo que existen como texto en el `.c`.

**4) Consumidores reales de instructions/lookback:** reconfirmé que el texto en `server.py:2517-2534` es el literal pasado a `FastMCP(instructions=...)` (el servidor real), y que `execute_wait_for`/`wait_for` (2036, 4149-4172) son la implementación viva, no un doc aislado. El test existente sólo comprueba 6 substrings ajenos a los 6 fragmentos de 55dd, confirmando que la crítica de "seis substrings autocumplidos" es correcta.

No apareció ningún hallazgo nuevo que degrade un veredicto. Reemito los siete bloques completos contra los mismos hashes, con el formato de líneas literal pedido.

```
ID: 55dd
PLAN: plans/inbox-20260830/06-fb-20260829-024747-55dd.md
SHA256: e0e0114e045c36f9070dc0593476e7ea1186dab20a76f9aee7cb608b99f55b42
VERDICT: PASS
FINDINGS:
- NONE
WHY: Identidad coincide con manifest y bytes. Las citas [EXACT] a server.py (2011-2033, 2036-2045, 2198-2202, 2523-2535) y MCPClientBridge.c:1887-1891 se releyeron línea a línea y son exactas. El plan excluye explícitamente el fragmento y=0 (delegado a fc6e), evitando solape de OWNS. El test existente (`test_fastmcp_instructions_name_the_junior_flow`) comprueba 6 substrings distintos a los 6 fragmentos de la ficha, así que exigir un test semántico nuevo es correcto y no tautológico. DPF B1/C4/E5 encajan con product-spec.md:54/68/90.

ID: 251d
PLAN: plans/inbox-20260830/10-fb-20260829-025502-251d.md
SHA256: 16572ca7bf5bd6686987654479f9359a00f9627682e5352b9359d434bb250187
VERDICT: PASS
FINDINGS:
- NONE
WHY: Identidad verificada. Default interno/público 200 (server.py:2036-2045/:4149-4172) y matching por substring (:2193-2203) confirmados. El diseño de fixtures 200/201 traza directamente a C4 (product-spec.md:68: "con una ventana de 200 líneas, el borde 200 satisface y 201 no"), cumpliendo la tensión obligatoria. Prohíbe parche automático sin nueva aprobación y exige verificar firma interna/pública/forwarding por separado.

ID: d73b
PLAN: plans/inbox-20260830/12-fb-20260829-030056-d73b.md
SHA256: 6937eb2e3170f9ee9f0c499da420993de398ebf41dc5b2a15b145c9786e2af29
VERDICT: PASS
FINDINGS:
- NONE
WHY: Identidad verificada. Citas a default 200 y a `ok=true,satisfied=false` en timeout (server.py:2011-2033) confirmadas. El fixture causal (acción→respuesta ya publicada→wait_for, 200 satisface/lookback_lines=0 vence) traza a la segunda mitad de C4 (product-spec.md:68), cumpliendo la tensión obligatoria. Bloquea expresamente heredar PASS de 251d, evitando verificador tautológico.

ID: fc6e
PLAN: plans/inbox-20260830/13-fb-20260829-032121-fc6e.md
SHA256: 7ce83ba16ba5cc93116b58098308935b3907d0a2e8011bb3c218c03d2a16d9b6
VERDICT: PASS
FINDINGS:
- NONE
WHY: Reabierto con foco adversarial: `DispatchWorldSpawn` valida antes de `CreateObjectEx`; `ValidateSpawnArgs` resuelve y==0 antes de flags y de `validation.pos`. Tracé la cadena completa validation.pos→pos_real (leído en `IsSpawnReady`/`PostJobSuccess`, primer tick de "ready")→asentamiento físico posterior (gate 2026-08-17 documentado en el test), confirmando que la distinción de tres capas de la ficha es real en código. Test ejecutado: 2/2 OK. DPF B1 coincide con product-spec.md:54. No exige igualdad `pos_real.y==surface_query.y` ni usa fixture UI.

ID: 243b
PLAN: plans/inbox-20260830/14-fb-20260829-103347-243b.md
SHA256: 2ab4d89de9f4107c0789b073edf02aab62a90b2b79238adbde1b97fdb6fd2c5c
VERDICT: PASS
FINDINGS:
- NONE
WHY: Identidad verificada. `object_delete` (server.py:2987-2996) distingue `ok` de `deleted`; bridge (MCPBridge.c:587-606) inicializa `deleted=0` y sólo sube a 1 tras `ObjectDelete`. `player_respawn` (server.py:~3703-3714) documenta `ok/requested` como solo-solicitud. Exige gate semántico real con mutante rojo si se deriva efecto de `ok`, no presencia textual. DPF E5 coincide literalmente con product-spec.md:90 ("transporte `ok` de efecto observado").

ID: 0de3
PLAN: plans/inbox-20260830/30-fb-20260830-002237-0de3.md
SHA256: 136bacf233b3572e632bfae54782ee5d885234b658c6e9e053072da76413a490
VERDICT: PASS
FINDINGS:
- NONE
WHY: Las nueve citas [EXACT] (MCPClientBridge.c:1082-1117/2332-2347, MCPMessages.c:425-429/454, product-spec.md:67, transport.c:111-120/475-480, dayzplayer.c:673-677, object.c:469-479, enscript.c:25-39 bajo P:/scripts) se re-verificaron byte-exactas. Confirmé que `not_seated` hoy produce `result.ok=false`→`ToolError` vía `wait_for_result` (server.py:676), validando la transición descrita. Confirmé en `result_prune.py` que campos escalares nuevos (found/seated) sobreviven la serialización real, no sólo existen en el `.c`. El diseño separa correctamente found/seated/seat/type/classname de métricas exclusivas `CarScript`. DPF C3 coincide con product-spec.md:67.

ID: 40e4
PLAN: plans/inbox-20260830/34-fb-20260830-112438-40e4.md
SHA256: c78057d6d97270b2ff6d2f4a2a79f512024dc17c4b61a64d847bb2a69d1a071a
VERDICT: PASS
FINDINGS:
- NONE
WHY: Citas [EXACT] a MCPBridge.c:1371-1424, MCPMessages.c:353-365, entityai.c:1834/677-685, inventory.c:137-139 re-verificadas exactas. Confirmé que `entities_query` (server.py:3449-3459) hoy documenta sólo `{type, classname, pos, distance}`, sin `has_cargo`, y que `result_prune.py` no filtra campos nuevos dentro de `entities`, validando semánticamente que el campo aditivo sobrevivirá el transporte real. El plan prohíbe explícitamente `HasAnyCargo`/listas como sustituto de capacidad. DPF C3 coincide con product-spec.md:67; paralelismo M03∥M04 tras M02 coincide con la ola 2a del DAG.

GROUP_VERDICT: PASS (7/7 — 55dd, 251d, d73b, fc6e, 243b, 0de3, 40e4)
```

