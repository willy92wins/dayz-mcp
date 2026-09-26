# Prompt next-session — Fase 5 S0 (arranque sesión nueva Cowork/Claude)

> Generado 2026-06-28. Pegar al abrir la sesión nueva. El hook SessionStart ya inyecta el
> LIVE-STATE de HANDOFF.md; esto lo complementa con el objetivo y la carga mínima.

```
===== PROMPT INICIO =====

Sesión nueva. DayZ-MCP — **Fase 5 (drivability autónoma de coches), implementación de S0**.

El hook SessionStart ya te inyectó el LIVE-STATE de HANDOFF.md. Esto lo complementa.

CARGA INICIAL MÍNIMA (en este orden, no abrir más todavía)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\2026-06-28-fase5-drivability-autonoma.md
   (plan v2.1 APPROVED — el contrato; §2 = S0, §11 = citas verificadas, §12 = changelog R22).
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
   (el drive_probe server a PORTAR: ProcessDriveProbe :1733, fases Ignite/Drive/Sample :1818-1898).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c
   (el peer cliente DONDE va S0: dispatch else-if :427, OnTick :158, GetGame().GetPlayer() :624/:967).
NO releas los reviews R22 round-1/round-2 salvo contradicción concreta — el plan v2.1 ya los integra.

OBJETIVO DE LA SESIÓN
Producir el handoff de IMPLEMENTACIÓN de S0 para Codex (patrón implementation-handoff,
scope-bounded) y, cuando Codex lo implemente, correr el **gate S0 in-game** (tú conduces el MCP).

S0 = añadir UN cmd `drive_probe_client` al peer cliente (`MCPClientBridge.c`) que:
(a) resuelve el coche por `GetGame().GetPlayer().GetCommand_Vehicle().GetTransport()` → CarScript;
(b) reusa las fases IGNITE→DRIVE→SAMPLE del `drive_probe` server (EngineStart → ShiftTo FIRST →
    SetThrottle → mide pos_delta + GetSpeedometer);
(c) reporta ownership: `IsOwner()` (pawn.c:194), `GetOwnerIdentity()` (pawn.c:209),
    `GetNetworkID()` (object.c:815);
(d) prueba CON y SIN `SuppressGameplay` y registra cuál mueve el coche.

GATE S0 (in-game, Claude conduce, CivilianSedan): `vehicle_enter` → `drive_probe_client` →
`pos_delta>1.0` tras 2 s throttle ⇒ PASS (hipótesis owner se sostiene → seguir a Tramo A).
`pos_delta≈0` ⇒ STOP; el trío IsOwner/GetOwnerIdentity/net id clasifica la causa (ownership no
transferido al cliente MCP vs otra). Reportar con evidencia, NO iterar a ciegas.

YA CERRADO EN LA SESIÓN ANTERIOR (NO re-litigar)
- Plan v2.1 R22-approved (round-1: 9 hallazgos; round-2: 3 doc-fixes). Diseño de los verbos,
  deadman, component-obligatorio, fixtures: cerrados.
- product-spec grupo G (G0-G2) añadido ANTES de S0.
- Owner-authority es HIPÓTESIS, no hecho — S0 la prueba. NO asumir que mueve.

REGLAS
- R2 cite-then-verify cualquier API/path antes de citarla.
- Implementación = Codex (handoff scope-bounded). NO codees tú el bridge.
- R5: agrupa el test in-game; S0 es 1 rebuild, no iterar entre fixes.
- R8: recorrer crash/owner scenarios antes de declarar S0 listo para gate.

PROHIBIDO EN ESTA SESIÓN
- Implementar el Tramo A (los 5 verbos) ANTES de que S0 pase. S0 es gate duro.
- Tocar el `drive_probe` server, la cámara, el harness, los 8 handlers existentes.
- Re-litigar el diseño del plan v2.1.

ENTREGABLE
1. Prompt de implementación de S0 para Codex
   (`DayZ_MCP_dev/reviews/2026-06-28-prompt-impl-fase5-s0-codex.md`).
2. (tras Codex) gate S0 in-game + veredicto PASS/STOP con evidencia (pos_delta + ownership).
3. Si S0 PASA → nota para promocionar la invariante owner-authority a la skill `dayz-vehicles`.

===== PROMPT FIN =====
```
