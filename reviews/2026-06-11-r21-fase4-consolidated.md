# R21 estructural Fase 4 — consolidado Claude + Codex (2026-06-11)

> Mitades independientes: Claude `2026-06-11-r21-fase4-claude.md` (0 P1 · 5 P2 · 9 P3) y
> Codex `AI/10_Projects/DayZ_MCP/reviews/2026-06-11-r21-fase4-codex.md` (3 P1 · 1 P2).
> Cross-verificación: cada hallazgo de la otra mitad re-verificado contra el código fuente
> (citas línea a línea) antes de consolidar. Veredicto consolidado: **needs-fix — 3 P1 ·
> 4 P2 · 8 P3** (pendiente adjudicación X.5 con usuario).

## Pago de la redundancia (por qué R21 dobla)

- **Codex cazó, Claude no**: el gate de versión falta en la ENTREGA (`/poll` drena la cola
  incondicionalmente — CONS-P1-1, la mitad Claude solo vio la sub-parte "versión pegajosa");
  `install-mcp.ps1` no puede expresar `--require-version` pese a que el plan §5.2/§6 exige
  que el install final lo active (CONS-P1-3).
- **Claude cazó, Codex no**: bypass de allowlist+audit de exec vía el ingress `/enqueue`
  (CONS-P2-1 — la cobertura de Codex afirma "sentinels -999.0 no colisionan", refutado vía
  ese mismo ingress, ver P3-5); `year` sin rango en ambas capas (CONS-P2-2); install
  cwd-dependent (CONS-P2-3); 8 P3 operativos.
- **Convergencia exacta** (hallado por ambos, independiente): audit de exec no fail-closed
  (orden enqueue→audit + excepción de I/O + empty-expr sin auditar) — CONS-P1-2.

## Matriz consolidada

| ID | Sev | Origen | Resumen | Cita clave |
|---|---|---|---|---|
| CONS-P1-1 | **P1** | Codex 001 ⊃ Claude 001 | Handshake fail-open en dos puntos: (a) `/poll` entrega comandos ya encolados sin validar el `ver=` de ESE poll (un poll `ver=999~…` o sin `ver=` recibe la cola igual); (b) poll sin `ver=` no borra la versión registrada → downgrade mantiene `version_state=ok` y bypassa `--require-version` | `loopback.py:92-105` (record_poll drena incondicional), `:93-94` (guard pegajoso); `server.py:117-123` (gate solo at-enqueue) |
| CONS-P1-2 | **P1** | Codex 002 ≈ Claude 003 | Audit de exec no fail-closed: se encola ANTES de auditar (`audit_exec` falla → el bridge ejecuta sin línea); `queue_full` → intento allowlisted sin rastro; `expr==""` → ToolError sin audit; `main_fn` ni validado ni en la entry (solo Claude) | `server.py:148-156` (orden), `:173-184` (I/O sin try), `:511-519` (empty-expr), `:176-181` (entry sin main_fn) |
| CONS-P1-3 | **P1** | Codex 003 (miss Claude) | `install-mcp.ps1` no soporta ni emite `--require-version`: la instalación final queda tolerante a legacy, contra plan §5.2/§6 ("el install de 4B lo activa") y product-spec E2 | `install-mcp.ps1:1-9` (param block sin flag), `:98-101` (serverArgs) |
| CONS-P2-1 | P2 | Claude 002 (miss Codex) | Con `--enable-exec-enforce` ON, POST `/enqueue` autenticado ejecuta `exec_enforce` con CUALQUIER expr — allowlist y audit viven solo en la capa tool MCP; el bridge ejecuta incondicional | `loopback.py:82-86`, `server.py:514-516`, `MCPBridge.c:702-722` |
| CONS-P2-2 | P2 | Claude 004 | `world_time_set.year` sin rango en NINGUNA capa → `SetDate(year_arbitrario)`; vía `/enqueue` sin `year` → default 0 | `server.py:402-403`; `MCPBridge.c:909-958`, `:632` |
| CONS-P2-3 | P2 | Claude 005 | Registro `-m dayz_mcp` cwd-dependent (paquete NO instalado en venv, verificado mecánicamente) → la vía documentada de uso global (`-s user`) da `ModuleNotFoundError` | `install-mcp.ps1:98-104,125`; gate 4A ops note `:50-52` |
| CONS-P2-4 | P2 | Codex 004 ∪ Claude 013 | Test gaps de seguridad: entrega-con-mismatch sin test; sticky-version sin test; audit-fail sin test; BOM allowlist SIN regression test (revert de GATE4B-002 pasaría en verde); `ver=` malformado/vacío sin test; fixtures loopback con `"ok": True` bool (residuo LL-139); smoke del comando emitido por install | `test_mcp_tools.py:226`, `test_fase4b_tools.py:101-125`, `test_loopback.py:59,119`, `test_fase4b_tools.py:104` |
| P3-1..8 | P3 | Claude 006-012, 014 | Leak results/zombie-burst en timeout (006) · audit I/O síncrono en event loop bajo OneDrive (007) · SystemExit(2)→BaseExceptionGroup exit 1 (008) · timeout sin tope + lock global (009) · sentinel -999.0 vía /enqueue (010) · GetPollVersion cachea `4~unknown` (011) · ver= sin percent-encoding (012) · altitude: validadores duplicados sin fuente única, alias `from_pos` fantasma (validation_alias disponible en SDK pinned), 11× boilerplate, shim re-exports stale, ServerConfig dual key/keyfile, allowlist no recargable, install hard-pin `py -3.14` (014) | detalle en `2026-06-11-r21-fase4-claude.md` |

