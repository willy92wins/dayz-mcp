# Prompt Codex — sesión 26 — fix BUG-008 (run-fase2.ps1 fuerza mod loose en vez del PBO)

Patrón: fix handoff (harness). Generado por Claude 2026-06-08. Copiar entre marcadores.

```
===== PROMPT INICIO =====

Tarea: arreglar BUG-008 en el harness de fase 2 — `run-fase2.ps1` fuerza `-mod=P:\DayZ_MCP` (source loose) si esa carpeta existe, ANTES de respetar `-ModSource`. Esa ruta loose define `DayZ_MCP` pero NO carga los scripts del módulo Mission del mod → produce el falso `Module: Mission 209x files; 443x classes`, sin `[MCP-POC] config loaded`, y el bridge no tickea. Esta sesión cubre ÚNICAMENTE ese fix del harness. NO toques el bridge (.c), ni `mcp_server.py`/`mcp_client.py`, ni la suite phase2.

## Evidencia (verificada host-direct por Claude)
- fase-1 PASS lanzó con `-mod=P:\Mods\@DayZ_MCP` (el PBO vía junction) → `Module: Mission 213x files; 463x classes`, bridge OK.
- fase-2 lanzó con `-mod=P:\DayZ_MCP` (loose) → `209x files; 443x classes`, sin `config loaded`, GATE=FAIL.
- Tú ya validaste que con el PBO carga bien: copia temporal `C:\tmp\run-fase2-use-modsource.ps1` → `Module: Mission 213x files; 468x classes` (463 + las 5 clases DTO de fase 2), `[MCP-POC] config loaded`, **GATE=PASS, overall_pass=true** (`C:\tmp\fase2-verdict.json`).
- Código fuente de DayZ_MCP NO cambió y es correcto; el problema es 100% del harness (selección de `-mod`).
- Bloque de la causa en `run-fase2.ps1` (lo leyó Claude): `$DeployModPath = Join-Path $WorkDriveRoot "DayZ_MCP"` y solo cae a `$ModSource` si ese path NO existe (y existe siempre) → siempre usa el loose.

## Carga inicial obligatoria
1. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase2.ps1  (a arreglar; ver la resolución de `$DeployModPath`).
2. C:\tmp\run-fase2-use-modsource.ps1  (tu copia temporal que YA funciona — referencia del fix correcto).
3. C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase1.ps1  (patrón probado: `-mod=P:\Mods\@DayZ_MCP`).
4. C:\Users\guill\ObsidianVault\AI\10_Projects\DayZ_MCP\bug-ledger.md  (BUG-008, :15).

## Fix
- Hacer que `run-fase2.ps1` lance contra el **mod desplegado `P:\Mods\@DayZ_MCP`** (el PBO, como fase 1) y/o **respete `-ModSource`** cuando se pase — NO forzar `P:\DayZ_MCP` loose. Replica lo que hace tu copia temporal que dio PASS.
- Decide tú si además conviene **reconstruir+desplegar el PBO** a `P:\Mods\@DayZ_MCP` al inicio del script (para que ediciones de source se reflejen sin pasos manuales). Si lo añades: AddonBuilder en el entorno del usuario falla por Steam-init; usa el mismo aislamiento que ya te funcionó (build en `C:\tmp` y copia del .pbo al deploy) o documéntalo como prerequisito. Si NO lo añades, deja claro en el handoff que el PBO debe estar desplegado/actualizado antes de correr.
- Mantén el resto del script igual (mission setup, cliente auto-conectado, fixture, suite phase2, cleanup).

## Restricciones
1. Solo `run-fase2.ps1` (y, si lo justificas, un paso de deploy del PBO). NO toques bridge .c, mcp_server.py, mcp_client.py, suite.
2. NO cambies la lógica de la suite ni el contrato fase 2.
3. PowerShell 5.1 (sin `&&`/`||`, sin ternario). Parsea el script tras editar (`[System.Management.Automation.Language.Parser]::ParseFile`).
4. Gate REAL = in-game (recuento de clases / `config loaded`), NO AddonBuilder.
5. NO rediseñes, NO te autorrevises (R21 aparte).

## Gate de verificación
Re-correr **sin copia temporal ni args especiales**:
`& "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\run-fase2.ps1" -WaitInGameSeconds 300`
Esperado: `Module: Mission ...; 468x classes` + `[MCP-POC] config loaded` + `GATE=PASS` + `overall_pass=true` en `tools\fase2-verdict.json`.

## Output esperado (A/B/C/D)
- Bloque A — qué cambiaste en `run-fase2.ps1` (+ paso de deploy si lo añadiste).
- Bloque B — resultado del re-run real (recuento de clases, `config loaded`, GATE, overall_pass) o, si tu entorno no lanza el juego, el parse OK + instrucciones para que el usuario lo confirme.
- Bloque C — confirmación de que `tools\run-fase2.ps1` ya no fuerza el loose; nota si añadiste deploy.
- Bloque D — handoff: marcar BUG-008 como fixed en `bug-ledger.md` (lo hace Claude en el cierre) + cómo se corre fase 2 de ahora en adelante.

===== PROMPT FIN =====
```
