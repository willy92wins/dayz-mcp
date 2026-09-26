# Tanda in-game — una sola sesión (2026-08-19)

Todo lo que este proyecto tiene pendiente de juego, agrupado. El ciclo in-game es el recurso
más caro del pipeline (DZ-R5): esta lista existe para que no haya que volver a entrar mañana.

**Orden pensado**: lo que puede tumbar el despliegue va primero, y el canario antes que nada
que lo pueda contaminar.

---

## 0. Antes de arrancar el juego (5 min, sin juego)

1. **Promocionar el fencing al árbol.** El parche está en `delta.diff` de esta misma carpeta;
   los sha256 de destino, en `MANIFEST.md`. Ojo con `gui\layouts\mcp_dialog.layout`: **no se
   promociona** — la copia es anterior y lo está tocando otra sesión (7670 → 7696 → 7722 →
   7922 B en una sola noche, arreglando BUG-097 y BUG-100). Está fuera del parche a propósito.
2. **Suite completa en el árbol.** Base esperada: 1678 tests, 2 fallos (los dos del centinela
   de `MCPBridge.c`, rojos por diseño hasta el paso 3).
3. **Reiniciar el daemon** para que cargue el fencing (`bridge_status.daemon_modules.stale`
   debe quedar vacío). ⚠ Desde ese momento, cualquier juego lanzado a mano queda
   `LEGACY_UNBOUND` = **solo lectura**. Es el coste aceptado de la opción A (D-57).
