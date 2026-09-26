# df93 — ronda 6: la puerta de admin tools pasa de EXIGIR VPP a VERIFICAR LO PEDIDO

**Decisión de Guillermo, 2026-09-06 ~20:00** («opción 1 me parece, un warn sería lo correcto»), tras
preguntar por qué el lote había decidido obligar a usar VPP: «yo no comenté de obligar a usar VPP …
otros servidores pueden preferir otras admin tools, debemos ser agnósticos a cuál use la gente».

## Cómo se coló el «obligatorio» (reconstruido con las fuentes, 2026-09-06 19:5x)

Nadie preguntó nunca «¿el MCP exige VPP?». El requisito entró en tres pasos, cada uno defendible por
separado; Guillermo solo vio «rechaza» como una cláusula dentro de pickers sobre qué lotes correr.

1. **La ficha** `fb-20260903-143856-df93` (03-sep, sesión Claude de LFQuad3, a partir de la queja «no
   había en ningún momento admin tools … no es la primera vez») proponía sembrar permisos, appendear
   `vppDisablePassword` y dejar VPP en el default «o al menos avisar cuando se pide un run interactivo
   sin él». Nunca dijo rechazar.
2. **La propuesta R3 de Reserva** (`reviews/2026-09-04-reserva/R3-df93-propuesta.md`, §Preguntas, l.174)
   convirtió «avisar» en «rechazar» («un aviso en stdout es lo que nadie lee») y lo acotó a runs
   interactivos (`mode ∈ {all, client}`, l.131). El picker del 05-sep 00:40 preguntaba «¿Y estos lotes
   offline más grandes?» y la opción recomendada llevaba dentro «Rechaza si falta @VPPAdminTools».
3. **El lote** (`lote-df93/GATES.md` P-D1) amplió el alcance de «interactivo» a «todo modo que arranca
   servidor» (`mode_starts_server`: `all` y `server`, también el headless sin cliente). La identidad por
   `publishedid = 1828439124` nació del hallazgo F-02 de Codex (seis policies llevan VPP por ruta
   absoluta y salían falsamente rechazadas). El cierre del lote midió que la puerta rechaza a 4 de 10
   policies selladas (DayZ_MCP, LF_VStorage, LFPowerGrid, SimpleGroup) y lo marcó «DECISIÓN para
   Guillermo»; el picker del 06-sep 17:41 lo presentó como «Ronda 4 + rechazar a todos (Recommended)»
   frente a avisar, parámetro explícito o aparcar. El picker del 06-sep 19:10 (destino y alcance) obtuvo
   «comentamos esto».

Fallo de proceso (G1): un riesgo que decide si la acción es correcta (¿el launcher es agnóstico?) se
resolvió por inercia dentro de opciones de lote, no como pregunta de diseño. Lección candidata al cierre.

## Hechos que sostienen la decisión

- Nada de esto está en producción: HEAD vivo `1cb90b5` tiene 0 referencias a `vpp` en `tools/dayz_mcp`;
  el addon del bridge (`addon/`) tiene 0: el MCP no necesita VPP para nada. VPP era la conveniencia de
  Guillermo en sus ciclos interactivos, heredada del default de `dayz-test.ps1:42`.
- La rama `work/df93-vpp-preflight` (`a7ddbac`) rechaza cualquier arranque de servidor sin VPP con
  `vpp_mod_not_requested` antes del lease (`native_launcher_transaction.py:455-456`, `:557-570`, `:600`)
  y declara que no hay bypass.
- La policy de DayZ_MCP lleva `default_base_mods: []` (sellada): quien quiera VPP por defecto en SUS runs
  lo pone ahí en el próximo rebuild, o en `extra_mods`. Es configuración por proyecto, no regla del
  launcher.

## Contrato nuevo (ronda 6)

| Caso | Antes (a7ddbac) | Ahora |
|---|---|---|
| Modo que no arranca servidor | limpio, sin lecturas | igual |
| Arranca servidor, sin candidato VPP en la lista efectiva | `missing: vpp_mod_not_requested` → rechazo | `warnings: vpp_mod_not_requested`, `hint=VPP_ABSENT_HINT`, **sin lecturas**, arranca |
| Arranca servidor, con VPP pedido | identidad / carpeta / raíz / cfg → `missing`; superadmins / credentials → `warnings` | **igual** (lo pedido se verifica; fail-closed sobre lo pedido) |

Frontera declarada: la puerta solo conoce VPP. Un servidor con otra herramienta de admin recibirá el
aviso `vpp_mod_not_requested` en cada arranque; es un token en la respuesta de `dayz_test_run`, no un
bloqueo. Un registro de herramientas de admin (o un campo `admin_tools` en la policy) sería otro lote,
solo si alguien lo pide.

## Ejecución

Implementa Grok (Cursor `cursor-grok-4.6-xhigh`, copia fiel de `wt-df93` en
`scratchpad/lote-df93-r6/ws`, brief `run1/brief.txt`, write-set `native_launcher_transaction.py` +
`test_vpp_preflight.py`); los comentarios de `dayz_test_tool.py` y de tres tests los reescribe el
receptor (`patch_r6_comments.py`); revisión cruzada Anthropic (Codex sin cuota hasta el 07-sep 04:27);
commit por pathspec en `wt-df93` sobre `a7ddbac`. Reserva avisada por mensaje a las 20:05 (parada
desde las 19:29).
