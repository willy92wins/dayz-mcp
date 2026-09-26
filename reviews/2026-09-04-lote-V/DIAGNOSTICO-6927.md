# Diagnóstico fb-20260904-114520-6927 — el lanzador tiró un servidor ajeno vivo en el 2302 (solo lectura, 2026-09-04 14:40)

**Ficha**: `dayz_test_run(project=DayZ_MCP, mode=all, port=2302, wait_for_box_s=600)` volvió `succeeded` en 13,3 s
sin esperar; a las 13:42:40 el servidor ajeno pid 22740 (6 min arriba, estable en 5245 MB) cayó y a las 13:43:00
apareció el del reportero. `session_status.box` decía libre (`runs=[97c0442d]` ya muerto, `foreign=[]`) y
`runs.json` tenía 127 runs con `owner_session_id=None` (generación del daemon `ad875657`, P6 estricto).

## Qué mira el daemon antes de lanzar (árbol vivo HEAD 2d069d2, código de lotes G/H)

- `process_lifecycle.py:1880-1888` (`start_run`): rechaza con `active_run_exists` + `audit_reason=foreign_diag_process`
  si `_foreign_diag_reason` ve un PID observado que no está registrado.
- `process_lifecycle.py:3313-3321` (`_foreign_diag_reason`) → `_diag_snapshot` (`:3091-3113`) → `self.diag_probe`, que
  `daemon.py:526-527` construye como `orphan_guard.snapshot_processes_by_name(["DayZDiag_x64.exe"])`: **enumera por NOMBRE
  de imagen y solo `DayZDiag_x64.exe`**.
- `process_lifecycle.py:3170-3222` (`_collect_probes`): `foreign` = procesos del sondeo cuyo PID no está registrado;
  `ports_in_use` = puertos **parseados del argv** de esos mismos procesos (`parse_dayz_launch_argv`, `:118`), no de la tabla
  de puertos del sistema. `_derive_box` (`:296-324`): `occupied = True if not scan_known else bool(runs or foreign)`.
- `wait_for_box_s` (lado tool) espera a que `session_status.box` esté libre; hereda el mismo sondeo.

## Por qué pudo salir `foreign=[]` con un servidor vivo

1. **La imagen no era `DayZDiag_x64.exe`** (p. ej. `DayZServer_x64.exe`, o un DayZDiag renombrado/copiado por el lote de
   otro proyecto — cabd describe exactamente un lanzador ajeno, `LFHeli_dev/tools/run_batch_f1.ps1`, sobre el 2302): el
   sondeo por nombre no lo ve, `scan_known=True`, `foreign=[]`, caja «libre». Es la hipótesis coherente con TODOS los datos
   de la ficha (el pid 22740 no tenía ficha ni aparecía en `foreign`).
2. Sondeo `unknown` no explica el caso: con `scan_known=False` la caja se declara OCUPADA (`_derive_box:317`), y la ficha vio
   `foreign=[]` con caja libre.

## Quién mató al pid 22740

En el lado Python del MCP no hay ningún sitio que termine un PID no registrado: las terminaciones pasan por
`native_process_guard.terminate(record)` sobre `ProcessRecord` propios (`process_lifecycle.py:2394,:2659,:2795`) y
`orphan_guard.try_reclaim_port` solo reclama puertos de `dayz_mcp` huérfanos (`orphan_guard.py:528-694`), no el 2302. El
único `Stop-Process` del árbol es `tools/run-step0.ps1:359-362` (herramienta manual, no la ruta de `dayz_test_run`).
Hipótesis abiertas: (a) el segundo servidor en el mismo puerto UDP hizo caer al primero (comportamiento del propio DayZ);
(b) la herramienta de la otra sesión lo cerró por su cuenta. La ficha no trae el RPT del 22740 ni su imagen: **sin ese
dato no se puede afirmar que el MCP lo matara**; lo que sí es medible es que el MCP lanzó encima de un puerto ocupado.

## Arreglo propuesto (dos piezas, ambas en ficheros del lote J del par: esperar a su integración)

1. **Ocupación por puerto, no solo por imagen**: en `_collect_probes` añadir un sondeo del SISTEMA para el puerto pedido
   (UDP/TCP LISTENING o bound en `port`, `port+1`…, con el PID que lo sostiene, como ya hace `orphan_guard` para el puerto del
   daemon) y tratar cualquier titular no registrado como `foreign` (con `port` e `image`), y ampliar el sondeo por nombre a
   las imágenes de servidor (`DayZServer_x64.exe`, `DayZ_x64.exe`). `start_run` ya rechaza cuando `foreign` no está vacío.
2. **`wait_for_box_s` espera, nunca limpia**: con la pieza 1, `session_status.box.occupied` pasa a ser verdadero y la espera
   FIFO se aplica sola; documentar en la descripción de `dayz_test_run` que un servidor ajeno en el puerto cuenta como caja
   ocupada aunque no tenga ficha.
Clase R9 (pérdida de datos ajena): auditoría con ≥2 ángulos (race del sondeo con caché de 1,5 s — `_BOX_OCCUPANCY_CACHE_S` —
y admin/reboot) antes de declararlo release-safe. Control positivo: reproducir con un `DayZServer_x64.exe` ajeno en el 2302 y
comprobar `active_run_exists` + `foreign=[{port:2302,…}]` y que `wait_for_box_s` espera.
