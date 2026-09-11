# Dictamen del Consejo — Vía de Recarga del Servidor MCP

## 1. Tu recomendación, en una frase, y si es (A) o (B)

**(A)** — Implementar un supervisor delgado y estable que retenga el transporte stdio del anfitrión y despache las peticiones JSON-RPC hacia un proceso trabajador `--worker` reciclable, permitiendo a la propia IA drenar, terminar y reaparecer el trabajador limpiamente mediante una tool de control sin depender de hot-reload in-process ni arriesgar la rotura del transporte con el anfitrión.

---

## 2. Por qué esa y no las otras

- **(B) Hot-reload del registro FastMCP in-process:** Sigue categóricamente refutado; los closures de validación (`tools/dayz_mcp/server.py:1930`, `:1950`), la validación estricta de clases en runtime (`server.py:3320`), la identidad inmutable de sesión (`server.py:1210-1227`, `tools/dayz_mcp/control_client.py:161`) y los heartbeats asíncronos (`server.py:3568`) dejan schemas desfasados e instancias zombi al reimportar módulos.
- **(A) Auto-suicidio del proceso stdio esperando reconexión del anfitrión:** Refutado con causa raíz sólida en `reviews/mcpreload-2026-09-09/RONDA3.md:48-54`; retornar de la tool no garantiza el flush en el transporte (`stdio.py:75-81`), no cierra la admisión frente a peticiones concurrentes (`lowlevel/server.py:678`), y los anfitriones estándar (`[SUPUESTO]` Claude Desktop / Cursor) marcan el servidor como caído y bloquean la sesión en vez de reaparecer el proceso.
- **Reutilizar la pareja padre/hijo observada en producción:** Es un espejismo del entorno; el padre (34744) -> hijo (21388) medido hoy NO es un supervisor MCP ni una infraestructura construida en `tools/dayz_mcp/__main__.py:1-8` o `server.py:5400-5441`, sino el stub redirector nativo de Windows (`tools/.venv-mcp/pyvenv.cfg:1-5`, `Scripts\python.exe` delegando en `C:\Python314\python.exe`), documentado expresamente como capa pasiva a saltar en `tools/dayz_mcp/orphan_guard.py:314-319` y `tools/dayz_mcp/host_config.py:380-381`.
- **Mover la ejecución de tools tras la frontera IPC del daemon (`:8765`):** Descabellado por radio de explosión y multi-tenancy; el daemon es un broker singleton compartido por las seis sesiones que almacena los runs de juego (`tools/dayz_mcp/daemon.py:413`, `:520`, `:598`) y arbitra la caja FIFO única, por lo que reciclarlo tiraría partidas de otras sesiones, chocaría con bloqueos activos y movería tools estrictamente locales (captura de pantalla en `server.py:4664` o playbooks locales) a un proceso central desacoplado.
- **(B) Reestructurar el árbol completo para que cada tool sea una hoja dinámica:** Aunque `playbook_reload` probó que una hoja pura con compilador aislado funciona (`tools/dayz_mcp/playbook_tool.py:85-129`, `server.py:5338-5351`), replicar ese mecanismo de carga por bytes, exclusión mutua de admisión (`playbook_tool.py:174`) e inventarios de esquemas en las 61 tools del sistema constituiría una reescritura masiva de alto riesgo regresivo sin cubrir fallos en el core.
- **Afordancias de protocolo MCP (`notifications/tools/list_changed`):** La notificación `list_changed` solo indica al cliente que refresque la lista de herramientas registradas (`tools/list`); la especificación MCP carece por completo de verbos de reconexión de transporte o recarga de código en caliente iniciados por el servidor.

---

## 3. Coste y riesgo

### Qué hay que tocar
1. **Punto de entrada y CLI:** En `tools/dayz_mcp/server_cli.py:72-95`, introducir `--worker` como opción interna de ejecución.
2. **Despacho en `run()`:** En `tools/dayz_mcp/server.py:5400-5441`, si `config.mode == "client"` y no se especifica `--worker`, desviar el flujo a `supervisor.run_supervisor(config)`. Si se especifica `--worker`, mantener la inicialización estándar `build_app(config)` conectada a los pipes stdio del supervisor.
3. **Módulo supervisor dedicado (`tools/dayz_mcp/supervisor.py`):** Módulo ligero (~150 líneas) y con stdlib pura (`asyncio`, `subprocess`, `json`):
   - Mantiene la conexión stdio con el anfitrión.
   - Lanza el hijo trabajador con pipes anónimas (`sys.executable -m dayz_mcp --client --worker ...`).
   - Almacena en memoria el mensaje `initialize` del anfitrión.
   - Mantiene un contador de peticiones en vuelo (`inflight_count`).
   - Expone/intercepta la tool de control `mcp_server_recycle`: drena llamadas pendientes, responde con éxito al anfitrión (garantizando el flush antes de matar al hijo), finaliza el proceso hijo viejo, arranca un hijo nuevo, retransmite el handshake `initialize` almacenado, emite `notifications/tools/list_changed` hacia el anfitrión y reanuda el despacho de la cola.
4. **Válvula de seguridad en el trabajador:** La tool `mcp_server_recycle` debe fallar inmediatamente si el cliente retiene un lease activo (`control_client.py:161`, `session_coordination.py:3430`), tickets en cola o heartbeat de caja (`server.py:3568`), obligando a liberar recursos mutantes antes de reiniciar el entorno de código.

