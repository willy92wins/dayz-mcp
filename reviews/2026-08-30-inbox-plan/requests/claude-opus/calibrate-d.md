# Calibración adversarial Claude Opus 5 D

Contexto de filesystem para esta segunda pasada: `P:/X` =
`/mnt/c/Users/guill/OneDrive/Documentos/DayZ Projects/X` (mapeo `subst` verificado).
Reabre por esa traducción toda cita P: que antes pareciera inaccesible. No abras contenido de
revisiones históricas ni uses copias de build/deploy como sustituto del source canónico.

En la MISMA sesión, relee tu propia primera respuesta desde el contexto y reabre los siete planes,
sin editar. No abras desde disco la ficha de grupo: ya está en el contexto. En `reviews/**` sólo
puedes leer este `calibrate-d.md` y `common.md`. No leas ni recibas ninguna salida Sonnet.

Reabre el orden real de `ValidateSpawnArgs`/Dispatch, el instante observado del control y!=0,
DTO/transporte de entidades y telemetría, y los consumidores reales de instructions/lookback.
Distingue evidencia semántica de grep textual.

## Ataques dirigidos obligatorios

No cuentes como gate semántico un grep, AST-presence o mock cuyo expected se derive del mismo
campo observado. Para cada ataque decide si el plan materializa un discriminador independiente;
si no, exige la corrección mínima.

- **55dd — significado, no tokens.** El test existente sólo hace `assertIn` sobre instrucciones
  (`tools/tests/test_weak_agent_consumer_ux.py:96-104`), mientras la ficha promete predicados
  semánticos. Ataca mutantes que conservan keywords pero invierten significado: `place_safely`
  después del spawn; Y solicitada en vez de `surface_query.y`; flags 3108 fuera del infectado
  vivo; afirmar que `wait_for` lee `.ADM`; decidir timeout por `ok` y no `satisfied`; o sustituir
  el `ItemBase` real por classname/null. Una paráfrasis semánticamente equivalente debe poder
  aceptar; mecanismos: `tools/dayz_mcp/server.py:2020-2028,2518-2535` y
  `addon/scripts/5_Mission/MCPClientBridge.c:1858-1864,1887-1898`.

- **251d — frontera pública exacta 200/201.** Exige logs LF aislados de exactamente 200 y 201
  líneas, needle sólo en la primera, invocación de la tool pública omitiendo `lookback_lines` y
  evidencia de que el fichero fue leído. El primero satisface y el segundo no; mutantes 199/201
  o forwarding omitido deben cambiar el resultado (`tools/dayz_mcp/server.py:1766-1789,
  1826-1848,1863-1874,4149-4173`).

- **d73b — causalidad pública 200/0.** Usa dos runtimes/logs independientes. La acción fake
  escribe un correlation-id único, hace flush y sólo entonces retorna; después nace el marker de
  `wait_for`. Con argumento omitido debe encontrarlo; con `lookback_lines=0` no, sin segunda
  escritura. Timeout normal es `ok=true,satisfied=false`, no excepción
  (`tools/dayz_mcp/server.py:1863-1867,2020-2028,2117-2129`). No heredes el PASS de 251d.

- **fc6e — tres observables distintos.** Separa `validation.pos` antes de `CreateObjectEx`, la
  primera posición pública del job y la simulación posterior. `ValidateSpawnArgs` resuelve Y
  antes de flags/posición (`addon/scripts/5_Mission/MCPBridge.c:2473-2542`) y Dispatch crea/encola
  (`:540-575`), pero `pos_real` se lee después en `PostJobSuccess` (`:3202-3217`) tras readiness
  (`:2856-2880`). El test actual sólo inspecciona texto/orden
  (`tools/tests/test_world_spawn_ground_contract.py:38-53`). El control y!=0 debe acreditarse en
  el boundary pre-create; y=0 debe probar flags 0/3108 con instante fijo, sin exigir igualdad
  geométrica post-simulación. Decide además si el fake world prometido existe realmente.

- **243b — contrato frente a efecto.** `deleted=1` se fija inmediatamente después de invocar
  `ObjectDelete` (`addon/scripts/5_Mission/MCPBridge.c:587-605`) y `requested` sólo acredita
  solicitud de respawn. Si la ficha afirma efecto, exige observable externo pre/post; si sólo
  describe el contrato, limita honestamente el gate y mata mutantes que confunden `ok` con el
  campo de efecto.

- **0de3 — `Transport` antes de `CarScript`.** Exige conductor, pasajero, a pie, transporte
  no-`CarScript` y transporte presente con `CrewMemberIndex=-1`. El código actual castea demasiado
  pronto (`addon/scripts/5_Mission/MCPClientBridge.c:1082-1090,2332-2347`). Acredita que
  type/classname nacen de `Transport`, que métricas cero no borran presencia y que el sentinel de
  `GetSeatAnimationType` no inventa driver cuando una subclase no overridea
  (`P:/scripts/3_Game/vehicles/transport.c:475-480`; enums
  `P:/scripts/3_Game/dayzplayer.c:673-677`).

- **40e4 — capacidad en wire.** Exige cuatro filas públicas: Object no-EntityAI, EntityAI sin
  cargo, cargo-capable vacío y ocupado. Vacío/ocupado conservan `has_cargo=true`; los otros dos,
  false explícito. Mata `HasAnyCargo`, `GetItemCount()>0`, `EntityAI.Cast()!=null`, allowlist por
  classname y omisión de false. `HasAnyCargo` mide ocupación
  (`P:/scripts/3_Game/entities/entityai.c:677-685`); capacidad procede de
  `GetInventory().GetCargo()` (`:1834-1836`,
  `P:/scripts/3_Game/systems/inventory/inventory.c:137-143`). Conserva count, cardinalidad y
  nearest-first y exige `reliability=player_in_bubble`.

- **Composición M22.** 55dd, fc6e y 243b reclaman fragmentos del mismo `FastMCP.instructions`.
  Decide si “exactamente seis” se refiere a seis IDs mínimos o si rechazaría instrucciones
  legítimas de terreno/efecto. No aceptes gates aislados que produzcan una composición imposible.

Emite de nuevo los siete bloques completos `ID / PLAN / SHA256 / VERDICT / FINDINGS / WHY` contra
los mismos hashes y termina en `GROUP_VERDICT`. No entregues un delta: esta segunda salida
sustituye íntegramente la primera.
