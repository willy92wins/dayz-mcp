# Prompt — R21 review de `drive_ladder.py` (Codex)

Pattern: R21 code/infra review (read-only, adversarial). Generado 2026-06-28.
Componente: `dayz-mcp-verify/references/drive_ladder.py` (orquestador de la escalera de aceptación, Fase 5).
Receptor: Claude verifica cada hallazgo contra el `file:line` real antes de aceptarlo, luego triaja a `bug-ledger.md`.

Lanzamiento (el orquestador vive bajo `.claude\skills\`, los gate drivers/bridge bajo el árbol del proyecto):
`codex -C "C:\Users\guill\OneDrive\Documentos\DayZ Projects" --add-dir "C:\Users\guill\.claude\skills\dayz-mcp-verify" -s read-only`

---

```
===== PROMPT INICIO =====

Tarea: R21 (revisión adversarial, SOLO-LECTURA) del orquestador `drive_ladder.py` de DayZ-MCP
(Fase 5, skill `dayz-mcp-verify`). Adversarial = intenta demostrar (a) que un rung da un PASS FALSO
o un FAIL FALSO (mapeando a un fix equivocado), y (b) que el orquestador se romperá o mentirá cuando
ESTRENE un coche real (SUB_BRZ) in-game. NO es un rewrite: veredicto + hallazgos con file:line.

CARGA INICIAL OBLIGATORIA (≤7, completos, en orden)
1. C:\Users\guill\.claude\skills\dayz-mcp-verify\references\drive_ladder.py
   (EL CÓDIGO BAJO REVISIÓN: orquestador R1→R6 vía raw /enqueue+/await contra el daemon :8765).
2. C:\Users\guill\.claude\skills\dayz-mcp-verify\SKILL.md
   (SOLO las secciones "DRIVABILITY: verbos de conducción" y "ESCALERA DE ACEPTACIÓN" = el CONTRATO
   que el orquestador debe implementar fielmente; el resto de la skill es contexto, no lo audites).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tramoA_gate_driver.py
   (la primitiva VERIFICADA Daemon/extract_pos/wait_ready/verdict que el orquestador INLINEA — compara
   por drift R7: await remove=1, orden de extract_pos, default de readiness).
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tramoA_verbs_gate.py
   (la secuencia de conducción R4/R5 que YA gateó PASS in-game — los result-fields que accede son
   ground-truth: seated/is_owner/speedo_max/pos).
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tramoB_getin_gate.py
   (la secuencia raycast→get-in R3 que YA gateó PASS in-game; OJO: gateó "componente-pasajero
   available=1, componente-CONDUCTOR unreachable" — guárdalo para la dimensión 1).

CONSULTAR SOLO SI UN HALLAZGO LO REQUIERE (R2 cite-then-verify, no leer de entrada):
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPBridge.c
  (first_block literales :589-708; result-fields del get-in y del drive probe server).
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPClientBridge.c
  (verbos cliente y result-fields: el verbo R4 `vehicle_get_in_client`→`ProcessVehicleGetInClientPrep:919-1029`
  = seat+`OnDebugSpawn`+ownership, NO RestoreGameplay; `engine_set`/`vehicle_control`/`vehicle_telemetry`;
  `cam_mode="free"`→`DisableSimulation` :1398. OJO R2.5: el `:1042 RestoreGameplay` es del job DISTINTO
  `drive_probe_client`, NO del verbo get-in — ver CONTEXTO).
- C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP\scripts\5_Mission\MCPMessages.c (DTOs).

CONTEXTO (lo que el orquestador AFIRMA — para que lo ataques)
- Conduce R1 spawn → R2 render(raycast) → R3 get-in diagnóstico → R4 self-seat → R5 drive → R6 steer,
  parando en hard-fails (R1, R4) y recogiendo soft-fails (R2, R3, R6) en una pasada, y emite verdict.json
  con el rung, el first_block y el fix mapeado de la taxonomía SUB_BRZ.
- Habla RAW al daemon (no por las tools MCP de server.py): los result-shapes vienen del BRIDGE, así que
  el ground-truth de los campos son los gate drivers (4,5) y el bridge .c, NO server.py.
- NO está gateado como CADENA R1→R6 end-to-end: cada rung reusa una secuencia de verbos gateada por
  separado in-game; la cadena entera es el propio test que la escalera existe para correr. Esta review
  es el filtro OFFLINE antes de estrenarlo (cazar bugs lógicos sin quemar un ciclo in-game).
- R2.5 YA CORREGIDO (BUG-040), no lo re-flaggees: la prosa que decía "el verbo get-in auto-restaura
  gameplay" era imprecisa; `vehicle_get_in_client` NO llama RestoreGameplay (eso es del job
  `drive_probe_client`). La garantía R2.5 es SOLO la regla no-freecam (`lookat` deja la sim viva, `free`
  la mata :1398). El orquestador `drive_ladder.py` NO usa `camera_set` (deja la captura visual al agente)
  → es inmune a R2.5. Verifícalo; si encuentras que SÍ toca la cámara, ESO sí es hallazgo.

DIMENSIONES DE LA REVISIÓN (adversarial — cada una exige un modo de fallo, no un "se ve bien")
1) Soundness del gate — busca el PASS falso y el FAIL falso. Ataca al menos:
   - R3: PASS = `any(s["available"] for s in crew_seats)`. ¿FALSO-PASS un coche donde un asiento
     PASAJERO está available pero el CONDUCTOR (crew_index 0) está unreachable/blocked? (tramoB demostró
     ese caso exacto.) El criterio de producto es "un humano entra al asiento de CONDUCTOR" — ¿el código
     comprueba seat 0 en concreto, o cualquiera?
   - R5: PASS = `pos_delta>1.0 AND speedo>0`. ¿Un coche con drivetrain muerto puede satisfacerlo (rodar
     por gravedad / inercia residual)? ¿`speedo>0` lo cierra o no?
   - R5 desambiguación obstáculo: `blocked = speedo>0 or gear>1`. ¿Un fallo de drivetrain real puede
     mostrar speedo>0/gear>1 transitorio → mal-etiquetado "obstáculo" (FAIL falso → fix equivocado)? ¿Y
     un obstáculo desde el frame 0 con speedo==0 → mal-etiquetado "drivetrain"?
   - R2: `solid>=6 de 12`. ¿6/12 está justificado? ¿Un coche winding-inverted o con caras faltantes
     llega a ≥6 (PASS falso)? ¿Uno sólido a una altura mala falla por debajo de 6 (FAIL falso)?
   - R1: `spawn.get("found", 1)` defaultea `found` a 1 si AUSENTE. Si el bridge omite `found` en un spawn
     fallido, ¿enmascara el fallo (PASS falso)? ¿`found` siempre está presente?
   - overall_PASS = `all(rungs objetivos)` con soft-fails registrados como PASS:False pero la pasada
     sigue. ¿overall_PASS refleja bien una pasada con soft-fail, y el journal deja inequívoco el rung que falló?