4. **Desplegar el PBO**: `DayZ_MCP_fence_DCC8730F.pbo`, en esta misma carpeta — 205.008 B,
   sha256 `DCC8730FEB98FF2A`. Verificado: 13 entradas, todas byte-idénticas a su fuente,
   `&inst=` x4 dentro, `MCP_BRIDGE_VERSION` sigue en `"8"` (no hay bump, D-57).

   ⚠ **El anterior (`A9C00899`, 14:11) se retiró y se borró, y el motivo importa**: el
   script de empaquetado **sustituye** las tres fuentes Enforce enteras con las de
   `enforce-fencing\`, y esa copia se había forkeado antes de que la sesión de UI añadiera
   el tool `ui_reload_layout` a `MCPClientBridge.c` (árbol, 17:33, ~98 líneas).
   Desplegarlo habría **borrado ese tool del addon en silencio**. El delta del fencing va
   ahora **mergeado sobre la versión de hoy** (6 ediciones, +448 B), no sustituyendo.
   Si el árbol vuelve a moverse, **rehacer el merge, no sustituir el fichero**.

   ⚠ **Reconstruido el 19/08 a las 14:11 sobre el árbol de ese momento.** Este PBO **caduca
   solo**: la sesión de UI sigue trabajando en el mismo addon y cada despliegue suyo lo
   invalida. Ya pasó dos veces en un día — a las 03:41 por `mcp_dialog.layout` (7670 → 7922 B)
   y a las 13:10 por `MCPDialogController.c` (→ 18.116 B). Desplegar uno caducado **revierte
   su trabajo en silencio**, y el síntoma aparece en su siguiente vuelo como una regresión
   inexplicable.

   **Antes de desplegar, comprueba que sigue vigente** (compara lo que lleva dentro contra el
   árbol, entrada a entrada; las tres de Enforce deben diferir, que es el fencing sin
   promocionar, y ninguna más):

   ```bash
   tools/pbo_still_valid.py
   ```

   Dice `VIGENTE` o `** CADUCADO **` nombrando el fichero culpable, y coge solo el
   `DayZ_MCP_fence_*.pbo` más reciente de esta carpeta. Si sale caducado,
   `tools\build_fence_pbo.ps1` reconstruye mezclando el addon del árbol con las tres `.c` de
   `enforce-fencing\` (que viajan aquí porque son la única pieza del cambio que no está en el
   árbol), y `tools\verify_pbo.py` confirma las 13 entradas byte-idénticas con `&inst=` x4.
   Backup del vigente antes de sustituir. Detalle en `tools\README.md`.

## 1. El canario del fencing — el gate que decide (D-55.9)

**Criterio de aceptación**: `wrong_target_canary_count == 0`.
**Valor que lo refuta**: un solo frame del cliente intruso con la pose mandada.

Guion mecánico: `canario\canary_fence.py` (en esta carpeta), en tres fases. Captura por
`cmdline_match`, **nunca por PID** — DayZDiag tiene launcher-pid ≠ window-pid
(`mcp_capture.py:330`), y un canario que fotografíe la ventana equivocada da un falso
resultado en cualquiera de los dos sentidos.

1. `--phase before` → contadores y PNG de control del cliente registrado.
2. `--phase spawn` → copia el `dayz_mcp.json` del cliente a un perfil temporal y lanza un
   segundo DayZDiag contra él. El intruso presenta el **mismo** `instance`: es el caso §7.5.
   Si no llega a pintar en ~60 s, el canario es **INCONCLUSO**, no un PASS.
3. El orquestador manda 5 × `camera_set` en modo `lookat` a una pose inequívoca (cámara al
   cenit), con su lease.
4. `--phase after` → PNG de las dos ventanas, contadores, y mata **solo** el PID del intruso.

**Qué esperar si el fencing funciona** (esto es nuevo respecto de la spec, que se escribió
antes de que existieran los contadores):

| Señal | Valor esperado |
|---|---|
| PNG del intruso | sin la pose mandada |
| `binding_state` del cliente | `AMBIGUOUS` |
| mutación nueva | 409 `instance_ambiguous` |
| `unaccredited_mutation_enqueues` | sin incrementar |
| entregas al registrado | también 0 — con AMBIGUOUS **nadie** muta, y eso es correcto |

Que el registrado tampoco reciba **no es un fallo**: el diseño es fail-closed. Se anota
`delivered_to_target=0` como dato, no como defecto.

5. Tras el canario en verde: **re-congelar el centinela** `test_task9_spawn_phase_markers`
   (las dos mitades) sobre el `MCPBridge.c` ya gateado. Nunca antes: el rojo es la señal de
   que el PBO no ha pasado el gate.

## 2. Lo que solo se puede medir con el juego arriba (y ya que estamos)

- **Tasa de fallo del lookup TCP** (NUEVO-3). Sin caché, un lookup fallido deja ese tick en
  `instance_unattributed`: 200 ms de parpadeo a 5 Hz. Nadie ha medido si la tasa real es ~0 o
  si el fencing "va a rachas". Basta con mirar `unaccredited_polls_by_class` tras unos minutos
  de sesión normal.
- **`guard.snapshot` sobre un DayZDiag ajeno o elevado.** Con el fail-closed nuevo, un stamp
  ilegible **bloquea**. El test solo cubre `os.getpid()` (proceso propio, siempre legible).
  Este es el escenario que podría dejar el fencing bloqueando de más.
- **`JsonFileLoader<MCPConfig>` con el campo `instance` de más.** Si un PBO viejo no tolera el
  campo, deja de configurarse, con síntoma indistinguible de "el mod no cargó".
- **BUG-095** (`disconnected` en teardown): el fix está empaquetado desde hace días y **no es
  entregable** con `dayz_test_stop`, que mata el proceso. Necesita un fin de misión gracil.
- **`ui_dialog` fase 3**: aceptación con clicks FÍSICOS en dos resoluciones (`ui_click` no
  cuenta, §5 del plan). Protocolo en `plans\2026-08-18-ui-dialog-fase3-protocolo.md`.
- **`dayz_test_run` sin `@DayZ_MCP`**: devuelve `succeeded` con el puente muerto. Verificar si
  en esta máquina el mod entra por `extra_mods` o no entra.
- **`capture_screenshot` sin foco**: devuelve frame congelado; el fix medido es `-noPause`.
- **Verbos G0-G4** marcados ❓ en la spec, con el puente ya capaz: re-medirlos de una pasada.

## 3. Reglas de la casa que aplican a esta tanda

- **No se matan procesos DayZ ajenos** (D-49). El canario mata **solo** el intruso que él
  mismo lanzó, por PID exacto.
- El click físico ajeno existe: un `ui_dialog` que nadie tocó se cerró solo a los 28 s. Si
  aparece un resultado raro en la fase 3, sospechar del escritorio compartido antes que del
  controlador.
- `dayz_test_stop` no admite lease propio (`session_transition_conflict`): soltarlo antes.
- `ready:true` no significa cliente dentro de la partida; para verbos de cliente,
  `wait_for(players_at_least=1)`.
- El log de script del **cliente** no se vuelca en caliente: sus `Print` no existen hasta que
  cierra. El patrón fiable sigue siendo `[MCP-POC] result posted id=<n>` en el log del
  servidor, con `lookback_lines=200`.

## 4. Si el canario sale mal

No hay plan B improvisado: `wrong_target_canary_count != 0` significa que el fencing no cerca,
y el camino es volver a la copia con el escenario exacto que lo produjo. Las siete sondas de
la revisión (`probe_pid_cache`, `probe_oscillation`, `probe_ready_and_leak`, `probe_r3`,
`probe_r4`, `probe_r4b`, `probe_r5`) reproducen offline los nueve escenarios ya cubiertos;
el que falle in-game será uno que ninguna cubre, y esa es la información valiosa: capturarlo
con `--phase after` completo (PNGs + contadores) antes de tocar nada.
