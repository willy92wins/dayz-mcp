Voy a leer el brief de revisión y seguir su formato al pie de la letra.El brief pide juicio adversarial: leo el informe y el código citado, y compruebo cada `path:line` en el fichero.Ahora cruzo las citas del informe con `server.py` y busco caminos de falso fresco, consumidores de `content` y el enganche privado.Sigo las citas de T2 y busco imports perezosos, consumidores de `content` y el contrato real de FastMCP.Citas del informe contra el fichero: varias están desplazadas. Hallazgos abajo, luego las seis preguntas.

Citas inexactas o desplazadas
- `server.py:3333` no publica `server_modules`. Ahí empieza `_with_tool_registry`; el campo se escribe en `server.py:3337`.
- `server.py:679` es el inicio de `_frozen_tool_registry_overlay`; fingerprint y captured_at van en `682-683`.
- `server.py:3342` no es el censo de fuentes. Es `_bridge_tool_names` (nombres de tools). La referencia de fuentes es `_SERVER_SOURCES` en `3331` / `server.py:60`.
- `mcp/server/fastmcp/server.py:302` es `def _setup_handlers`. El `call_tool` se registra en `:308`.
- El resto que comprobé cuadra: `server.py:60`, `:3263`, `:5333`; T2 `3276`, `:3320`, `:1930/:1950`, `:1116`, `:1205`, `:1218`, `:3568`, `:5411`, `:910`, `:5413`, `:5392`; `knowledge.py:620`; `control_client.py:161`; `daemon.py:413/:520/:598/:730/:451/:467/:90`; `process_lifecycle.py:3358/:3423`; `session_coordination.py:2084`.

---

MAYOR — `playbooks/runner.py` se carga, se cachea y no entra en el censo

donde: tools/dayz_mcp/playbook_tool.py:61 y tools/dayz_mcp/server_freshness.py:26

por que importa: Es el mismo fallo de hoy (proceso con código viejo, marcador en silencio) sobre el compositor de playbooks. `load_runner` registra el módulo como `dayz_playbook_runner`, no `dayz_mcp.*`; el fichero vive en `playbooks/`, no en `tools/`. `loaded_source_files` no lo ve, así que tampoco puede marcarlo `loaded_after_server_snapshot`. `_runner` se queda con el bytecode de la primera carga.

como se dispara: Sesión MCP ya abierta. Primera `playbook_run` (carga y cachea `playbooks/runner.py`). Se edita `_async_run` u otra función de ese fichero. La siguiente `playbook_run` sigue en lo viejo. No hay `_meta.server_code_freshness`, `tool_registry_source_stale` sigue `false`, `content` tiene un solo bloque. El test `test_loaded_watch_covers_registered_implementations_and_helpers` solo mira `tool.fn.__module__` de tools registradas; el runner no es esa función.

confianza: alta. No hace falta ejecutar el juego: el nombre y la ruta quedan fuera de las tres ramas del censo. No corrí un proceso live que lo demuestre de punta a cabo.

---

MAYOR — Un import perezoso de `dayz_mcp.*` deja el marcador en unknown para siempre

donde: tools/dayz_mcp/server_freshness.py:66 y tools/dayz_mcp/secure_launcher.py:116

por que importa: Unknown pegajoso no es un falso “fresh”, pero mata el canal. Tras el primer uso real, cada tool añade el bloque de texto y `status=unknown`. El operador aprende a ignorarlo; el contrato de `content` extra pasa a ser el de todas las respuestas, no solo la alarma. El informe trata el import tardío como unknown explícito y no reancla; no nombra que el happy path de `dayz_test_run` lo dispara.

como se dispara: Snapshot en `server.py:60`, antes de `build_app`. `native_launcher_backend` y `native_bundle` no están importados entonces (`secure_launcher.py:116` y `:234`). Un `dayz_test_run` que entra en `consume_registered_launcher` / `_load_verified_bundle` los carga. Desde esa llamada, `unreadable_reasons["dayz_mcp.native_launcher_backend"] = "loaded_after_server_snapshot"` (y los que arrastre `native_bundle`: `dayz_tools_paths`, `native_child_announcement`, `request_path_authority`, `native_debug_state`, …). Reopen del cliente MCP: vuelve a fresh hasta el siguiente launch.

confianza: alta en el grafo de imports; media en que *todo* `dayz_test_run` lo toque (pack_only/preflight puede no llegar al backend nativo). No ejecuté un launch real.

---

MAYOR — Dos SHA-256 de ~51 fuentes por cada tool, en un árbol OneDrive