2) Corrección del código (R2 — result-fields vs los gate drivers verificados + bridge). Cada `res.get(...)`
   que lee el orquestador: ¿coincide con lo que leen los gate drivers (4,5) y lo que POSTea el bridge?
   Marca cualquier clave que el orquestador INVENTE y los gate drivers no usen (`found`, `gear`, …).
   Orden: ¿`vehicle_control` exige `engine_set start` antes? Si R4 hizo timeout, ¿las llamadas R5
   (`engine_set`/`vehicle_control` resuelven el coche por ResolveOwnedCar) erroran limpio o leen basura?
3) ¿Se romperá/mentirá al estrenar SUB_BRZ? Traza dato→consumo→efecto:
   - R6 arco: el signo del cross-product `hx*dz - hz*dx`, ¿mapea bien izquierda/derecha en el plano XZ de
     DayZ (Y arriba)? ¿`steer_left_sign=-1` es el default correcto? (R6 es INFO, no PASS — riesgo bajo, pero
     el cálculo puede mentir.)
   - `_timeout`: NO se chequea en `engine_set`/`vehicle_control`/`vehicle_telemetry`. Un timeout a mitad
     de secuencia → lecturas downstream stale → verdict falso. ¿Dónde muerde?
   - Tiempos: `drive_s` (5s) vs `hold_ttl_s` (12s) y el deadman del held-state. ¿El coche conduce TODA la
     ventana de medida, o el held-state expira a mitad? ¿R6 re-arma control tras el deadman de R5?
   - Re-run: si el player ya está sentado de una pasada previa, ¿`wait_ready`/R4 se comportan?
4) Invariantes / acoplamiento (R7): el Daemon/extract_pos/wait_ready INLINEADO, ¿driftó del original
   verificado tramoA_gate_driver.py (await remove=1, orden de extract_pos pos_real/pos/state, ok-default
   de readiness)? `DEF_KEYFILE` hardcoded.
5) Portabilidad / higiene: keyfile Windows hardcoded, `--journal` default a cwd (¿dónde cae verdict.json?),
   encoding, las claves raw `from`/`to` de scene_raycast.

CRITERIO DE SEVERIDAD (no inflar):
- FAIL (P1 bloqueante): PASS/FAIL falso que mandaría a un fix equivocado, o un crash de la pasada.
- WARN (P2): robustez/edge (timeout no chequeado, tiempos frágiles, re-run).
- NIT (P3): higiene (hardcodes, encoding, naming).

BOUNDARIES: SOLO-LECTURA, findings NO fixes. NO modifiques archivos. NO reescribas el orquestador. NO
redesign. NO audites el bridge Enforce ni los verbos MCP (ya gateados in-game) — solo el orquestador
Python `drive_ladder.py` y su contrato con las dos secciones citadas de la skill. NO audites la taxonomía
SUB_BRZ en sí (vive en `dayz-vehicles`, ya verificada) — solo si el MAPEO fallo→fix del orquestador la cita
mal. R2 cite-then-verify por hallazgo: lee el .c / gate driver real antes de afirmar que un campo no existe.

OUTPUT: un archivo
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-28-drive-ladder-review-codex.md
con: (Bloque A) resumen ejecutivo = VEREDICTO (SOUND / SOUND-with-fixes / UNSOUND); (Bloque B) matriz de
hallazgos (ID | file:line | severidad | resumen | fix sugerido); (Bloque C) hallazgos detallados, cada uno
con su cita file:line verificada y el modo de fallo (PASS falso / rompe-al-estrenar); además, si un hallazgo
del código nace de que la PROSA de la skill (ESCALERA) es ambigua, anótalo como fix del runbook, no solo del
.py; (Bloque D) "lo que NO pude verificar" + próximo paso. Append una línea con timestamp + status=open a
C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\codex-review-inbox.md.

===== PROMPT FIN =====
```