### Qué se puede romper
1. **Deadlock de pipes en Windows:** Si el buffer de pipe anónima se satura con cargas útiles grandes (p. ej. `capture_screenshot`) sin lectura asíncrona concurrente en el supervisor.
2. **Replay de inicialización incompleto:** Si el nuevo trabajador no recibe exactamente los mismos parámetros de `initialize` y `notifications/initialized`, FastMCP rechazará las llamadas posteriores por violación de protocolo.
3. **Orfandad del trabajador:** Si el proceso anfitrión o el supervisor terminan de forma abrupta, el trabajador hijo podría quedar flotando en memoria si no cuenta con un watchdog de muerte de proceso padre (`orphan_guard.py:314`).

### Cómo se prueba que funciona
1. **Harness offline de transporte:** Test automatizado sin DayZ ni daemon donde un cliente MCP simulado conecta con el supervisor; se ejecuta una tool que devuelva "versión 1", se muta el archivo de la tool en disco, se invoca `mcp_server_recycle`, y la siguiente llamada devuelve "versión 2" demostrando que el PID del supervisor no cambió y el socket/pipe del cliente no sufrió desconexión.
2. **Test de exclusión mutua de admisión:** Verificar que una solicitud de reciclado con una llamada en vuelo espera a que esta finalice, y que un intento de reciclado con lease activo (`active_lease_token is not None`) es rechazado con error tipado.
3. **Prueba de regresión de frescura:** Comprobar que tras el reciclado exitoso del trabajador, `tools/dayz_mcp/server_freshness.py:24` reporta `status="fresh"` y `tool_registry_source_stale=False` sin necesidad del remedio externo `reopen_mcp_client`.

---

## 4. Qué NO resuelve

1. **Bugs en el propio supervisor:** Si se introduce un error en `tools/dayz_mcp/supervisor.py`, el supervisor no puede reciclarse a sí mismo; ese caso residual sí requiere que un operador o anfitrión reinicie la sesión. (Por ello el supervisor debe ser mínimo, determinista y sin dependencias).
2. **Código del daemon en `:8765`:** Los cambios en `tools/dayz_mcp/daemon.py`, `SessionCoordinator` o `ProcessLifecycle` viven en el broker común; el reciclado del trabajador client no afecta ni actualiza el proceso daemon.
3. **Conservación de leases a mitad de secuencia:** No permite reciclar mientras se retiene un lease exclusivo sobre el juego; las herramientas deben invocarse fuera de secuencias mutantes activas.
4. **Modo `--embedded`:** Las sesiones que ligan el puerto local en el propio proceso (`server.py:5406-5416`) quedan fuera; el supervisor está concebido para `--client`.
5. **Anfitriones reacios a `list_changed`:** Si el código nuevo añade herramientas al catálogo, clientes que ignoren la notificación `notifications/tools/list_changed` no verán las nuevas firmas hasta abrir una conversación nueva (`[SUPUESTO]`), aunque sí ejecutarán de inmediato el código nuevo de todas las tools preexistentes.

---

## 5. El primer paso barato

**Sonda de aislamiento en script desechable (< 45 minutos, sin tocar producción ni tirar procesos vivos):**

Crear un script temporal fuera de `tools/` (p. ej. `scratch/probe_supervisor_pipe.py`) que implemente la mecánica básica:
1. Arranca un proceso "supervisor" en asyncio que abre un hijo Python mediante `subprocess.Popen([sys.executable, "-c", ...], stdin=PIPE, stdout=PIPE)`.
2. El supervisor envía una petición JSON-RPC `{"jsonrpc":"2.0","id":1,"method":"tools/call",...}` y recibe respuesta.
3. El supervisor simula una edición en un fichero auxiliar de prueba.
4. El supervisor envía la orden de terminación al hijo viejo, levanta un hijo nuevo, reinyecta el handshake `initialize` y envía una segunda llamada `tools/call`.
5. **Criterio decisorio:** El cliente conectado al supervisor comprueba que la segunda respuesta contiene el nuevo código y que la conexión stdio nunca emitió EOF ni levantó excepción de pipe roto en Windows. Si el test pasa, la arquitectura es plenamente viable; si Windows o el SDK bloquean las pipes anónimas en asyncio, se aborta sin haber perturbado el árbol ni las sesiones vivas.

---

## 6. Confianza y qué faltó para subirla

- **Nivel de confianza:** **Alta** en la dirección arquitectónica (A, supervisor/trabajador) como la única alternativa que respeta la inmutabilidad de la conexión stdio del anfitrión sin caer en las trampas demostradas del hot-reload in-process o del suicidio de proceso. **Media-Alta** en la implementación inmediata sobre Windows.
- **Qué faltó para subirla:**
  1. Ejecución práctica de la sonda del paso 5 para verificar el comportamiento exacto de los streams asíncronos (`asyncio.connect_read_pipe`) en Windows frente a pipes anónimas de subprocesos creados con `subprocess.Popen`.
  2. Confirmación empírica del comportamiento de los clientes anfitriones de producción (`[SUPUESTO]` Claude Desktop / Codex) al procesar la notificación `notifications/tools/list_changed` durante una conversación activa sin reiniciar el chat.
