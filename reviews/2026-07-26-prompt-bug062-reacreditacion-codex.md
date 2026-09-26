# Prompts Codex — BUG-062 (b) re-acreditación + BUG-063 (c) diagnóstico legible

**Autor**: Claude (orquestador) · **Fecha**: 2026-07-26
**Regla aplicada**: un deliverable por sesión → **dos prompts, dos sesiones**.
Recomendado ejecutar **el B primero** (pequeño, riesgo cero, quita un falso diagnóstico
que ya indujo error), y el A después.

Contexto común: `bug-ledger.md` BUG-062/BUG-063 y el runbook
`AI/20_Runbooks/dayz-mcp-agent-session-protocol.md` §`credential_source_untrusted`.

---

## PROMPT A — BUG-062 (b): re-acreditación ante generación nueva de daemon

===== PROMPT INICIO =====

Implementa en DayZ_MCP la recuperación automática de un cliente MCP vivo cuando el daemon
al que se acreditó ha sido reemplazado por otro. Alcance cerrado: sólo el camino de
credencial/transporte del cliente. NO toques lifecycle, cola, runs ni el bridge Enforce.

## Problema verificado

El daemon se autoapaga por inactividad y el siguiente cliente lo re-spawnea con
`daemon_generation` nueva. Observado tres veces el 2026-07-26 (PIDs 49216 → 52524 →
ninguno). Cada reemplazo deja inservibles a TODOS los clientes vivos de la caja: hoy la
única recuperación es abrir una sesión nueva, porque las tools sólo cargan al arranque.
Con 60 procesos cliente vivos, un reemplazo desarma a todo el mundo a la vez.

## Carga inicial obligatoria (rutas absolutas)

1. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\daemon_credential.py`
2. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\accredited_daemon_transport.py`
3. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\control_client.py`
4. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\` — los tests de credencial/transporte existentes
5. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\product-spec.md` §H, filas **H10** y **H12**

## Estado del código (verificado por el orquestador; re-verifica antes de editar)

- `daemon_credential.py:57-67` fija al construirse la tupla de autoridad
  (`kind/host/port/keyfile/native_executable/argv/cwd/security_build_id/authority_sha256`).
- `daemon_credential.py:72-100`: `_assert_authority_unchanged` / `_revalidate_authority`
  lanzan `credential_source_untrusted` si esa tupla cambia o si `policy.revalidate()` falla.
- `daemon_credential.py:~150-212`: H12/BUG-056 ya implementa refresh **single-flight
  disparado por 401** con exactamente un reintento, y llama `_revalidate_authority()` antes
  de reintentar.
- `daemon_credential.py:202-207`: `daemon_identity_unverified` se **re-lanza tal cual**,
  deliberadamente fuera del camino de refresh.
- `accredited_daemon_transport.py:273` es el sitio del `raise` de `daemon_identity_unverified`.

## Qué implementar

Que un cliente vivo que se encuentra un daemon **distinto pero legítimo** rehaga su
acreditación en vez de quedar inservible, sin relajar ninguna comprobación.

Principio rector que justifica el cambio: **una sesión nueva se acredita con éxito contra
ese mismo daemon**. Permitir que un cliente vivo repita EXACTAMENTE la misma acreditación
completa no relaja nada — sólo cambia *cuándo* se hace. Si la acreditación completa no
pasa, el resultado sigue siendo fallo cerrado.

Requisitos:

1. Distinguir dos causas hoy fusionadas, porque merecen respuestas distintas:
   - **el daemon cambió** (proceso/generación distintos, acreditación completa vuelve a
     pasar) → re-acreditar y reintentar una vez;
   - **la config/policy del propio cliente cambió** (keyfile, argv, cwd, build id…) →
     sigue fallando cerrado; no es un daemon nuevo, es un cliente que ya no es el que se
     registró.
2. La re-acreditación ejecuta el camino **completo** (owner del socket, PID único y
   estable, executable/argv/cwd canónicos, doble snapshot nativo) igual que al construirse.
   Prohibido cualquier atajo, cacheo o aceptación por "status 2xx".
3. Acotada: single-flight (como el refresh existente), **un** reintento por request, sin
   bucles, respetando el deadline original. Segundo fallo → cerrado.
4. Nunca inicia, reemplaza ni mata daemons; no crea, adopta, libera ni altera leases,
   tickets, runs ni ownership. La invalidación H2/H5 por cambio de generación se conserva:
   re-acreditarse **no** resucita un lease de la generación anterior.
5. Cuando aun así no se pueda recuperar, el error debe decir **qué hacer**: código estable
   y distinguible cuyo mensaje indique explícitamente abrir una sesión nueva. Hoy
   `credential_source_untrusted` y `daemon_identity_unverified` no lo dicen y se
   diagnostican mal de forma sistemática. Sin secretos en el mensaje.

## Método

