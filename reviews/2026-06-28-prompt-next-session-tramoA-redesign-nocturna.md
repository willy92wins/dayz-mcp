# Prompt next-session NOCTURNA AUTÓNOMA — Rediseño Tramo A (Fase 5 DayZ-MCP)

> Patrón: next-session bootstrap + orquestación autónoma. Generado 2026-06-28 al cierre de la
> sesión S0. Diseñado para correr **de noche, sin humano**: máxima autonomía, decisiones
> auto-resueltas, STOP+report en gates duros (NO preguntar — el usuario duerme).

```
===== PROMPT INICIO =====

Sesión NOCTURNA AUTÓNOMA. DayZ-MCP Fase 5 — REDISEÑO de Tramo A + avanzar lo máximo posible tú
solo (investigar → diseñar → Codex implementa → gate in-game), orquestando con workflows y Codex
headless. El usuario está dormido: NO uses AskUserQuestion. Donde normalmente preguntarías, elige
la opción que la evidencia favorezca, anótalo, y sigue. Solo PARA (no preguntes) en los gates duros
de abajo y déjalo en el handoff de la mañana.

CONTEXTO (qué pasó y por qué este rediseño)
El gate S0 (2026-06-28) PROBÓ in-game que el approach original de Tramo A es inviable tal cual:
`vehicle_enter` (server `StartCommand_Vehicle`) sienta al player SOLO server-side; el CLIENTE nunca
obtiene `GetCommand_Vehicle()` ni el ownership del coche (F1, medido ×6 in-game). Por eso
`drive_probe_client` aborta con `not_seated`. Investigación de source (2 jueces adversariales,
unified-root-holds): el coche es un Pawn; el throttle→física es nativo y lo aplica el OWNER; un
coche sin cliente-dueño lo simula el server (de ahí F2: el server movió un PHYSICS car = artefacto
single-box, NO un plan-flip). **El plan se sostiene; Tramo A sigue siendo necesario, pero su
PRECONDICIÓN cambia: el CLIENTE debe tomar ownership él mismo (get-in client-side), no depender del
asiento forzado server-side.**

CARGA INICIAL MÍNIMA (en este orden; tiene TODO lo verificado, no re-derives)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-28-prompt-impl-fase5-s0-codex.md
   (EL doc maestro: handoff S0 + review 3-lentes + resultado del gate F1/F2 + investigación F2 con
   la cadena de citas vanilla + decision tree del gate + la invariante candidata + infra reusable.)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-drivability-autonoma.md
   (plan v2.1: Tramo A §3, fail-closed §5, gates §7, plan B HumanInputController §8.)
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c
   (donde va el seat client-side: dispatch :444, drive_probe_client PREP :646, SuppressGameplay/Restore.)
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md (LIVE-STATE).

HECHOS VERIFICADOS (NO re-derivar; citados en el doc maestro)
- F1: server-forced seat NO da ownership ni vehicle-command al cliente (in-game).
- F2: server `SetThrottle` mueve un coche SIN dueño (artefacto single-box); `IsServerOrOwner`
  (carscript.c:3222-3231) solo gatea teardown (carscript.c:822/850/986), no el throttle.
- Get-in real: `ActionGetInTransport.Start()` (método compartido cliente+server, actiongetintransport.c
  :82-98) llama `m_Player.StartCommand_Vehicle` en el Human DEL CLIENTE + reserva asiento por juncture
  (:141-161). La transferencia de ownership es proto-native (no hay SetNetworkOwner en script).
- Infra lista: `tools\run-s0-gate.ps1` (launcher contra el daemon :8765, sin time-freeze, deja procs);
  driver raw `scratchpad\s0_gate_driver.py`; daemon con whitelist `drive_probe_client` nuevo; AddonBuilder
  `-packonly` (si el copy final falla, copiar el temp PBO a mano a P:\Mods\@DayZ_MCP\Addons).

NORTH STAR (lo que sería ÉXITO por la mañana)
Que el CLIENTE tome ownership del coche in-game (`IsOwner()==true` client-side + `GetCommand_Vehicle()`
no-null) y `drive_probe_client` mida por fin el `pos_delta` owner-side → la hipótesis original de S0
(owner-authority) TESTEADA. PASS = el coche se mueve owner-side (pos_delta>1.0 Y speedo>0 Y engine_on).

PLAN DE ORQUESTACIÓN AUTÓNOMA (avanza por todas las fases que la evidencia permita)

FASE A — Investigar (workflow de lectura de source, R2 cite-then-verify): ¿puede MCPClientBridge
hacer que el cliente tome ownership?
  A1. ¿Basta un `StartCommand_Vehicle(GetGame().GetPlayer(), car, crew, seatAnim)` client-side para
      `IsOwner()==true` + `GetCommand_Vehicle()` no-null? ¿O hace falta la reserva por juncture
      (`AddInventoryJunctureEx` / `InventoryLocation.SetVehicle`) que hace `ActionGetInTransport`?
      Estudia ActionGetInTransport COMPLETO (Start/OnUpdate/OnStartServer/juncture), transport.c
      (CrewGetIn proto native), human.c (StartCommand_Vehicle). Verifica con jueces adversariales.
  A2. Decisión auto-resoluble: si source favorece el seat client-side mínimo → diseña eso. Si
      claramente necesita el juncture → diseña el get-in client-side por juncture. Documenta la
      elección y su evidencia.

FASE B — Confirmación barata de F2 (plegar al diseño): añadir captura de ownership del coche
  (`IsOwner`/`IsAuthorityOwner`/`GetOwnerIdentity`) al server `drive_probe` (MCPBridge) para que el
  mecanismo del artefacto single-box quede confirmado por LECTURA directa (predicción:
  `IsAuthorityOwner==true` + `GetOwnerIdentity==null` mientras el server lo mueve). Cierra el gap de
  inferencia nativa del veredicto F2.

FASE C — Diseñar (plan delta): redacta el rediseño de Tramo A como delta del plan (append al plan o
  nuevo doc en plans/): precondición de ownership client-side, el verbo/mecanismo de seat client-side,
  y luego los 5 verbos de control gateados a `IsOwner()` client-side. Marca [EXACT]/[DESIGN], citas
  path:line. (Si el diseño es grande/multi-archivo → es el límite "cambio grande": deja el plan
  redactado y PARA antes de implementar, déjalo para revisión; los cambios pequeños/reversibles del
  spike sí impleméntalos.)

FASE D — Implementar (Codex headless, autónomo): handoff scope-bounded para el spike de seat
  client-side + la lectura de ownership de Fase B. Lanza `codex exec` headless (patrón probado
  2026-06-28: smoke "READY" primero, stdin con EOF `codex exec - ... < prompt.txt`, `-s workspace-write`,
  `-C "C:\Users\guill\OneDrive\Documentos\DayZ Projects"`; waiter host-direct PowerShell, NO Monitor
  bash). Receptor: verifica bloques A/B/C/D, re-corre `.venv-mcp\Scripts\python.exe -m unittest discover
  tests`, lee el diff. Revisión adversarial offline (workflow) ANTES del rebuild (R5).

FASE E — Gate in-game (autónomo; la infra está lista):
  - EXCLUSIVIDAD PRIMERO (box único + contendido por otras sesiones Cowork, p.ej. A6_SR2M): mata
    DayZDiag residual, vigila ~40s un relaunch ajeno. **Si hay un DayZServer_x64/DayZ_x64/DayZDiag
    ajeno con otro @Mod → NO lo evictes: hazlo constar y PARA la fase in-game**, haz solo las fases
    offline (A–D) y deja el gate para la mañana.
  - Build+deploy: AddonBuilder `-packonly`; si el copy final falla, copia el temp PBO
    (`%TEMP%\DayZ_MCP.pbo`) a `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo`; verifica que el símbolo nuevo
    está en el PBO (Select-String).
  - Si tocaste `loopback.py`: reinicia el daemon (mata el python `-m dayz_mcp ... --port 8765` SIN
    `--client`; llama un MCP tool para respawn; verifica whitelist por enqueue raw).
  - Lanza `run-s0-gate.ps1`; espera peers polleando (`/status`) + spawn marker; conduce con un driver
    raw (spawn CivilianSedan → SEAT CLIENT-SIDE nuevo → confirma `IsOwner` client-side → drive_probe_client
    "" y "suppress" → lee pos_delta/speedo/engine/ownership trio).
  - Veredicto por el decision tree del doc maestro. Teardown (mata DayZDiag, libera el box).

GATES DUROS (PARA + reporta, NO iteres a ciegas, NO preguntes)
- Box contendido por otra sesión → solo fases offline, PARA in-game, repórtalo.
- Tras el seat client-side, si el cliente AÚN no toma ownership (`IsOwner` false / `GetCommand_Vehicle`
  null) → PARA, captura el trío + el estado exacto, reporta el fallo preciso (es en sí un hallazgo clave;
  probable que haga falta el juncture o algo nativo).
- `pos_delta≈0` con `IsOwner==true` client-side → el throttle owner-side NO mueve el coche → PARA,
  plan B = `HumanInputController` (plan §8), reporta con el dato.
- 3 rebuilds in-game sin progreso → PARA, cambia de estrategia, reporta.

REGLAS: R2 cite-then-verify; implementación = Codex (scope-bounded); R5 agrupa tests in-game; R7/R8
invariantes+crash/owner walk; box-exclusividad (NUNCA evictar otra sesión). Ultracode ON: orquesta con
workflows y verifica hallazgos adversarialmente; el coste de tokens no es restricción.

ENTREGABLES (handoff de la mañana)
1. Rediseño de Tramo A (plan delta) con la precondición de ownership client-side.
2. Spike implementado (Codex) + receptor + revisión.
3. Veredicto del gate in-game con evidencia (o progreso offline + por qué el in-game quedó bloqueado).
4. Handoff actualizado + lecciones/refuerzos propuestos para cuando el usuario despierte.

PROHIBIDO: evictar otra sesión Cowork del box; iterar a ciegas; implementar tú el bridge (es Codex);
tocar producción sin el flujo de gate; declarar PASS sin el triple criterio (pos_delta>1.0 + speedo>0
+ engine_on) y owner-side confirmado.

===== PROMPT FIN =====
```

## Notas (no van en el prompt)
- Arrancar la sesión nocturna en ventana donde el box esté libre (idealmente ninguna otra sesión
  Cowork con DayZ). Si hay contención, las fases A–D (offline) avanzan igual; solo la E se bloquea.
- El daemon :8765 puede haberse reciclado entre sesiones; el prompt cubre el reinicio si cambia
  `loopback.py`.
