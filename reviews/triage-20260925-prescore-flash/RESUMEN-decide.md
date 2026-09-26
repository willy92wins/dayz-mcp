# Decisión de triaje DayZ-MCP — 2026-09-25

Cobertura: **43/43** ids únicos de `inputs/propose.jsonl`; 0 omitidos. `vs_flash`: **10 OVERRIDE**, 33 ACCEPT.

| Clase | Filas |
|---|---:|
| X | 25 |
| C1 | 0 |
| C2 | 3 |
| C3 | 15 |
| C4 | 0 |

## X-YA_RESUELTO — candidatos de cierre

- `fb-20260924-012921-296b`: El preflight de Steam lee la clave REAL (WMI) pero el cliente DayZ lee la copia virtualizada de la app: verde falso
- `fb-20260924-011620-678b`: DayZ no arranca: error «Steam not running» al lanzar (2026-09-24); diagnosticar el preflight de Steam

`296b` se normalizó de Flash `C3/X-YA_RESUELTO` a **`X/X-YA_RESUELTO`** por el merge #91 (`78cc50a`); el buzón sigue abierto. `678b` queda candidato adicional por #90/#91. Ninguno se resolvió ni se llamó a `pipeline_resolve`.

## C3-C4 — candidatos; no despacho

- `fb-20260924-235534-df3a` (C3, EVIDENCIA): object_anim en un coche: la fase escrita vuelve a 0 en menos de 0,3 s
- `fb-20260924-235528-0878` (C3, CAMBIO): vehicle_prepare_fixture da bad_args con argumentos válidos: la herramienta y el PBO desplegado de @DayZ_MCP no casan
- `fb-20260924-235524-e7ef` (C3, CAMBIO): Claude Code nunca ve los verbos de juego: el aviso tools/list_changed tras el lease no le llega
- `fb-20260924-130252-6084` (C3, EVIDENCIA): Steam not running: la app ve una copia congelada de Steam\ActiveProcess; WMI lee el real (medido)
- `fb-20260924-010108-b1a7` (C3, CAMBIO): Recuperar una sesion MCP sin reiniciar la app: hot reload sin lease fantasma y verbos sin reabrir el cliente
- `fb-20260924-003722-0c27` (C3, CAMBIO): server_reload deja al worker nuevo con un lease heredado sin operacion: todo da session_transition_conflict
- `fb-20260921-121956-2569` (C3, CAMBIO): LF_VStorage via dayz_test_run: build=true rechazado por el gate nativo; wait_for no ve el run
- `fb-20260920-230648-99db` (C3, CAMBIO): Gate: detect a stale launcher seal at merge or daemon start, and name the failing key in the error
- `fb-20260915-151528-6ed1` (C3, CAMBIO): Árbol vivo de DayZ_MCP_dev: 9 ficheros versionados con copia CRLF; dos van embebidos en el launcher sellado
- `fb-20260915-132202-2143` (C3, EVIDENCIA): vehicle_get_in_client: tras minutos sin conducir, el jugador vuelve al punto de teleport y todo get-in da not_seated
- `fb-20260915-111038-75e7` (C3, CAMBIO): f298 confirmado por el dueño: DayZ roba el foco en cada arranque, también en segundo plano, y a veces el MCP
- `fb-20260915-105311-fade` (C3, EVIDENCIA): f298 medido con sonda pasiva: el join no confina el cursor; los DayZ toman el foco 1-2,5 s al arrancar
- `fb-20260915-014753-ba11` (C3, EVIDENCIA): Canario BUG-096 (2026-09-15): intruso con la misma cuenta Steam se queda en el menu; killed_verified falso negativo
- `fb-20260913-143454-8bc6` (C3, CAMBIO): Un coche de world_spawn que sobrevive a su run vuelve por persistencia y object_delete ya no lo alcanza
- `fb-20260910-102449-f47b` (C3, CAMBIO): camera_set deja el render del cliente congelado y restore_gameplay no lo recupera ni lo detecta

## Límite de esta pasada

G-CAL **no durable-green**: las 43 filas tienen `confianza=DUDOSA`; no hay dispatch, Opus, fix, append a `triage.jsonl` ni mutación del buzón. `requiere_autorizacion=false` se emite por el contrato solicitado para este hop y no concede permiso para aterrizar contribuciones o cambiar herramientas.

Se verificó el plan v2.2 y una copia legible del checkout en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev`, cuyo HEAD es `78cc50a`. La unidad `P:` no estaba montada; no se ejecutaron gates nuevos ni una prueba in-game.
