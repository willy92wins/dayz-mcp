# Prompt — fix DayZ-MCP window-grab selection (sub-sesión)

Generado 2026-06-15 desde la sesión del pipeline de animación de player (A6_SR2M).
Continúa el fix de `2026-06-14-prompt-fix-visual-capture.md` (aquél arregló los frames
STALE con `PrintWindow` PW_RENDERFULLCONTENT). Pegar en una sub-sesión (Cowork/Claude o
Codex CLI) abierta sobre el proyecto DayZ_MCP. Al terminar, volver con el bloque D.

```
===== PROMPT INICIO =====
TAREA (una sola): hacer que el window-grab del DayZ-MCP seleccione DETERMINÍSTICAMENTE
la ventana de render del CLIENTE diag (la que conduce `camera_set`), de modo que dos
capturas dentro de la MISMA sesión salgan de la MISMA ventana y geometría. Diagnosticar
primero qué ventana se está colando, luego fijar la selección. NO re-abrir el problema de
frames stale (ya resuelto con PrintWindow).

## Contexto del fallo (observado 2026-06-15, gate A6 conduciendo el bridge por PowerShell)
- En UN solo run del gate (mismo launch), dos grabs consecutivos (~50s aparte) salieron de
  ventanas DISTINTAS:
    * faithful -> PNG 1302x776  (= cliente lanzado con -window -x=1280 -y=720, + bordes)
    * probe    -> PNG  868x517  (otra ventana, más pequeña; tamaño que el selector viejo de
      spike0 ya marcaba como "la ventana EQUIVOCADA", ver spike0-window-enum.ps1:2-3)
  -> imposible comparar dos capturas (faithful vs probe) cuando vienen de ventanas/tamaños
  distintos. Una salió bien, la otra no.
- ROOT CAUSE candidato (verificar, no asumir): `mcp-grab.ps1` elige "la ventana de clase
  'DayZ' de MAYOR ÁREA entre TODOS los pids de DayZDiag_x64":
    * mcp-grab.ps1:138-139  $pids = todos los DayZDiag_x64 (server Y cliente son ambos DayZDiag)
    * mcp-grab.ps1:142       $render = pid in $pids AND class=='DayZ' AND class!='ConsoleWindowClass'
    * mcp-grab.ps1:143       $chosen = $render | Sort area desc | First
  La selección NO está fijada al pid del cliente. Si en el momento del grab la ventana 1302
  no es la de mayor área enumerable (minimizada / no visible / resize / aparece una 2a ventana
  'DayZ'), se cuela la 868.
- DATO ÚTIL ya verificado esta sesión (incorporar al proyecto): `camera_set` devuelve **ok=1
  solo cuando el cliente está IN-WORLD**; en la pantalla de carga/spawn devuelve ok!=1 (vacío/0).
  Es una señal fiable de "cliente renderizando el mundo" (la pantalla de carga es brillante y
  no-negra, así que engaña a nonBlackRatio/liveness; ok=1 no). Útil para validación content-aware.

## DIAGNÓSTICO PRIMERO (no tocar la selección hasta saber qué es la 868)
Con un cliente diag vivo y EN EL MUNDO, enumerar TODAS las top-level windows con pid+class+
rect+visibilidad, separando pid-server vs pid-cliente. Determinar qué es la 868x517:
  (a) la ventana de render del SERVER (DayZDiag -server, aunque se lance -WindowStyle Hidden),
  (b) una 2a ventana del cliente (popup/dialog), o
  (c) la MISMA ventana del cliente tras un resize (p.ej. al completar la carga del mundo).
spike0-window-enum.ps1 -All ya hace esta enumeración. Adjuntar el dump como evidencia.
Si es (c) un resize de la misma ventana, el fix es estabilizar el tamaño / aceptar tamaño
variable (la vista ES correcta); si es (a)/(b), el fix es fijar la selección al cliente.

## Carga inicial obligatoria (leer antes de tocar nada)
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\reviews\2026-06-14-prompt-fix-visual-capture.md  (el fix previo: PrintWindow, A1-A4)
2. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\spike0\mcp-grab.ps1  (grab canónico, SINGLE SOURCE OF TRUTH; selección :142-143; ya emite window:{pid,class,width,height})
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\spike0\spike0-window-enum.ps1  (enumerador + lógica -ExpectW/-ExpectH ya existente :73-77)
4. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\mcp_capture.py  (shell-out a mcp-grab; _run_window_capture ~:180)
5. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\HANDOFF.md  (estado vivo)
6. C:\Users\guill\OneDrive\Documentos\DayZ Projects\A6_SR2M_dev\_gate\gate-mcp.ps1  (consumidor real / test de aceptación end-to-end; GrabRaw llama a mcp-grab -Method auto)

## Criterios de aceptación (un solo deliverable)
- A1 (root cause): identificado con evidencia (el dump de ventanas) qué es la 868 y por qué se
  coló. Sin especular.
- A2 (selección determinista): `mcp-grab.ps1` puede targetear el cliente de forma fiable.
  Recomendado: añadir params `-TargetPid <pid>` y/o `-ExpectW/-ExpectH` (con tolerancia), y
  preferir la ventana cuyo pid==cliente AND class=='DayZ' AND tamaño≈esperado; mantener el
  fallback actual solo si no hay match. Es el SINGLE SOURCE OF TRUTH: NO forkear la lógica de
  grab; mcp_capture.py y los consumidores pasan los params nuevos. (El consumidor A6 ya conoce
  el pid del cliente — `$cli.Id` en gate-mcp.ps1 — y lo pasará; tu tarea es habilitar el param.)
- A3 (validación content-aware, si aplica): si tocas la validación de captura, asertar CONTENIDO
  (sujeto en frame), no solo non-black. La señal `camera_set ok==1` (in-world) es un buen gate
  previo al grab — documentarla/usarla.
- A4 (consumidor end-to-end): en UN run de A6_SR2M_dev\_gate\gate-mcp.ps1, los dos grabs
  (faithful + probe) salen de la MISMA ventana del cliente y del MISMO tamaño (~1302x776).
  Adjuntar AMBOS PNG como evidencia (mismo tamaño, ambos mostrando al player).

## NO (límites)
- NO toques el pipeline de animación (A6_SR2M\, A6_AnimRTTest\; de A6_SR2M_dev\ solo LEE el gate
  como consumidor). NO re-bakees nada del pipeline de anim. NO toques la cámara/ángulo del gate
  A6 (gate-mcp.ps1 CamDX/DY/DZ/LookDY) — eso lo ajusta la sesión de animación.
- NO re-abras el problema de frames stale (PrintWindow ya lo resolvió). NO uses MakeScreenshot (T165276).
- NO rompas los gates fase 0-2 (re-correrlos si tocas el bridge Enforce). NO mutes ~/.codex/config.toml.

## Restricciones
- R2 cite-then-verify firmas/API (Win32, py3d, Enforce). Captura es host-side (PowerShell);
  el grab vive en mcp-grab.ps1. Python stdlib-only para shims; el server corre en .venv-mcp.
- Si re-empaquetas el PBO del bridge: AddonBuilder -packonly -> P:\Mods\@DayZ_MCP, verificar por
  strings (no mtime). Probablemente este fix es 100% host-side PowerShell (sin tocar el PBO).

## Output esperado (bloques A/B/C/D al cerrar)
- A: archivos creados/modificados (paths absolutos).
- B: output real de los tests — el dump de ventanas (evidencia del root cause) + el A4 end-to-end
  con AMBOS PNG (faithful+probe, mismo tamaño, player visible). Output real, no parafraseado.
- C: el ROOT CAUSE con evidencia (qué era la 868 y el porqué de la selección errónea) y qué cambió.
- D: handoff para volver a la sesión de animación: cómo pasa el consumidor el pid/size del cliente
  a mcp-grab (firma exacta), y confirmación de que gate-mcp.ps1 ya saca ambos grabs de la misma ventana.
===== PROMPT FIN =====
```

## Receptor (al volver con el output de la sub-sesión)
- Bloque A → Read de cada path (verificar que existen).
- Bloque B → los dos PNG A4 deben ser del MISMO tamaño y ambos mostrar al player; verificar con PIL.
- Bloque C → root cause se incorpora a `pipeline-anim-player.md` §7.
- Bloque D → en gate-mcp.ps1, pasar `-TargetPid $cli.Id` (y/o -ExpectW/-ExpectH) en `GrabRaw` →
  re-correr el gate; ya con ventana consistente, ajustar el ÁNGULO de cámara (front/lateral) para
  encuadrar la mano de apoyo y cerrar el gate visual del SR2M.
