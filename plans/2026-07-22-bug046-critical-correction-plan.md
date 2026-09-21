# BUG-046 — Addendum de corrección crítica C-01/C-02/H9

Fecha: 2026-07-22  
Estado: aprobado por la autorización permanente del usuario para adaptar el plan cuando mejore seguridad, calidad y coste  
Precedencia: este addendum sustituye únicamente las decisiones incompatibles de `2026-07-22-bug046-lease-queue-liveness-plan.md` §§2.2, 4.4 y Task 6A. El resto del plan sigue vigente.

## Objetivo y traza DPF

- H4 exige que la promoción FIFO ocurra únicamente durante un `session_wait` vivo (`product-spec.md:130`).
- H9 exige launcher nativo/neutral, registrado y sin autorización de PowerShell ni `.ps1` (`product-spec.md:135`).
- C-01 bloquea H4/H9: el grant publicaba `_active` antes de cerrar el WAL (`tools/dayz_mcp/session_coordination.py:445-453,2142-2150`) y `authorize` sólo cotejaba el token contra `_active` (`:1015-1024,1067-1113`).
- C-02 bloquea H4/H9: un fault reconstruido desde snapshot sin marker sólo vivía en memoria (`tools/dayz_mcp/runtime_state.py:602-625`) y el snapshot de la siguiente generación podía dejarlo limpio (`tools/dayz_mcp/daemon.py:231-239`; `runtime_state.py:383-404`).
- El launcher existente bloquea H9 porque construye y ejecuta PowerShell (`tools/dayz_mcp/secure_launcher.py:350-360,413-425`) y permitía sustituir la raíz de confianza mediante `--registry` (`:597-606`).

## Decisiones cerradas

1. La autoridad de un grant es provisional mientras exista WAL pendiente. No se devuelve token, no se expone owner activo y ninguna mutación autoriza hasta que `completed -> clear` termine y la revalidación post-I/O confirme el mismo grant.
2. Todo fault sintético de startup se convierte en un latch durable y reparable antes de consumir la generación anterior. Reinicios sucesivos conservan el bloqueo. Un snapshot corrupto se clasifica una vez; no se releen los mismos bytes para abortar el daemon.
3. El launcher PowerShell no se termina ni se habilita. Se desactiva fail-closed antes de adquirir sesión o crear hijos. El CLI productivo no admite `--registry`; el registro canónico no aprueba consumidores hasta existir un PE nativo revisado.
4. Los `.ps1` heredados se preservan como material histórico, pero ningún código, test o documentación operativa puede invocarlos. No se cambia ExecutionPolicy.
5. H4 y H9 permanecen `❌` hasta completar los viability tests y los gates reales; los tests offline no cambian por sí solos ese estado.

## Fases y ficheros

### A — Fence de autoridad

Scope: `tools/dayz_mcp/session_coordination.py` y tests focales de coordinación.

Viability tests:

- grant inmediato y FIFO: callback/barrera antes de `completed`, antes de `clear` y con `clear` fallido; una segunda request no obtiene token y `authorize` rechaza;
- tras clear correcto: exactamente un token/owner se publica y autoriza;
- tombstone o drift de revisión durante I/O: cero token y compensación/fault durable;
- toda ejecución bajo `p0s_test_runner.py` termina con `intercept_count=0` y `attempts=[]`.

### B — Fault durable de startup

Scope: `tools/dayz_mcp/runtime_state.py`, `daemon.py`, `loopback.py` y tests focales de recovery/lifecycle.

Viability tests:

- snapshot-fault + marker ausente -> reinicio B -> reinicio C: ambos bloquean grants;
- reparación exacta e idempotente elimina el latch y sólo entonces permite el primer grant;
- snapshot corrupto no impide exponer status/doctor/admin repair y nunca se interpreta como limpio;
- ninguna clasificación de startup imprime secretos ni pierde la evidencia corrupta.

### C — Contención H9

Scope: `tools/dayz_mcp/secure_launcher.py`, `tools/approved-launchers.json`, tests del launcher/migración y documentación operativa inmediata.

Viability tests:

- el entrypoint falla antes de abrir sesión y antes de cualquier launch mientras no haya consumidor PE aprobado;
- `--registry` se rechaza en parsing y no existe override productivo equivalente;
- no hay `subprocess.run/Popen/create_subprocess*` activo en los tests de migración ni literales operativos que autoricen PowerShell, `pwsh`, `cmd` o `.ps1`;
- registro vacío/corrupto/duplicado falla cerrado; ejecución focal protegida con cero intentos.

### D — Endurecimiento posterior, secuencial

Después de re-revisar A-C:

1. completar `completed cleanup-pending` sin degradarlo a fault;
2. escanear completamente audit current + backups en `write_once` y fijar la razón de reparación;
3. cerrar tabla semántica estado/fase WAL y persistir fronteras prepared/committed/published;
4. añadir causalidad verificable por request ID al grant de wait y endurecer el gate local;
5. resolver la elección daemon cross-process de Task 5B;
6. diseñar e implementar el consumidor PE nativo; sólo entonces reabrir el launcher.

## Gates y rollback

- No se ejecutan listener, daemon, DayZ, configuración host real ni procesos hijo durante A-C.
- Tests: Python del venv con `-I -B` y allowlists explícitas sobre `tools/p0s_test_runner.py`.
- Rollback de A/B: restaurar los hashes previos documentados en los informes y conservar los markers/snapshots; nunca limpiar evidencia para volver atrás.
- Rollback de C: no reactiva la ruta PowerShell. Si el launcher nativo falla, permanece desactivado fail-closed.
- Informes adversariales de entrada: `.superpowers/sdd/bug046-combined-authority-review.md` SHA-256 `80382FC67A2729C24A60BCD4A0821B086B4DC6EBACF4F0EBFBB7DE564C51EBA7` y `.superpowers/sdd/bug046-combined-launcher-review.md` SHA-256 `5531118E4F841AB3BC5609496DB5779C0A6CD54305F4D2A82DF92136D1F695DB`.

## Criterio de salida de este addendum

A-C requieren tests RED demostrados, GREEN focal dos veces, batería combinada protegida, diff review independiente y hashes finales. Cualquier hallazgo Critical/High nuevo reabre la fase. D y los gates vivos permanecen abiertos hasta su propia ejecución y evidencia.