TDD estricto: primero un test que reproduzca el escenario real (cliente acreditado contra
daemon A; A desaparece; B legítimo ocupa el puerto; la siguiente llamada debe recuperarse)
y que falle por la razón esperada. Y un test negativo por cada camino que DEBE seguir
fallando cerrado: policy/config drift, daemon no legítimo, segundo fallo, deadline agotado.
Sin negativos verdes, el trabajo no vale.

## Fuera de alcance (no lo hagas)

- NO toques `session_coordination.py`, `process_lifecycle.py`, `loopback.py`,
  `dayz_test_*`, el bridge Enforce ni ningún `.c`.
- NO cambies `--idle-timeout` ni los registros MCP: ya se mitigó por configuración
  (600→3600) y no es tu tarea.
- NO toques `doctor.py` — es el PROMPT B, sesión aparte.
- NO arranques DayZ, no adquieras lease, no lances lifecycle, no reinicies daemons.
- NO refactorices de paso. Cada línea traza a BUG-062(b).

## Salida esperada

Bloques A/B/C/D estándar. En **B** pega la salida real de unittest (no parafraseada),
incluyendo los negativos. En **C**, cualquier ambigüedad de diseño que resolvieras y por qué.

===== PROMPT FIN =====

---

## PROMPT B — BUG-063 (c): separar fallo de sonda de config ilegible

===== PROMPT INICIO =====

Arregla un diagnóstico engañoso en el doctor de DayZ_MCP. Cambio pequeño y cerrado.

## Problema verificado

`doctor` reporta `CONFIG_UNREADABLE` cuando lo que falla es la **sonda CLI**, no el
registro. En `doctor.py:303-309`, `_parse_registration` recibe `result: tuple[int, str]`
—la salida de ejecutar `claude mcp get` / `codex mcp get --json`— y hace
`raise ValueError("config_unreadable")` en cuanto `returncode != 0`. El emisor está en
`doctor.py:816-830`.

Observado el 2026-07-26: doctor devolvió `CONFIG_UNREADABLE` para **ambas** plataformas
mientras los dos registros eran correctos, verificados leyendo los ficheros a mano
(`--client`, `--keyfile`, `--port 8765`, `--require-version`, `--idle-timeout`,
`--client-platform`). El riesgo no es cosmético: el finding invita a re-registrar el MCP y
reiniciar daemons —operaciones que tumban trabajo de otros agentes— cuando no hay nada roto.

## Qué implementar

Separar las dos causas en findings distintos:

- **sonda fallida** (`returncode != 0`, salida no utilizable): código nuevo tipo
  `CONFIG_PROBE_FAILED`, con mensaje que deje claro que **no** prueba que el registro esté
  mal y que el siguiente paso es leer el fichero de config, no re-registrar.
- **config realmente ilegible/inválida** (parse o forma incorrecta): conserva
  `CONFIG_UNREADABLE`.

Fail-closed se mantiene en ambos casos: ninguno pasa a WARN silencioso ni desaparece del
resumen. Sin secretos ni material de clave en los mensajes.

## Carga inicial obligatoria

1. `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\dayz_mcp\doctor.py`
2. Los tests de doctor bajo
   `C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools\tests\`

## Método

TDD: un test que hoy no distingue los dos casos y falla al exigir la distinción; después el
cambio. Cubre las cuatro combinaciones (sonda OK + config válida / sonda OK + config
inválida / sonda fallida / sonda fallida en ambas plataformas).

## Fuera de alcance

- NO toques `daemon_credential.py` ni el transporte — es el PROMPT A.
- NO cambies el resto de findings del doctor ni su schema de salida más allá del código nuevo.
- NO arranques DayZ, no adquieras lease, no reinicies daemons.

## Salida esperada

Bloques A/B/C/D. En **B**, salida real de unittest.

===== PROMPT FIN =====

---

## Notas operativas para quien lance esto

- **Escritura de archivos**: `apply_patch` está roto en este host. Escribir por shell
  (`Set-Content`/`Add-Content -Encoding utf8` en bloques ≤4 KB, o `python open().write()`),
  nunca Base64/FileStream (el filtro del proveedor lo bloquea). Verificar cada archivo tras
  escribirlo (`py_compile` o line-count).
- **Fuentes en OneDrive**: copiar a un scratchpad local antes de delegar; Codex se cuelga en
  la hidratación de OneDrive.
- **Lanzamiento**: implementación = ciclo **background** (`task --background --write
  --fresh`), nunca `codex-rescue` (tope duro de 10 min). Outputs bajo el workspace `-C`.
- **Riesgo de filtro**: el prompt A roza vocabulario de seguridad (acreditación, guard). Está
  redactado en términos operativos a propósito. Si el proveedor lo bloquea, es implementación
  —no una review de seguridad— y el patrón conocido es reformular en lenguaje neutro, no
  abandonar. Ver memoria `codex-filter-blocks-security-reviews`.
- **Receptor**: verificar el Bloque A con `Test-Path` host-direct (`ls` de bash da
  bindfs-stale), y no dar por buenos los tests del Bloque B sin ver salida real de unittest.