donde: tools/dayz_mcp/server_freshness.py:58 y tools/dayz_mcp/server_freshness.py:133

por que importa: El dato de 6,30 ms es una lectura en caliente, no dos, y no es cota. El wrapper hace snapshot antes y después; `bridge_status` hace un tercero (`server.py:3336`). En OneDrive Files On-Demand, `read_bytes` hidrata. Peor caso: decenas de ficheros deshidratados o con oplock del sync → segundos o más por llamada, o `OSError` que tumba la tool (el handler no atrapa el fallo del snapshot). El event loop se libra (`to_thread`); la llamada MCP no. El daemon ya rechazó este diseño: `daemon.py:650` hace `stat(mtime, size, st_ino)` y solo hashea si el triple cambió; documenta el miss de reemplazo que conserva los tres (`:668`).

como se dispara: Árbol en `C:\Users\guill\OneDrive\Documentos\DayZ Projects\`, ficheros “solo en línea” o sync activo. Un agente que llama `bridge_status` / tools en ráfaga. Un save de editor que deniega la lectura a la vez. Lo que se pierde al cachear como el daemon: el caso que el test `test_same_size_and_mtime_edit_is_stale` construye a propósito. El incidente de las 14:30 fue un commit; mtime (y casi siempre el tamaño) cambia. Ese miss no es el bug de hoy.

confianza: alta en el recuento de lecturas y en que el árbol es OneDrive; baja-media en el número de milisegundos del peor caso (no medí un directorio deshidratado). El informe admite que no hay cota bajo almacenamiento bloqueado.

---

MAYOR — El segundo TextContent convierte en JSON inválido el texto concatenado, justo cuando hay alarma

donde: tools/dayz_mcp/server_freshness.py:141

por que importa: `content[0]` se conserva (los tests de `content[0]` siguen). `structuredContent` también. Quien concatena bloques, parsea el último, o asume `len==1`, se rompe. El primer bloque de una tool dict ya es JSON; el extra es otro objeto JSON. Concatenados: `{...}{"server_code_freshness":...}`, que `json.loads` rechaza. Eso ocurre en stale/unknown, que es cuando más hace falta entender el resultado. Tras el import perezoso de arriba, pasa en casi todas las llamadas. El brief dice “todas las respuestas”: en código, solo si hay stale o unreadable (`_call_marker` devuelve `None` si está fresco). Menos invasivo: solo `_meta` + `bridge_status.server_modules`; o un renglón de texto no JSON (`SERVER_CODE_STALE reopen_mcp_client`); o el bloque extra solo en `bridge_status`.

como se dispara: Tool que devuelve un dict (casi todas). Cliente que junta `content[].text` y parsea. O código que usa `content[-1]` como payload. `test_mcp_tools._content_json` usa `content[0]` y además `app.call_tool`, que no pasa por el wrapper. La sesión MCP real sí: `test_ui_error_diagnostics.py:165` ya recorre `content[1:]`.

confianza: alta en el cambio de contrato y en que fresh no añade bloque. No inspeccioné el host Cursor/Claude concatenando en producción.

---

MAYOR — Si el despacho deja de usar `request_handlers[CallToolRequest]`, el marcador muere en silencio

donde: tools/dayz_mcp/server_freshness.py:130

por que importa: En `mcp==1.27.2`, `_setup_handlers` corre en `FastMCP.__init__` (`fastmcp/server.py:239` / `:308`) y `run_stdio_async` no vuelve a registrar. Falta `_mcp_server` o la clave → AttributeError/KeyError al hacer `build_app`: ruidoso. El caso silencioso es otro: el dict sigue ahí, el despacho pasa por otro sitio (middleware, copia en `run()`, otra clave), o `response.root` deja de ser `types.CallToolResult` (`isinstance` en `:139` omite el marcador). Omitir en stale es un falso fresh. No hay canario en `server_modules` de que el wrapper sigue siendo el handler vivo. El pin `tools/requirements-mcp.txt:1` no detecta un bump que deje el atributo y cambie el camino.

como se dispara: Subir `mcp` por encima de 1.27.2, o un FastMCP que reasigne `request_handlers` al servir. Hoy, con 1.27.2 y el orden wrap-al-final-de-`build_app`, el wrap se mantiene.

confianza: alta en el análisis de 1.27.2 (código vendido en reviews + pin). Media en que un bump concreto falle en silencio y no con excepción; no corrí 1.28+.

---

MENOR — `tool_registry_source_stale` ahora es bool o dict; un `is True` se traga el unknown

donde: tools/dayz_mcp/server_freshness.py:90 y tools/dayz_mcp/server.py:3338

por que importa: `if payload["tool_registry_source_stale"]:` trata el objeto unknown como stale (conservador). `is True` / `is False` deja pasar unknown como “no stale”. El test adaptado acepta ambos (`test_mcp_tools.py:656`).

como se dispara: Consumidor que comparaba identidad de bool y una fuente ilegible.

confianza: alta en el contrato; no tengo un consumidor de producción que use `is True`.

---

Pregunta 1 — Falso fresco

El informe ya cubre, y lo comprobé: terceros fuera de `dayz_mcp.*`; `.pyc`/no-`.py` → digest `None` → unknown, no fresh; monkeypatch en memoria con fuente igual; edición revertida entre los dos snapshots; ventana entre import de una dependencia y `server.py:60`. En esos, no veo un falso fresh extra.

Lo que no reconoce: el runner de playbooks (hallazgo 1). Un módulo `dayz_mcp.*` importado después no dice fresh: dice unknown (hallazgo 2). No vi `.pyc` que deje pasar código viejo como fresh: si `__file__` es `.py` y los bytes coinciden con el snapshot, la memoria es esa fuente; si no se puede leer, unknown.

Pregunta 2 — Coste

Sí hay que cachear, al estilo `daemon.py:650` (stat triple, hash solo si se mueve). Lo que se pierde: el edit adversarial de mismo tamaño y mtime (y el ACL deny sobre el mismo objeto, que el daemon también declara miss). No se pierde el commit de las 14:30. El peor caso OneDrive no está medido; 6,30 ms no sirve de cota. Bloqueante en este árbol.

Pregunta 3 — Segundo bloque de texto

El riesgo existe, y no está justificado como está (JSON pegado a JSON). Fresh no añade bloque; stale/unknown sí. Forma menos invasiva: `_meta` + `bridge_status`; texto no JSON si el modelo tiene que verlo. `content[0]` de este repo sigue siendo el payload. No es bloqueante si el canario y el unknown pegajoso se cierran (si no, el extra bloque se vuelve permanente).

Pregunta 4 — API privada

Hoy: fallo ruidoso al instalar si desaparece la superficie. Silencioso si el dict queda como fósil o si `isinstance` falla. Eso hay que convertirlo en unknown (canario: el handler actual `is` el wrapper), no en omisión.

Pregunta 5 — Fuga

Aquí no veo un secreto que no debiera salir. Miré el marcador: `server_pid`, `server_started_at`, nombres de módulos, reason codes (`source_unreadable_now`, etc.), no rutas. Bind local. El PID ya viaja en la identidad de sesión (`server.py:1207`). Nombres de módulos son el propio paquete. No publican hashes ni keyfile.

Pregunta 6 — El NO de T2

El NO a recargar el registro FastMCP / `daemon_reload(scope="tools")` es sólido. Closures de schema (`server.py:1930` y `:1950`), `isinstance` del runtime (`:3320`), lease/ControlClient (`:1205`, `control_client.py:161`), heartbeat de box (`:3568`), stdio (`:5411`) y loopback embedded (`:910` / `:5413`) no son hojas. Que los runs vivan en el daemon no prueba un traspaso a mitad de launch (`process_lifecycle.py:3358`).

Subconjunto seguro que descarta demasiado rápido, si T2 era “aplicar código nuevo”:
1. `playbook_tool.py:61-79`: tirar `_runner` y volver a `exec_module`. No toca FastMCP, leases ni stdio.
2. En `--client`, salir del proceso stdio y dejar que el host reabra (`reopen_mcp_client`; ya existe `_release_and_exit` en `server.py:5363`). El daemon y la partida no van en ese proceso. Eso no es un verbo de recarga in-process; es el remedio que ya publican, automatizado.

No pediría implementar T2. El NO al hot-reload del registry se sostiene.

---

**ACEPTAR CON CAMBIOS.** Bloqueantes: (1) meter en el censo lo que se carga fuera de `dayz_mcp.*` / `tools/` (`dayz_playbook_runner`, y cualquier otro `spec_from_file_location` del proceso de tools); (2) importar en el instante del snapshot el closure real de producción (`native_launcher_backend`, `native_bundle`, …) para que unknown no sea el estado estable tras un `dayz_test_run`; (3) no hashear las ~51 fuentes dos veces por llamada — `stat` primero, como el daemon; (4) canario de que el wrapper sigue siendo el handler de `CallToolRequest`, y si no, `unknown`, nunca omisión. El segundo bloque de texto y el NO de T2 no son bloqueantes si (2) se cumple.