Nota preexistente (no-F4, sin verificar offline): batch poisoning vía `/enqueue` con arg mal
tipado podría tumbar el parse del batch entero en el bridge (detalle en mitad Claude).

## Adjudicación de severidad (razonada)

La mitad Claude tenía CONS-P1-1(b) y CONS-P1-2 como P2 (blast radius: gate de coherencia del
propio operador, sin frontera de confianza). Consolidado se aceptan como **P1** por el
criterio escrito del prompt ("fail-open de seguridad" = FAIL/P1) y porque: (1) violan
contratos EXPLÍCITOS del plan v2 (§5.2 "version_mismatch → fail-closed SIEMPRE"; §5.3 "cada
invocación → línea JSON"; §6 "--require-version lo añade el install de 4B"); (2) CONS-P1-1(a)
hace el gate inerte en la ventana enqueue→poll sin necesidad de escenario raro; (3) los tres
tienen fix Python/install-only barato. Precedente: R21 fase 1 (Codex escaló, consolidado
aceptó el escalado).

## Propuesta X.5 (scope si se aprueba — SOLO fixes, sin rebuild del PBO)

Todo Python + install + tests; NO toca Enforce (cero rebuild, cero re-test in-game de fases):

1. **F4-X5-1** (CONS-P1-1): `record_poll` registra SIEMPRE la versión (None sobreescribe) +
   gate de entrega — el loopback acepta un validador opcional inyectado por `Runtime`
   (`EXPECTED_BRIDGE_VERSION` + `expected_game_version` + `require_version`); poll no-ok →
   `commands=[]` sin drenar. El shim/harness standalone no inyecta validador → back-compat
   intacta (regresión run-fase3 inafectada).
2. **F4-X5-2** (CONS-P1-2 + CONS-P2-1): audit ANTES de encolar, fail-closed (`audit_failed` →
   ToolError sin encolar); auditar también `bad_args`/empty-expr; incluir `main_fn` en la
   entry; mover allowlist+audit al chokepoint `ServerState.enqueue_command` para
   `exec_enforce` (cierra también el bypass `/enqueue`; el harness no usa exec → sin impacto).
3. **F4-X5-3** (CONS-P1-3 + CONS-P2-3): `install-mcp.ps1` con `-RequireVersion` default ON
   (escape explícito `-AllowLegacy` para transición) + arreglo del cwd (entry por path
   absoluto o `pip install -e` de pyproject mínimo) — un solo pase sobre el script.
4. **F4-X5-4** (CONS-P2-2): rango de `year` en la capa Python (p.ej. 1970-2100, `bad_year`);
   la capa Enforce queda como P3 documentado (evita rebuild).
5. **F4-X5-5** (CONS-P2-4): tests de todos los anteriores + BOM regression + `ver=` malformado
   + fixtures loopback a int 0/1.

Gate de la X.5: suite completa offline + smoke MCP real (cliente SDK) + regresión
`run-fase3.ps1` una vez (sin rebuild). P3-1..8 → bug-ledger (backlog), sin tocar.

## Estado

- [ ] Adjudicación usuario: ¿X.5 con qué scope? (pendiente)
- [ ] Tras adjudicar: inbox → status applied/deferred, bug-ledger P3s, post-session.
