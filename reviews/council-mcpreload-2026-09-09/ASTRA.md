# ASTRA — Aplicar código nuevo desde la propia sesión MCP

## 1. Recomendación, en una frase

**(A) Introducir un supervisor propio por sesión `--client` que conserve stdio y la identidad/coordinación, y permita a la IA sustituir el proceso trabajador que contiene el registro FastMCP y sus implementaciones, después de cerrar admisión y drenar sus llamadas.**

## 2. Por qué esa y no las otras

**El padre existente no aporta ese supervisor MCP.** `tools/dayz_mcp/__main__.py:3` importa directamente `server.run`; los flags seleccionan modos excluyentes (`tools/dayz_mcp/server_cli.py:72`), y `--client` acaba sirviendo stdio en `tools/dayz_mcp/server.py:5437`. No hay en esa rama un bucle que sustituya trabajadores.

La instalación sí contiene otro padre: el redirector de venv. `tools/.venv-mcp/pyvenv.cfg:1` apunta a `C:\Python314`; `C:/Python314/Lib/venv/__init__.py:356` selecciona `venvlauncher.exe`. Su SHA-256 y el de `tools/.venv-mcp/Scripts/python.exe` coinciden: `97c3228a59dcc05a771ab4eeec8126ce3f36ebb53616b479adc9f2c8050a9e84`. La sonda con ese ejecutable creó PID **45664**, que lanzó al intérprete **41468**, con PPID **45664**. El redirector hereda los handles de stdio, lanza una vez, espera y devuelve el código de salida; no conserva una sesión MCP ni arranca un sucesor ([CPython 3.14.3, PC/venvlauncher.c:425, :445, :453, :460](https://github.com/python/cpython/blob/v3.14.3/PC/venvlauncher.c#L425-L460)).

| Alternativa | Motivo para descartarla como solución general |
|---|---|
| Hot reload del registro | Persisten closures y tipos anteriores: `tools/dayz_mcp/server.py:1935`, `:1955`, `:3331`; construir todo el registro en otro intérprete elimina esa mezcla. |
| Suicidio y reapertura del anfitrión | Mantengo la refutación de `reviews/mcpreload-2026-09-09/RONDA3.md:46`: respuesta aún sin flush, admisión abierta y reapertura del host sin acreditar. |
| Ejecutar las tools dentro del daemon | Traslada el código rancio al proceso que posee el manifest y lifecycle (`tools/dayz_mcp/daemon.py:413`, `:520`, `:598`); añadirle trabajadores separados sería una variante del supervisor, con alcance compartido entre sesiones. |
| Convertir todas las implementaciones en hojas recargables | Útil para hojas reales como `playbook_reload` (`tools/dayz_mcp/server.py:5348`), pero coordinar módulos, clases y estado exige una refactorización amplia y mantiene el aislamiento inferior de un solo intérprete. |
| Mensaje MCP de reinicio | En la revisión soportada por este SDK (`tools/.venv-mcp/Lib/site-packages/mcp/types.py:27`), el cierre usa el transporte y `notifications/tools/list_changed` anuncia catálogo; ninguno manda cargar Python nuevo. No acredité una API del anfitrión invocable por la IA que complete ese ciclo. [Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle#shutdown), [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#list-changed-notification). |

## 3. Coste y riesgo

**Coste medio-alto de integración; viabilidad del transporte comprobada.** El SDK instalado es `mcp==1.27.2` (`tools/requirements-mcp.txt:1`). Permite una fachada con `Server.list_tools` y `Server.call_tool` (`tools/.venv-mcp/Lib/site-packages/mcp/server/lowlevel/server.py:434`, `:492`) y conexiones internas con `stdio_client` y `ClientSession.call_tool` (`tools/.venv-mcp/Lib/site-packages/mcp/client/stdio/__init__.py:106`; `tools/.venv-mcp/Lib/site-packages/mcp/client/session.py:368`). El retorno de `CallToolResult` se conserva directamente en `lowlevel/server.py:540`, bajo esa misma raíz SDK. Esto permite mantener contenido, salida estructurada, metadatos y errores del trabajador.

**[DESIGN] Reparto de propiedad y cambios necesarios:**

- **Supervisor:** bootstrap separado del import de `server.py`, sesión MCP externa, admisión, correlación de peticiones y respuesta de una nueva tool administrativa de aplicación. Esa tool pertenece al padre; no es una llamada al trabajador ni un paso de playbook.
- **Coordinación estable de la sesión:** extraer del runtime identidad, `ControlClient`, estado de lease/ticket/operación y renovación de claims. Hoy nacen en `tools/dayz_mcp/server.py:1210`, `:1223`, `tools/dayz_mcp/control_client.py:161` y `tools/dayz_mcp/server.py:3129`. El PID e identidad enviados al daemon deben pertenecer al supervisor real. Copiar únicamente el UUID al nuevo hijo no sirve: `tools/dayz_mcp/session_coordination.py:2777` detecta identidades diferentes con el mismo session_id.
- **Trabajador sustituible:** registro, schemas, adaptadores e implementaciones volátiles; acceder a coordinación mediante un contrato IPC explícito. Hay que separar construcción del runtime y registro, hoy juntas en `tools/dayz_mcp/server.py:3287`, y adaptar la comprobación de tipo de `:3331`. No pasar instancias, locks ni clases Python entre generaciones.
- **Daemon compartido:** sigue siendo autoridad de runs, FIFO y lifecycle. No renovar identidad, liberar el lease ni modificar el orden de la cola como efecto de aplicar código.

**[DESIGN] Protocolo de aplicación:**

1. Seleccionar una generación de fuentes por manifest de hashes: copia inmutable, sin `.pyc` heredados, con rutas de datos separadas y respetando las autoridades de rutas existentes. Un árbol compartido que cambia durante el import no es una versión coherente.
2. Cerrar admisión bajo la misma exclusión que asigna cada llamada a una generación, antes de sus esperas y efectos. Contar también preparación, cola, playbooks y tareas derivadas: el heartbeat actual se crea antes de tomar `tool_lock` (`tools/dayz_mcp/server.py:3546`, `:3579`, `:3585`). Mantener ping, estado y cancelaciones operativos.
3. Preparar el candidato sin emitir operaciones al daemon, inicializar su conexión interna y verificar salud, versión IPC y catálogo completo. Primera entrega: rechazar cambios del contrato público. Drenar la generación antigua con plazo; error de carga, incompatibilidad o plazo vencido conservan la anterior. Cancelar la espera del anfitrión no equivale a haber terminado el efecto del trabajador.
4. Conmutar una sola vez cuando los resultados antiguos ya estén materializados en el padre, cerrar únicamente el trabajador retirado y responder desde el supervisor con generación, PID y manifest aplicado. La entrega externa deja de depender del cierre del trabajador: el escritor sigue vivo en el padre. La separación importa porque el SDK responde después del handler y hace flush en otra tarea (`tools/.venv-mcp/Lib/site-packages/mcp/server/lowlevel/server.py:770`, `:800`; `tools/.venv-mcp/Lib/site-packages/mcp/server/stdio.py:75`).

**Prueba de aceptación de la integración:** misma conexión externa, nueva conducta y generación verificadas; lease, ticket, operation_id, FIFO y run conservados; segunda sesión sin cambios. Negativos: import fallido, schema incompatible, petición durante el drenaje, cancelación con efecto pendiente, trabajador muerto y dos aplicaciones simultáneas. No reintentar automáticamente mutaciones de resultado desconocido. Comprobar además propagación de progreso/cancelación, límites de cola y cierre de descendientes propios. El riesgo principal es perder coordinación y provocar cleanup: `tools/dayz_mcp/daemon.py:90` llega a lanzamientos sin acuse en `tools/dayz_mcp/process_lifecycle.py:3358` y puede terminar procesos en `:3423`.

## 4. Qué NO resuelve

- Actualizar el propio supervisor, su núcleo de coordinación, el SDK o el daemon: siguen siendo procesos con código cargado. La frescura del daemon (`tools/dayz_mcp/daemon.py:650`) debe conservar su diagnóstico independiente.
- Reciclar el modo embedded: posee y cierra su loopback en el mismo proceso (`tools/dayz_mcp/server.py:5408`, `:5440`).
- Aplicar cambios de PBO/Enforce, dependencias nativas o contratos IPC incompatibles.
- Rescatar transparentemente una llamada colgada o de efecto desconocido; puede rechazarse la aplicación hasta resolverla.
- Actualizar todas las sesiones a la vez: cada supervisor adopta explícitamente su generación.
- Cambiar schemas en esta primera entrega. El SDK puede emitir `send_tool_list_changed` (`tools/.venv-mcp/Lib/site-packages/mcp/server/session.py:477`), pero la actualización efectiva del catálogo en cada anfitrión exige otro gate.
- Instalar retrospectivamente el supervisor en las conexiones actuales. Su primer despliegue requiere una activación externa; después, los cambios admitidos del trabajador sí los puede aplicar la IA.

## 5. El primer paso barato

**Sonda de transporte ya ejecutada, con Python 3.14.3 y MCP 1.27.2: PASS en 4,429 s, exit 0.** Un cliente SDK mantuvo una conexión stdio real con una fachada; esta sustituyó otro proceso conectado por stdio. Los cuerpos Python de los trabajadores contenían literales distintos y se compilaron mediante `-c`.

| Observable recibido por el cliente | Resultado |
|---|---|
| PID de fachada antes/después | **11900**, sin cambiar ni repetir initialize externo |
| Trabajador y conducta anterior | **28356**, `old` |
| Llamada lenta admitida antes de aplicar | Terminó con `old` y PID **28356** |
| Nueva llamada durante drenaje | Error `refresh_in_progress` |
| Respuesta de aplicación | Recibida por la conexión original |
| Trabajador y conducta posteriores | **39460**, `new`; `_meta` conservado |
| Candidato con catálogo incompatible | Rechazado; `new` siguió disponible |
| Final del fixture | Cero llamadas activas; contextos de procesos propios cerrados |

**Esto decide “el SDK/stdio permite la vía”: sí.** No decide conservación del estado DayZ; el fixture no importa `dayz_mcp`, no usa leases ni contacta con el daemon. La integración necesita los gates de §3.

**Reproducción [EXACT], desde la raíz física del proyecto:** el siguiente comando extrae el fixture de este mismo documento y lo ejecuta por stdin, sin crear otro archivo. Tiene un plazo global de 35 segundos. Se incluyen controles positivos y negativos; exit 0 exige todas sus aserciones. El fixture serializa las llamadas de cada trabajador y no pretende ser el dispatcher de producción.

```powershell
@'
from pathlib import Path
document = Path("reviews/council-mcpreload-2026-09-09/ASTRA.md").read_text(encoding="utf-8")
marker = "<!-- ASTRA_" + "PROBE_BEGIN -->"
source = document.split(marker, 1)[1].split("```python\n", 1)[1].split("\n```", 1)[0]
exec(compile(source, "<astra-stdio-probe>", "exec"))
'@ | & 'tools/.venv-mcp/Scripts/python.exe' -B -
```

<details>
<summary>Fixture reproducible: solo procesos ficticios, sin archivos auxiliares</summary>

<!-- ASTRA_PROBE_BEGIN -->
```python
import asyncio, base64, json, os, sys, time
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

worker = r'''
import asyncio, json, os, sys
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
app = Server("astra-worker-probe")
version = '__ASTRA_GENERATION__'
@app.list_tools()
async def listing():
    names = ["probe", "slow"] if version != "bad" else ["different"]
    return [types.Tool(name=n,inputSchema={"type":"object","additionalProperties":False}) for n in names]
@app.call_tool()
async def call(name, arguments):
    if name == "slow": await asyncio.sleep(2)
    data = {"implementation":version,"worker_pid":os.getpid()}
    return types.CallToolResult(content=[types.TextContent(type="text",text=json.dumps(data))], structuredContent=data, _meta={"probe_marker":"preserved"})
async def main():
    async with stdio_server() as (r,w):
        await app.run(r,w,app.create_initialization_options())
asyncio.run(main())
'''
facade = r'''
import asyncio, base64, json, os, sys
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
worker = base64.b64decode(os.environ["ASTRA_PROBE_WORKER"]).decode()
app = Server("astra-facade-probe")
condition = asyncio.Condition()
current = None
active = 0
draining = False
def result(data,error=False):
    return types.CallToolResult(content=[types.TextContent(type="text",text=json.dumps(data))],structuredContent=data,isError=error)
async def actor(version, queue, ready):
    try:
        params = StdioServerParameters(command=sys.executable,args=["-B","-u","-c",worker.replace("__ASTRA_GENERATION__", version)],env={"PYTHONDONTWRITEBYTECODE":"1"})
        async with stdio_client(params) as (r,w):
            async with ClientSession(r,w) as session:
                await session.initialize()
                catalog = await session.list_tools()
                ready.set_result(catalog.tools)
                while True:
                    request = await queue.get()
                    if request is None: break
                    name,args,future = request
                    try:
                        value = await session.call_tool(name,args)
                        if not future.done(): future.set_result(value)
                    except BaseException as exc:
                        if not future.done(): future.set_exception(exc)
                        if isinstance(exc,asyncio.CancelledError): raise
    except BaseException as exc:
        if not ready.done(): ready.set_exception(exc)
        else: raise
async def start(version):
    q = asyncio.Queue()
    ready = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(actor(version,q,ready))
    catalog = await ready
    return {"queue":q,"task":task,"catalog":catalog}
async def stop(target):
    await target["queue"].put(None)
    await target["task"]
async def invoke(target,name,args):
    future = asyncio.get_running_loop().create_future()
    await target["queue"].put((name,args,future))
    return await future
def signature(target):
    return [t.model_dump(mode="json",exclude_none=True) for t in target["catalog"]]
@app.list_tools()
async def listing():
    return current["catalog"] + [
        types.Tool(name="refresh",inputSchema={"type":"object","properties":{"version":{"type":"string"}},"required":["version"],"additionalProperties":False}),
        types.Tool(name="status",inputSchema={"type":"object","additionalProperties":False})]
@app.call_tool()
async def call(name,args):
    global current,active,draining
    if name == "status":
        return result({"facade_pid":os.getpid(),"active":active,"draining":draining})
    if name == "refresh":
        async with condition:
            if draining: return result({"error":"refresh_in_progress"},True)
            draining=True
        candidate=None
        try:
            candidate=await start(args["version"])
            if signature(candidate) != signature(current):
                await stop(candidate)
                candidate=None
                return result({"error":"schema_changed"},True)
            async with condition:
                await condition.wait_for(lambda: active == 0)
                previous=current
                current=candidate
                candidate=None
            await stop(previous)
            return result({"status":"switched","facade_pid":os.getpid()})
        finally:
            if candidate is not None: await stop(candidate)
            async with condition:
                draining=False
                condition.notify_all()
    async with condition:
        if draining: return result({"error":"refresh_in_progress"},True)
        selected=current
        active+=1
    try: return await invoke(selected,name,args)
    finally:
        async with condition:
            active-=1
            condition.notify_all()
async def main():
    global current
    current=await start("old")
    try:
        async with stdio_server() as (r,w):
            await app.run(r,w,app.create_initialization_options())
    finally: await stop(current)
asyncio.run(main())
'''
async def main():
    started=time.monotonic()
    params=StdioServerParameters(command=sys.executable,args=["-B","-u","-c",facade],env={"PYTHONDONTWRITEBYTECODE":"1","ASTRA_PROBE_WORKER":base64.b64encode(worker.encode()).decode()})
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as session:
            await session.initialize()
            await session.list_tools()
            old=await session.call_tool("probe",{})
            before=(await session.call_tool("status",{})).structuredContent
            slow_task=asyncio.create_task(session.call_tool("slow",{}))
            while not (await session.call_tool("status",{})).structuredContent["active"]:
                await asyncio.sleep(.01)
            refresh_task=asyncio.create_task(session.call_tool("refresh",{"version":"new"}))
            while not (await session.call_tool("status",{})).structuredContent["draining"]:
                await asyncio.sleep(.01)
            rejected=await session.call_tool("probe",{})
            slow=await slow_task
            switched=await refresh_task
            new=await session.call_tool("probe",{})
            incompatible=await session.call_tool("refresh",{"version":"bad"})
            preserved=await session.call_tool("probe",{})
            after=(await session.call_tool("status",{})).structuredContent
            checks={
                "same_facade_pid":before["facade_pid"] == after["facade_pid"],
                "new_worker_pid":old.structuredContent["worker_pid"] != new.structuredContent["worker_pid"],
                "old_behavior":old.structuredContent["implementation"] == "old",
                "inflight_completed_old":slow.structuredContent == old.structuredContent,
                "new_admission_rejected":rejected.isError and rejected.structuredContent["error"] == "refresh_in_progress",
                "switch_response_received":switched.structuredContent["status"] == "switched",
                "new_behavior":new.structuredContent["implementation"] == "new",
                "metadata_preserved":new.meta == {"probe_marker":"preserved"},
                "schema_change_rejected":incompatible.isError and incompatible.structuredContent["error"] == "schema_changed",
                "rollback_preserved_current":preserved.structuredContent == new.structuredContent,
                "idle_at_end":after["active"] == 0 and not after["draining"]
            }
            assert all(checks.values()), checks
            report={"checks":checks,"facade_pid":before["facade_pid"],"old":old.structuredContent,"new":new.structuredContent}
    report["elapsed_s"]=round(time.monotonic()-started,3)
    report["owned_process_contexts_closed"]=True
    print(json.dumps(report))
asyncio.run(asyncio.wait_for(main(),35))

```
<!-- ASTRA_PROBE_END -->

</details>

## 6. Confianza

**Media en la propuesta completa; alta en la viabilidad del transporte y en descartar el redirector como supervisor MCP.** Falta integrar y probar el runtime real, sus autoridades de rutas, identidad, leases, FIFO y lifecycle; también cancelaciones, fallos de proceso y el anfitrión real. La sonda satisfactoria no sustituye esas pruebas.

**[SUPUESTO]** Los seis pares vivos descritos en el BRIEF usan el mismo redirector inspeccionado: WMI devolvió acceso denegado, por lo que no revalidé esos PID concretos. La comprobación del binario y la reproducción corresponden al entorno local aprobado.

Fuentes leídas desde `C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev`: HEAD al inicio `5d9f356`, al cierre `beb3339`, con trabajo concurrente ajeno. Los hashes de los 275 archivos de fuentes, tests y configuración contrastados antes y después de las sondas no cambiaron; las anclas corresponden al contenido leído, no a un árbol limpio. Se leyeron INFORME §2, GROK-VERDICT y RONDA3 §2. No se repitió la suite global para este dictamen documental ni se operó DayZ/Steam. La consulta final `session_status` fue rechazada: `MCP tool call requires approval, but approval policy is never`; el estado compartido final queda sin verificar. No se adquirió lease ni hubo cierres de procesos de juego.

Única escritura de esta lane: este `ASTRA.md`, que contiene dictamen, evidencia y fixture. Sin commit de esta lane ni actualizaciones de memoria, HANDOFF o buzones, por el alcance solicitado.
