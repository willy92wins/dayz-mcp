# Plan: distribuir dayz-mcp por Workshop e instalación para agentes

> Copia del plan del hilo de distribución a 25/11/2026, sobre `main` f767dd8, con la pestaña «Modo instalado» como anexo al final. Las fechas sin año son de 2026.

## Resumen y recomendación

Recomiendo dos canales y una orden que los casa. El PBO firmado va al Workshop, con un clic de la persona. La mitad Python va como wheel en la release de GitHub: el agente la instala con `uv` y la monta con `dayz-mcp setup --json`.

- **Qué gana.** De clonar el repo y montar cinco piezas a mano se pasa a un clic y cinco órdenes, seis si falta `uv`, sin Python instalado de antemano.
- **Qué no cambia.** El modelo de seguridad: solo loopback, clave por instalación y lista blanca. Tampoco el nivel 2: `dayz_test_run` y su lanzador sellado siguen saliendo del clon.
- **El resellado.** No afecta al PBO del Workshop. Lo nuevo es que Steam puede cambiar el PBO sin avisar a la mitad Python. `doctor` lo detecta y da la orden de arreglo.
- **El coste serio.** El modo instalado, ya leído sitio a sitio el 12/11: de 14 sitios que miran al árbol fuente, dos impiden arrancar y uno pide un cambio pequeño. La sonda S4 lo midió ese día en tu PC: funciona con esas correcciones y creando el entorno con el módulo venv de Python en vez de con uv venv, que una segunda pasada comprobó. Ningún cambio toca los módulos sellados. El detalle está en el anexo «Modo instalado».
- **El orden.** Las cinco sondas están hechas desde el 19/11. Ahora, en paralelo, el PBO firmado en el Workshop y la mitad Python, que salen juntos en la release v1.6. Aceptar un solo cliente MCP es la fase 3, después de la 2.
- **De ti.** Publicar el item con el Publisher y guardar la clave fuera de línea. E7 y E8 los confirmaste el 25/11, y también que la fase 3 va después de la 2.

## Punto de partida: cómo se instala hoy la v1.5

La release v1.5 solo reparte el PBO. Todo lo demás sale de clonar el repo y pide herramientas que un usuario nuevo no tiene. Leído en `main` (`cac2e8d` y posteriores), `QUICKSTART.md`, `README.md` y `docs/RELEASE.md`.

| Pieza | De dónde sale hoy | Qué cuesta |
| --- | --- | --- |
| PBO `DayZ_MCP.pbo` | Zip de la release de GitHub, junto a `SHA256SUMS.txt` y `VERSION.json` | Descargar, verificar y copiar a `@DayZ_MCP\Addons`. No trae `.bisign` ni `.bikey` |
| Servidor MCP en Python | Clon de git. Corre desde `tools/` del árbol fuente | Git, Python 3.11 o más y `install-mcp.ps1`: venv, 4 dependencias fijadas y `pip install -e` |
| Registro en el cliente MCP | `install-mcp.ps1 -Register` | Exige Claude Code y Codex CLI a la vez, los dos nativos |
| `dayz_mcp.json` con la clave | El instalador, si recibe `-ServerProfiles` y `-ClientProfiles` | Hay que conocer las carpetas de perfiles del servidor y del cliente |
| Carga del mod | `-mod=` añadido a mano en servidor y cliente | Ruta con espacios y comillas en el argumento entero |
| Que el agente arranque el juego (`dayz_test_run`) | Lanzador nativo compilado en la propia máquina | MSVC, Windows SDK, política local y registro sellado |

Cinco barreras concretas para alguien de fuera:

1. **La mitad Python no viaja en la release.** Además, instalada desde un wheel no arranca. El wheel no lleva los playbooks, y el servidor los carga al importar (`playbook_tool.py:28`, `server.py:404`). Y el daemon solo acepta el `python.exe` de `tools\.venv-mcp` (`daemon.py:313-322`). Tampoco lleva `mcp-grab.ps1` (`mcp_capture.py:102`). Medido en tu PC el 12/11; las líneas son de `main` f767dd8.
2. **Hacen falta dos clientes.** Con solo Claude o solo Codex el daemon responde `daemon_provenance_incomplete` (`host_config.py:417-421`).
3. **El PBO va sin firmar.** Un servidor con `verifySignatures=2` no admite a un cliente que lo cargue.
4. **Las dos mitades tienen que casar.** El daemon exige la versión de puente 13 y el mismo hash del contrato de argumentos. Si no, `version_mismatch` o `arg_contract_mismatch`. Hoy eso lo cuida quien instala.
5. **El sellado es por máquina.** El lanzador fija los hashes de `DayZDiag_x64.exe` y `binarize.exe`. Cada actualización de Steam de DayZ o de DayZ Tools obliga a resellar (15/10, 21/10, 3/11 y 4/11 en nuestro PC).

## El PBO por Steam Workshop

El PBO de la release puede ir al Workshop sin reconstruirlo: le faltan la firma, la clave pública y un `mod.cpp`. Ninguna actualización de Steam del juego obliga a refirmarlo ni a republicarlo.

### Qué se sube

| Fichero del item | De dónde sale |
| --- | --- |
| `addons/DayZ_MCP.pbo` | El zip de la release, con el mismo sha256 que `VERSION.json` |
| `addons/DayZ_MCP.pbo.<clave>.bisign` | `DSSignFile` con nuestra clave privada |
| `keys/<clave>.bikey` | `DSCreateKey`, una sola vez |
| `mod.cpp` | Nuevo: nombre, autor y versión |
| `meta.cpp` | Lo crea el Publisher al publicar |

### Firma y bikey

- `DSCreateKey` y `DSSignFile` están en `DayZ Tools\Bin\DsUtils`. Dan una clave privada (`.biprivatekey`), una pública (`.bikey`) y un `.bisign` por PBO ([wiki de la comunidad](https://stardz-team.github.io/DayZ-Modding-Wiki/en/08-tutorials/07-publishing-workshop)).
- La firma es un fichero aparte, así que el sha256 del PBO no cambia. El PBO de la v1.5 se puede firmar tal cual: medido el 12/11 en tu PC con una clave de prueba, y DSCheckSignatures da la firma por buena.
- La clave privada no sale del PC ni entra en el repo, y necesita copia de seguridad. Si se cambia, cada servidor tiene que sustituir la `.bikey`.
- La firma importa cuando el servidor lleva `verifySignatures=2` y el cliente carga el mod: sin nuestra `.bikey` en `keys\` el cliente es expulsado ([gestión de mods](https://stardz-team.github.io/DayZ-Modding-Wiki/en/09-server-admin/10-mod-management)). La guía oficial pone `verifySignatures = 0` para probar mods sin firmar ([Bohemia](https://community.bistudio.com/wiki/DayZ:Modding_Basics)).
- Hoy `tools/` no tiene ningún paso de firma: ninguna referencia a DsUtils en scripts ni en el runbook de release.

### Publicación

- El Publisher de DayZ Tools pide título, descripción, imagen de 512 por 512, carpeta del mod, etiquetas y visibilidad. Va por el cliente de Steam abierto, con su sesión, o sea, tu cuenta, sin otro login: lo dicen sus cadenas (`SteamAPI_Init`, «Publisher requires that the Steam client is online»), leídas el 12/11 sin ejecutarlo. Así que no debería sacarte de Steam, aunque eso no está medido. Solo tiene ventana: las DayZ Tools no traen el `PublisherCmd` de línea de órdenes que sí traen las de Arma 3.
- La misma wiki describe `steamcmd +workshop_build_item` con un `.vdf` (appid 221100, `contentfolder`, `publishedfileid` para actualizar). Funciona para DayZ: el 12/11 creó en tu PC el item oculto de prueba en 13 s, con tu sesión guardada. Pero su login es otra sesión de tu cuenta, y en el mismo PC echa al cliente de Steam abierto («Session Replaced», en el mismo segundo). El cliente no vuelve solo, y las partidas del PC se quedan sin autenticación de Steam (kick 49) hasta que vuelves a entrar. En tu PC, SteamCMD con tu cuenta solo vale con Steam cerrado y sin partidas.
- El PBO que se sube sale de los ficheros de la release, comprobado con `SHA256SUMS.txt` y `pbo_provenance.py`. Nunca de `P:\Mods`: el 6/11 otra tubería cambió ese PBO compartido por el build del antihack (`0b1f64a8`, otro contrato) sin dejar rastro en git.

### Resellado y actualizaciones

| Qué cambia | Efecto | Qué toca hacer |
| --- | --- | --- |
| Steam actualiza DayZ o DayZ Tools | La firma del PBO sigue valiendo. El lanzador nativo sellado queda desfasado (`NATIVE_BUNDLE_EXTERNAL_DRIFT`) | Nada en el Workshop. Resella solo quien haya construido el lanzador |
| Publicamos versión nueva del item | Steam cambia el PBO a cada suscriptor sin avisar a la mitad Python | Subir la mitad Python a la par. Si no, `version_mismatch` o `arg_contract_mismatch` |
| El juego rompe compatibilidad de scripts | El PBO deja de compilar | Release nueva y versión nueva del item, como hoy |

El sello no fija el PBO: el 4/11 cambió el PBO vivo (`B28CEC03` a `6989BC95`) y los lanzadores no se resellaron. Lo nuevo que trae el Workshop es la segunda fila, y se resuelve en la fase 3.

### Antihack y abuso

- El mod es inerte sin `dayz_mcp.json` en el perfil o en la misión (`MCPBridge.c:350-362`) y solo acepta `http://127.0.0.1:<puerto>/` (`MCPBridge.c:378-382` y `7655-7699`, `MCPClientBridge.c:859-863` y `9741-9785`). Publicarlo no da control remoto sobre el servidor de nadie.
- DayZDiag no corre con BattlEye (guía oficial). En retail, un mod firmado entra si el servidor tiene su clave.
- Riesgo que queda: el puente de cliente automatiza la cámara, la entrada y la interfaz del propio jugador. En un servidor público solo carga si el administrador instaló nuestra `.bikey`. La ficha del Workshop debe decir que es una herramienta de desarrollo.

### Suscripción en cliente y servidor

- **Persona.** Abre la ficha y pulsa Suscribirse. `steam://url/CommunityFilePage/<id>` abre la ficha, pero ninguna orden `steam://` suscribe sin clic ([Valve](https://developer.valvesoftware.com/wiki/Steam_browser_protocol)). Es el único paso manual de todo el flujo.
- **Dónde queda.** En `steamapps\workshop\content\221100\<id>\`. El launcher de DayZ crea `!Workshop\@<nombre>` como junction a esa carpeta (visto en nuestro PC, `plans/2026-08-16-workshop-mods-accreditation.md`).
- **Montaje local, el de referencia.** Servidor y cliente están en la misma máquina: una suscripción vale para los dos, con el mismo `-mod=<ruta>`.
- **Solo servidor.** `-serverMod=<ruta>`: el cliente no necesita el mod ni la clave, y se pierden los verbos de cliente. `docs/SERVERMOD.md` sigue sin verificar en juego.
- **Servidor sin cliente de Steam.** `steamcmd +login <cuenta> +workshop_download_item 221100 <id>`; `anonymous` no vale. Pide credenciales, así que para un agente sin persona delante se queda el zip de la release. Y en una máquina con el cliente de Steam abierto con la misma cuenta, ese login lo echa.
- **Nombre.** Hoy `pack-addon.ps1` escribe una carpeta real `!Workshop\@DayZ_MCP`, la que usa LIVE. Si el item se llama igual, el junction choca con ella. Se mide en la fase 0.

## La parte que no es PBO, con el agente como instalador

Un agente no necesita un instalador gráfico. Necesita poco que bajar, una orden que lo monte todo y otra que lo compruebe, las dos sin preguntas y con salida JSON. La vía que mejor lo da es un wheel en la release de GitHub instalado con `uv`.

### Opciones comparadas

| Opción | Qué hace el agente | Cambio en el repo | A favor | En contra y riesgos |
| --- | --- | --- | --- | --- |
| **A. Wheel en la release y `uv`** (elegida el 12/11) | `uv python install`, un venv con el módulo `venv` de ese Python, `uv pip install --require-hashes -r <lock>` y luego `dayz-mcp setup --json` | Punto de entrada `dayz-mcp`, estado fuera del árbol fuente, y el wheel y su lock con hashes como ficheros nuevos de la release | Pesa 1 MB. `uv` trae su propio Python. El wheel y sus dependencias van con hash exigido. La confianza sigue anclada en la release | Cambia el intérprete aprobado del daemon y otros dos sitios, sin tocar los módulos sellados. `uv` pasa a ser requisito |
| A2. Lo mismo desde PyPI | `uv tool install dayz-mcp==X` | Lo de A, más cuenta de PyPI y publicación desde CI | Orden más corta. PyPI publica hashes. Abre la puerta al registro oficial de MCP | Cuenta y secreto nuevos que cuidar. El nombre está libre hoy y habría que reservarlo |
| B. Zip de `tools/` y el instalador actual | Bajar, verificar, extraer y lanzar `install-mcp.ps1` | Solo `make_release.py` y el runbook | No toca el paquete. Sale en días | Pide Python 3.11 ya instalado y PowerShell sin restricción de scripts. Son 6 MB con piezas que sobran. Al actualizar, código y estado están mezclados |
| C. Scripts dentro del item del Workshop | Copiar del item a `%LOCALAPPDATA%` e instalar | Como B, más el paso de publicación | Un solo canal. Las dos mitades casan siempre | El Workshop no revisa ni firma scripts: una cuenta de Steam comprometida empuja código a cada agente. Wallpaper Engine anunció el 1/7/2026 que retiraba los ejecutables del Workshop por ese motivo ([PC Guide](https://www.pcguide.com/news/wallpaper-engine-puts-an-end-to-exe-wallpapers-after-malware-spreads-on-the-steam-workshop/)) |
| D. Exe empaquetado o instalador MSI | Bajar y ejecutar un `.exe` | Build y firma de código nuevos | No pide Python. Doble clic | Opaco para el agente. SmartScreen y antivirus con binarios sin firmar. Certificado de pago. `uv` ya quita el requisito de Python |
| E. Paquete `.mcpb` de Claude Desktop | Doble clic de una persona | Manifiesto v0.4 con servidor de tipo `uv` | Un clic en Claude Desktop | Solo sirve a Claude Desktop y no es instalación por agente. El tipo `uv` es experimental ([mcpb #158](https://github.com/modelcontextprotocol/mcpb/pull/158)) |

### Por qué A

- El trabajo del agente baja a cinco órdenes, y actualizar es repetirlas.
- Separar el estado (clave, registro, auditoría) del código es lo que hace seguras las actualizaciones. Hoy viven juntos en `tools/`.
- Es el mismo cambio que pedirían después PyPI o `.mcpb`, así que no cierra ninguna puerta.
- B queda como reserva. S4 no la ha hecho necesaria: el modo instalado pide tres correcciones y ninguna toca los módulos sellados.

### Medido aquí el 11/11 (uv 0.11.32, sobre `main`)

- `uv build --wheel tools/` produce `dayz_mcp_tools-1.5-py3-none-any.whl`: 964 842 B, 108 módulos, sin punto de entrada y sin datos. Faltan los `playbooks/` y `mcp-grab.ps1`.
- `uv tool install` de ese wheel falla con «No executables are provided by package».
- Con un paquete de prueba, `uv tool install "pkg @ file://...whl#sha256=<hash malo>"` instala igual. `uv pip install --require-hashes` sí lo rechaza. El 12/11 se midió el resto: `uv tool install` no exige hashes de ninguna forma, así que la receta pasa a un venv propio con `uv pip install --require-hashes`. Las pruebas están en el anexo «Modo instalado».
- `dayz-mcp`, `dayz_mcp` y `dayz-mcp-tools` devuelven 404 en PyPI: están libres.
- El arranque desde un wheel no se pudo probar en Linux: el paquete carga `ctypes.WinDLL` al importar. Lo probó la sonda S4 el 12/11, en Windows: arranca con tres correcciones.

### Autoconfiguración del cliente

`install_mcp.py --register` ya registra el servidor en Claude y en Codex con transacción, journal y vuelta atrás. Le faltan dos cosas para servir fuera: funcionar instalado fuera del árbol y admitir un solo cliente. La segunda es una decisión tuya, porque afloja una regla de seguridad.

## Propuesta: instalación de punta a punta

La persona pulsa Suscribirse una vez. El agente hace el resto con cinco órdenes y recibe un JSON que dice si el puente está listo.

El PBO llega por Steam y la mitad Python por la release. `setup` es el único punto que conoce las dos, y `doctor` avisa cuando dejan de casar.

### Lo que hace el agente

1. Lee `llms-install.md`, en la raíz del repo. La ficha del Workshop y el README apuntan a él.
2. Crea el entorno en una carpeta bajo `%LOCALAPPDATA%\DayZ_MCP` propia de cada versión de dayz-mcp, con tres órdenes. Baja el Python con `uv python install`, en la versión que fija `llms-install.md`, así que es el mismo en todas las máquinas. Saca su ruta real: `uv python find --managed-python` da un junction, que se resuelve con `os.path.realpath`. Y crea el entorno con el módulo `venv` de ese Python: `<python> -m venv --without-pip <carpeta>`. No con `uv venv`: su lanzador cambia de carpeta al arrancar, y entonces el daemon instalado estorba a otras instancias. Medido el 12/11 en tu PC. Si no hay `uv`, antes `winget install astral-sh.uv`.
3. Instala con `uv pip install --python <carpeta>\Scripts\python.exe --link-mode=copy --require-hashes -r <lock de la release>`. El lock fija el wheel y sus 32 dependencias, cada uno con su hash: con un byte cambiado, `uv` no instala nada. Con `--link-mode=copy`, los ficheros se copian en vez de enlazarse a la caché de `uv`.
4. Lanza `dayz-mcp setup --json` por su ruta dentro del entorno, la última orden, que:
   - encuentra Steam, DayZ, el item del Workshop y los perfiles, o los recibe por argumento;
   - comprueba que el PBO es el de su misma release, por el sha256 de `VERSION.json`;
   - genera la clave si no existe y escribe `dayz_mcp.json` en los perfiles de servidor y de cliente;
   - registra el servidor en Claude Code y en Codex, que hoy hacen falta los dos (la fase 3 decide si basta uno);
   - devuelve la ruta exacta para `-mod=` y lo que falte, cada cosa con su nombre y su remedio.
5. Pide que se reinicie el cliente MCP, que es lo único que no puede hacer él.
6. Con el juego arrancado llama a `session_status`, que levanta el daemon, y después a `bridge_status`. `ready: true` cierra la instalación.
7. Más adelante, `dayz-mcp doctor --json` dice si algo se ha desfasado y con qué orden se arregla.

### Qué cambia en cada pieza

| Pieza | Cambio |
| --- | --- |
| Release de GitHub | Dos ficheros más: el wheel y su lock con hashes, cubiertos por `SHA256SUMS.txt`. El zip del addon pasa a llevar `.bisign`, `Keys/`, `mod.cpp` y `VERSION.json`, con la misma estructura que el item |
| Paquete Python | Punto de entrada `dayz-mcp` con `setup` y `doctor`. Modo instalado, con el estado en `%LOCALAPPDATA%\DayZ_MCP`. `playbooks/`, `mcp-grab.ps1` y `VERSION.json` viajan dentro del wheel, y también el instalador. El modo de árbol fuente queda igual |
| Item del Workshop | PBO firmado, clave, `mod.cpp` y `VERSION.json`. La descripción enlaza a `llms-install.md` |
| Documentación | `llms-install.md` nuevo. `QUICKSTART.md` pone la vía del Workshop primero |
| Runbook de release | Pasos de firma y de publicación del item |

### Dos niveles

- **Nivel 1, el que cubre este plan: el puente.** El agente habla con un juego que arranca la persona, o él mismo con la línea que le da `setup`.
- **Nivel 2, como hoy y desde el clon: `dayz_test_run`.** El lanzador nativo se compila con MSVC en cada máquina y se resella tras cada actualización de Steam. No entra en la distribución fácil.

## Fases y criterios verificables

Cuatro fases. La 0 solo medía y está cerrada desde el 19/11; con sus resultados se corrigió este plan. El grill de plan del 25/11 está al final de esta sección. La revisión del plan (R22) la hace Neon sobre su copia en `plans/2026-11-11-distribucion.md`, porque Sol no revisa hasta el reinicio de Codex del 27/11. Las fases 1 y 2 van en paralelo y salen juntas en la release v1.6. Trazan a los criterios nuevos E7 y E8 de `product-spec.md`, confirmados el 25/11.

Asientos: implementa una lane del PC elegida con la skill `delegar`: Muse para el Python con tests de Windows, Grok de reserva y una lane gratis para la documentación. La puerta la corre el PC, y la clave privada nunca llega a un worker. Revisa otra familia solo lo que la evidencia deja sin probar, según `evidencia-y-revision.md`: una pasada dirigida al intérprete aprobado y una ronda a los rechazos de `make_release.py`. Hasta el reinicio de Codex del 27/11 revisa Claude lo que no haya escrito Claude, y Neon lo demás; desde el reinicio, Sol. Cada fase va en su worktree y con su PR.

### Fase 0: sondas, sin código en `main`

| Sonda | Qué se hace | Pasa si | Si falla |
| --- | --- | --- | --- |
| S1 Firma | Firmar el PBO de la v1.5 con una clave de prueba. Servidor con `verifySignatures=2` | El sha256 del PBO sigue siendo `F27A8AED…D659`. Con la `.bikey` el cliente entra; sin ella el RPT del servidor registra la expulsión | Se anota si el exe diag ignora la firma y la prueba se repite con el cliente retail, en una ventana sin partidas |
| S2 Item oculto | Publicar un item oculto con PBO, firma, clave, `mod.cpp` y `VERSION.json`. Suscribirse en el PC | Cada fichero bajado tiene el sha256 del subido, también `VERSION.json`. Quedan escritos el nombre del junction y el `meta.cpp` | Si los ficheros extra no llegan, `VERSION.json` se lee de la release por red |
| S3 SteamCMD | Actualizar ese item con `workshop_build_item` y un `.vdf` | La versión nueva llega al suscriptor con los sha256 esperados | La publicación se queda en el Publisher, a mano |
| S4 Wheel en Windows | Instalar el wheel de `main` con su lock en un venv fuera del árbol, creado por `uv` sobre el Python que él baja, y arrancar `--client` contra un daemon aislado: instancia con nombre y puerto propio, sin juego | Las siete comprobaciones de Modo instalado dan lo esperado. La que más pesa: con el wheel sin tocar, el daemon se para con `daemon_python_not_approved`, salida 78 | Se rehace la estimación de la fase 2 o se entrega antes la opción B |
| S5 Actualización en caliente | Publicar otra versión del item con una partida abierta | Queda escrito cuándo cambia el PBO en disco y qué `reason` da `bridge_status` | No aplica: es una medida |

S1, S2, S3 y S5 necesitan tu sesión de Steam. Ninguna toca LIVE: el item de prueba lleva otro título y no se carga junto al PBO de desarrollo.

Resultados a 19/11:

- **S4, hecha.** El wheel instalado con `uv` en un venv propio arranca cliente y daemon, con tres correcciones: los playbooks dentro del wheel, el intérprete aprobado tomado del propio venv y un venv sin el lanzador de `uv`. La tercera la comprobó una segunda pasada: con un venv del módulo venv de Python, el daemon instalado no estorba a LIVE ni a experimental. También responde a la pregunta de la actualización: ante el daemon de la versión anterior, el cliente nuevo da un error con nombre, no levanta otro y arranca el suyo cuando el viejo sale por inactividad. Las siete comprobaciones están en el anexo «Modo instalado».
- **S1, hecha.** Firmado con una clave de prueba, el PBO de la v1.5 conserva su sha256 (`F27A8AED…D659`) y `DSCheckSignatures` da la firma por buena. Con DayZServer retail y `verifySignatures = 2`, sin la `.bikey` en `keys` el servidor echa al cliente al entrar (kick 120); con ella, entra. A los 23 s lo echó BattlEye («Game restart required») aunque el cfg llevaba `BattlEye = 0`: es otra puerta y queda sin investigar.
- **S2, hecha.** SteamCMD creó el item oculto de prueba en 13 s, con tu sesión guardada y sin avisos: el Workshop de DayZ acepta subidas por SteamCMD. Al suscribirte, Steam bajó en unos 2 s los mismos 5 ficheros, con los mismos sha256. Después tu cliente borró esa copia dos veces tras un «Access Denied» al pedir los datos del item privado (16/11 y 18/11). El junction y el `meta.cpp` del launcher quedan sin medir.
- **S3, la actualización va con el Publisher.** El login de SteamCMD con tu cuenta te sacó del cliente de Steam («Session Replaced») y las partidas del PC salieron con kick 49 hasta volver a entrar; las DayZ Tools no traen un Publisher de línea de órdenes. El 19/11 el Publisher actualizó el item en unos 6 s; quiere la carpeta del mod, porque con la de `addons` falla sin subir nada. La llegada al suscriptor se mide en la fase 1: con el item privado, tu Steam no apuntó la suscripción nueva.
- **S5, pasa a la fase 1.** Se mide con el item público, publicando una actualización con una partida abierta: el 19/11 el item privado no se comportó en tu cliente como uno público.

### Fase 1: PBO firmado e item público

- **Entrega.** La clave definitiva, la `.bikey` pública y `mod.cpp` en `tools/release/`, el paso de firma en `docs/RELEASE.md` y su comprobación en `make_release.py`. El zip y el item tienen la misma estructura: `@DayZ_MCP` con `Addons\DayZ_MCP.pbo`, su `.bisign`, `Keys\DayZ_MCP.bikey`, `mod.cpp` y `VERSION.json`. Publicas tú con el Publisher, una vez por release y apuntando a esa carpeta, porque SteamCMD con tu cuenta te saca de Steam.
- **Pasa si.** Cada fichero del item tiene el mismo sha256 que en el zip, y el PBO el de `VERSION.json`. `DSCheckSignatures` dice «is OK» con la `.bikey` del repo. Con DayZServer retail y `verifySignatures = 2`, como en S1, el cliente suscrito entra sin kick 120 con nuestra `.bikey` en `keys`, y sale con kick 120 sin ella. `bridge_status` da `ready: true` en una instancia aislada, con nombre y puerto propios, lease y la caja libre, que carga el mod desde la ruta del Workshop.
- **Negativos.** `make_release.py` sale con 2 y un código con nombre, como hoy con `pbo_missing`. Sin `.bisign`, `bisign_missing`. Si el `.bisign` no empieza por la `.bikey` del repo, `bisign_wrong_key`: es una comparación en Python y vale también en Linux. Si `DSCheckSignatures` no dice «is OK» para ese `.bisign`, `bisign_invalid`, por ejemplo con el PBO cambiado después de firmar; se lee su salida porque su exit code es 0 en todos los casos. Sin DsUtils, `signature_check_unavailable`. La clave privada no sale en `git ls-files`, ni en el zip ni en el item, y `.gitignore` excluye `*.biprivatekey`.
- **Evidencia que te traeré.** Los tests de `make_release.py` con una clave de prueba: un caso bueno y los cuatro rechazos, cada uno visto fallar sin su comprobación. La salida de `DSCheckSignatures` en el PC con la firma buena y con el PBO cambiado. Los sha256 del zip, del item y de `VERSION.json`. El RPT del servidor en las dos entradas y el JSON de `bridge_status`.
- **Medidas que vienen de la fase 0.** El item nace Privado y las comprobaciones con Steam se hacen el día de la suscripción, porque en S2 tu cliente borró días después la copia del privado. Pasa a Público al cerrar la fase 1. Con el item ya público, en su primera actualización, se miden la llegada al suscriptor (sha256 de cada fichero, junction y `meta.cpp` del launcher) y S5, con una partida abierta (cuándo cambia el PBO en disco y qué `reason` da `bridge_status`). Son medidas, no puertas.

### Fase 2: mitad Python instalable por un agente

- **Entrega.** Wheel con el punto de entrada `dayz-mcp` y con los playbooks y `mcp-grab.ps1` dentro, modo instalado, `setup --json`, comprobación de mitades en `doctor --json`, `llms-install.md`, y el wheel y su lock con hashes en la release.
- **Pasa si.** En un perfil limpio del PC, aislado como en S4 (`USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, `TEMP`, `CODEX_HOME` y `PATH` propios, sin Python ni `uv`), con Steam, DayZ, el item suscrito, y Claude Code y Codex instalados, las órdenes de `llms-install.md`, seis como mucho, acaban con `setup` en exit 0. Su JSON trae `ok: true`, la ruta para `-mod=` y la lista `missing` vacía. Al arrancar el juego, `bridge_status.ready` es `true`. Una segunda pasada de `setup` no cambia la clave ni duplica el registro. Bajo el venv solo aparecen `__pycache__` y `approved-launchers.lock`; la clave, la configuración y los diarios quedan en `%LOCALAPPDATA%\DayZ_MCP`. Con el daemon instalado vivo, el daemon de otra instancia arranca sin esperar: el clasificador de arranque da `not_writer` para todos sus procesos.
- **Negativos.** Sin item suscrito: exit distinto de 0 y `workshop_item_missing` con la URL de la ficha. PBO de otra release: `halves_mismatch` con la orden de arreglo, y esa orden deja `ready: true`. Wheel o dependencia con un byte cambiado: `uv pip install --require-hashes` sale distinto de 0 y no instala nada. `VERSION.json` ausente: error con nombre, sin adivinar. Daemon lanzado con un `python.exe` que no es el del venv del paquete: sigue dando `daemon_python_not_approved`, salida 78. Con un solo cliente MCP instalado: error con nombre que dice cuál falta, y nada queda registrado a medias. Fuera de Windows: `platform_unsupported`, sin tocar nada.
- **Regresión.** En el árbol fuente la suite actual pasa igual en Windows y en Linux, y un fixture comprueba que las rutas resueltas son las de hoy. `packaged-modules.lock.json` y `packaged-modules.linux.lock.json` quedan byte a byte igual: no se tocan `daemon_contract.py` ni `host_config.py`. La rama POSIX del intérprete aprobado (`daemon.py:323-330`) no cambia. Lo que cambia es su rama de Windows, parte de lo que acredita el criterio H10: la revisión tiene que centrarse ahí.
- **Evidencia que te traeré.** La suite en verde en la CI, en Windows con 3.11 y 3.14 y en Linux. Los tests nuevos del modo instalado, vistos fallar sin su cambio: el intérprete aprobado en los dos modos, los playbooks y `mcp-grab.ps1` dentro del wheel y el `python.exe` ajeno con 78. El sha256 de los dos locks antes y después. Y el registro de la pasada en el perfil limpio, con cada orden y su exit code, el JSON de `setup` y cada negativo con su código.
- **Mapa.** Qué sitio cambia y cuál no, uno por uno, en el anexo «Modo instalado».

### Fase 3: un solo cliente MCP, después de la 2

- **Entrega.** El daemon arranca con un único registro cuando la instalación se hizo en ese modo de forma explícita.
- **Pasa si.** Con solo Claude registrado, `bridge_status` responde.
- **Negativos.** Dos registros que difieren siguen dando `daemon_provenance_conflict`. Ninguno sigue dando `daemon_provenance_unavailable`. Una instalación de dos clientes a la que le falta uno sigue dando `daemon_provenance_incomplete`.
- **Ojo.** Toca H1 (topología de clientes) y H10 (acreditación del daemon), criterios de `product-spec.md`, ya cerrados. Pide grill de producto antes de planificarla: decidido el 25/11, al cerrar la fase 2 te lo traigo antes de implementar.

### Grill del plan, 25/11, sobre `main` f767dd8

**Gate de product-spec.** Las fases 1 y 2 no trazaban a ningún criterio: E3 cubre la instalación desde el clon con `install-mcp.ps1` y está cumplida desde el 10/6. Por eso el criterio nuevo va antes de las dos fases, no solo de la 2. Criterios nuevos del grupo E de `product-spec.md`, confirmados por ti el 25/11:

- **E7, PBO firmado en el Workshop.** Cada release publica en el item DayZ-MCP el PBO firmado, su `.bisign`, `Keys\DayZ_MCP.bikey`, `mod.cpp` y `VERSION.json`, y el PBO del item y el del zip tienen el sha256 de `VERSION.json`. Con `verifySignatures = 2`, el servidor admite al cliente suscrito si tiene nuestra `.bikey` en `keys`, y sin ella lo echa con kick 120. `make_release.py` rechaza un PBO sin firma o firmado con otra clave, y la clave privada no está en git ni en la release.
- **E8, mitad Python instalable por un agente.** En un perfil de Windows sin Python ni `uv`, con Steam, DayZ, el item suscrito, y Claude Code y Codex, un agente sigue `llms-install.md` con seis órdenes como mucho: `dayz-mcp setup --json` sale con 0 y, con el juego arrancado, `bridge_status.ready` es `true`. `doctor --json` nombra un PBO de otra release con la orden que lo arregla. Desde el clon todo sigue como hoy.
- **Intención.** Que quien tenga DayZ y un agente monte el puente sin clonar ni compilar, con la misma seguridad: solo loopback, clave por instalación y lista blanca.
- **Fuera de alcance.** El lanzador nativo de `dayz_test_run`, el modo instalado en Linux y macOS, PyPI, MSI y `.mcpb`, y suscribirse o arrancar el juego sin la persona.
- **Referente.** La anatomía de un mod del Workshop de DayZ, medida en S2: PBO y `.bisign` en `addons`, `.bikey` en `keys` y `mod.cpp`. Y la convención `llms-install.md` de los marketplaces de MCP para que instale un agente.

**Desafío.** E8 pide hoy Claude Code y Codex a la vez, y eso choca con su intención: quien solo tiene uno no puede instalar. Lo resuelve la fase 3, que toca H1, H10 y `host_config.py`, que está en el lock sellado. Decidido el 25/11: entra después de la 2, con su grill de producto antes de implementar.

**Lo que decido por defecto.** Si alguna no te vale, dímelo.

1. Una sola release, v1.6, para las dos fases. Sale cuando estén mergeadas las dos PR: la corta el PC con `docs/RELEASE.md`, y te aviso antes de crear la etiqueta. Publicas el item una vez.
2. La firma es un paso del runbook: `DSSignFile` en el PC con la clave definitiva, guardada en una carpeta local fuera de OneDrive y del repo, con tu copia fuera de línea. `make_release.py` nunca recibe la clave privada: recibe el `.bisign` y lo comprueba con la `.bikey` del repo, que es pública.
3. `mod.cpp` fijo en `tools/release/`, sin versión: nombre DayZ-MCP, una línea que dice que es una herramienta de desarrollo inerte sin `dayz_mcp.json`, y el enlace al repo. La descripción de la ficha te la dejo en la PR.
4. «Entra» con `verifySignatures = 2` se mide como en S1. Cuánto aguanta conectado con BattlEye queda como medida y no como puerta, porque en S1 BattlEye lo echó a los 23 s por otra causa.
5. La versión del juego no se fija por defecto, como hoy, y `doctor --json` avisa si no es la probada.
6. El modo instalado es solo de Windows. El paquete se sabe instalado cuando vive en el `site-packages` de un venv, y entonces el intérprete aprobado es el `python.exe` de ese venv. Un `python.exe` ajeno sigue dando 78 en los dos modos.
7. El perfil limpio es el del PC aislado como en S4, con `CODEX_HOME` dentro del perfil, porque el CLI de Codex no sigue `USERPROFILE`.

**Sondas sin juego del 25/11 (R26, DS14 en tu PC).**

- `DSCheckSignatures` sale con 0 también cuando la firma está mal. Con la firma buena dice «is OK»; con un byte del PBO cambiado, «is wrong»; con otra clave en `keys`, «Key not found for signature». `make_release.py` lee esa línea y solo acepta «is OK» para el `.bisign` esperado.
- El `.bisign` empieza por la `.bikey` entera: sus primeros 165 bytes son iguales, y con otra clave difieren desde el nombre de la autoridad. `bisign_wrong_key` es esa comparación.
- El CLI de Codex respeta `CODEX_HOME` y no sigue `USERPROFILE`, como en S4. El perfil limpio lleva su propio `CODEX_HOME`, así que la prueba no toca tus registros reales.

## Riesgos y preguntas abiertas

El riesgo mayor es tocar la acreditación del daemon para el modo instalado. El resto se acota con las sondas.

| Riesgo | Qué pasaría | Cómo se acota |
| --- | --- | --- |
| El modo instalado toca módulos de seguridad | Un fallo en la acreditación del daemon | Sonda S4 antes de escribir código. El modo de árbol fuente no cambia y lo fija un fixture. Los módulos sellados no se tocan. Revisión de otra familia centrada en el intérprete aprobado de `daemon.py` |
| El daemon instalado estorba a otras instancias | Mientras vive, el daemon de LIVE o de otra instancia no arranca: espera 30 s y sale con 75 | Visto por S4 con el lanzador que deja uv venv. Con el entorno creado por el módulo venv de Python no pasa: la segunda pasada de S4 dio not_writer para LIVE y experimental el 12/11. La fase 2 lo fija con un criterio |
| SteamCMD con tu cuenta en tu PC | Echa al cliente de Steam abierto («Session Replaced»). El cliente no vuelve solo y las partidas del PC salen con kick 49 hasta que vuelves a entrar | Visto el 12/11. Publicar con el Publisher, que va por el cliente abierto; SteamCMD con tu cuenta, solo con Steam cerrado y sin partidas |
| Cuenta de Steam comprometida | Un PBO alterado llega a cada suscriptor | El PBO no hace nada sin `dayz_mcp.json` y solo habla con loopback. `setup` y `doctor` comparan su sha256 con el `VERSION.json` que viaja en el wheel de GitHub, un segundo ancla |
| Clave privada perdida o filtrada | Cada servidor cambia la `.bikey`, o alguien firma PBO falsos con nuestro nombre | Copia de seguridad fuera de línea, nunca en el repo ni en la nube. Rotación descrita en el runbook |
| Steam cambia el PBO sin avisar | El puente deja de estar listo tras una actualización del item | `doctor` lo nombra y da la orden. S5 mide el comportamiento real |
| Choque con `!Workshop\@DayZ_MCP` del PC | El junction del item pisa la carpeta que usa LIVE, o no se crea | S2 con otro título, y la decisión 5 |
| Puente de cliente en servidores públicos | Automatización de un jugador en un servidor ajeno | Solo carga si ese administrador instaló nuestra clave. Aviso en la ficha |
| `uv` como requisito nuevo | Una versión nueva de `uv` cambia de comportamiento y la receta deja de valer | El venv es nuestro, en `%LOCALAPPDATA%\DayZ_MCP`, y la receta usa de `uv` solo la descarga de Python y `uv pip install`. S4 la probó en Windows con `uv` 0.12.15 |

Preguntas que siguen abiertas:

- Resuelta por S1: la firma se probó con DayZServer retail, así que no hace falta saber si el exe diag aplica `verifySignatures=2`.
- ¿Cómo nombra el launcher el junction: por el título del item o por otra cosa?
- Resuelta en el grill del 25/11, por defecto: la versión del juego no se fija, como hoy (`--expected-game-version` sigue opcional), y `doctor --json` avisa si no es la probada.
- ¿Hay alguna norma de Bohemia o de Valve contra un mod de automatización como este? No he encontrado ninguna y no lo he confirmado con ellos.

## Decisiones

1. **Vía para la mitad Python.** Decidida el 12/11: A, wheel en la release y `uv`, con B de reserva.
2. **Publicar en el Workshop con tu cuenta.** Decidido el 25/11: el item definitivo se hace público al cerrar la fase 1. El de prueba se queda como está.
3. **Arrancar la fase 0.** Decidida el 12/11 y cerrada el 19/11. `C:\dzmcp-dist` y el item de prueba se quedan como están, como elegiste el 19/11.
4. **Un solo cliente MCP.** Decidido el 25/11: como modo explícito, en la fase 3, después de la 2 y con su grill de producto antes de implementar. Hoy quien solo tiene Claude Code no puede arrancar el daemon.
5. **Nombres.** Decidido el 25/11: `DayZ-MCP` para el item, distinto de la carpeta de desarrollo `@DayZ_MCP`, y `DayZ_MCP` para la clave.
6. **PyPI.** Decidido el 25/11: para después. Reservar el nombre `dayz-mcp`, que el 11/11 estaba libre, lo haces tú con tu cuenta cuando quieras.
7. **Contrato.** Decidido el 25/11: con grill de producto. El gate del grill de plan lo adelanta a antes de las dos fases: E7 y E8, confirmados el 25/11.

## Fuera de alcance

- Repartir el lanzador nativo ya compilado o rediseñar el sellado. `dayz_test_run` sigue en el nivel 2 y merece plan propio.
- Servidores remotos o alojados por terceros: el daemon solo escucha en `127.0.0.1`.
- Exe empaquetado, MSI, `.mcpb` y registro oficial de MCP.
- Suscribir al Workshop sin la persona, o arrancar el juego desde `setup`.
- Cambios en las tools del MCP o en el Knowledge Pack, que se instala como hoy.
- El modo instalado en Linux y macOS. Desde #411 `main` arranca también en un host Linux desde el clon, y eso no cambia.

## Fuentes

- [Publicar en el Workshop, wiki de la comunidad](https://stardz-team.github.io/DayZ-Modding-Wiki/en/08-tutorials/07-publishing-workshop)
- [Gestión de mods en el servidor, wiki de la comunidad](https://stardz-team.github.io/DayZ-Modding-Wiki/en/09-server-admin/10-mod-management)
- [DayZ: Modding Basics, Bohemia](https://community.bistudio.com/wiki/DayZ:Modding_Basics)
- [Steam browser protocol, Valve](https://developer.valvesoftware.com/wiki/Steam_browser_protocol)
- [Wallpaper Engine retira los ejecutables del Workshop, PC Guide](https://www.pcguide.com/news/wallpaper-engine-puts-an-end-to-exe-wallpapers-after-malware-spreads-on-the-steam-workshop/)
- [mcpb #158, servidor de tipo `uv`](https://github.com/modelcontextprotocol/mcpb/pull/158)
- Repo `willy92wins/dayz-mcp` en `main`: `QUICKSTART.md`, `README.md`, `docs/RELEASE.md`, `docs/SERVERMOD.md`, `product-spec.md` y `plans/2026-08-16-workshop-mods-accreditation.md`.

* [Instalación de uv](https://docs.astral.sh/uv/getting-started/installation/) y [descarga automática de Python](https://docs.astral.sh/uv/guides/install-python/), Astral

La wiki de la comunidad no es fuente oficial. Lo que solo sale de ella está en las sondas de la fase 0.

## Anexo: modo instalado, qué cambia en el código

Leído el paquete sitio a sitio el 12/11 sobre `main`, y medido ese mismo día en tu PC con la sonda S4 (`c77ebef`). Las líneas están puestas al día el 25/11 sobre `main` `f767dd8`: los sitios son los mismos y hay uno nuevo, solo de Linux. De los 14 sitios que miran al árbol fuente, dos impiden arrancar desde un wheel: los playbooks, que el wheel no lleva y el servidor carga al importar, y el intérprete aprobado del daemon. Otro pide un cambio pequeño. Fuera del paquete hacen falta tres cambios más, y uno en la receta: el entorno no puede crearse con `uv venv`. Ninguno toca los módulos sellados, así que la fase 2 no obliga a recompilar el lanzador.

### Los 14 sitios del paquete

El paquete es `tools/dayz_mcp`. Instalado desde un wheel vive en `<venv>\Lib\site-packages\dayz_mcp`, así que la carpeta que contiene el paquete pasa de `tools` a `site-packages`.

| Sitio | Para qué sirve | Nivel | Desde un wheel | Qué se hace |
| --- | --- | --- | --- | --- |
| `daemon.py:313-322` | Intérprete aprobado: el daemon solo arranca bajo `tools\.venv-mcp\Scripts\python.exe` | 1 | **Bloquea.** Esa ruta no existe: `daemon_python_not_approved`, salida 78 | Cambio: el aprobado es el `python.exe` del venv que contiene el paquete. Solo la rama de Windows: la de POSIX (`daemon.py:323-330`, desde #411) no cambia |
| `daemon_contract.py:12` | Carpeta de trabajo canónica del daemon: la que contiene el paquete | 1 | Vale: pasa a ser `site-packages` | Nada. Es un módulo sellado |
| `instance_context.py:96-99` | Reconocer a otro daemon por su carpeta de trabajo o, si no, por la ruta de su `python.exe` | 1 | La carpeta de trabajo acierta para el intérprete. El lanzador que deja `uv venv` trabaja desde otra carpeta y queda sin reconocer: el daemon de otra instancia lo toma por un escritor desconocido | Cambio en la receta, no en el código: crear el venv con el módulo `venv` de Python, cuyo lanzador hereda la carpeta de trabajo. Comprobado por la segunda pasada de S4: con ese venv, el daemon da not_writer para LIVE y experimental |
| `playbook_tool.py:28` | Carpeta `playbooks/` de la raíz del repo | 1 | **Bloquea.** No existe, y `server.py:404` carga el runner al importar: cualquier `python -m dayz_mcp` sale con `playbook_runner_missing`, salida 1 | Cambio: los playbooks viajan en el wheel y se buscan dentro del paquete |
| `server_freshness.py:27` | Qué fuentes vigila el aviso de código viejo | 1 | Vigila además los módulos sueltos de `site-packages`. Inocuo | Sigue a la ruta nueva de los playbooks |
| `doctor.py:306` | Carpeta donde busca lanzadores `dayz-test.ps1` antiguos, y chequeo del lanzador sellado | 1 | Recorrería el venv entero y daría siempre `NATIVE_BUNDLE_MANIFEST_UNREADABLE` | Cambio: en modo instalado, ni carpeta que recorrer ni ese chequeo |
| `daemon.py:447` | Carpeta `_audit` de exec-enforce cuando no se pasa `--exec-audit-path` | 1, opcional | Escribiría dentro de `site-packages` | Nada: `setup` no registra exec-enforce. Quien lo active pasa la ruta |
| `steam_prepare_supervisor.py:66` | Carpeta de trabajo del ayudante de Steam | 2 | Vale: `site-packages` | Nada |
| `launcher_registry.py:31` | `approved-launchers.json`, el registro del lanzador sellado | 2 | Ausente, como en un clon sin compilar | Nada |
| `registry_lock.py:82` | `approved-launchers.lock`, el candado de ese registro | 2 | Se crea vacío en `site-packages` la primera vez que algo consulta el registro | Nada. Junto a `__pycache__`, lo único que debe aparecer en el venv |
| `launcher_registry_update.py:27` | Registro, bundle, recibos y línea base para renovar el sello | 2 | Ausentes | Nada |
| `dayz_test_tool.py:1265-1270` | `request-policy.json` del bundle, para listar proyectos | 2 | Ausente: `launcher_policy_missing` | Nada |
| `process_lifecycle.py:1308-1313` | `closure-manifest.json` del bundle, para un texto de ayuda | 2 | Ausente: el texto dice «unreadable» | Nada |
| `stdio_bridge.py:22` | Ayuda de diagnóstico: `python.exe` y clave por defecto del árbol; importa `install_mcp` | Diagnóstico | Falla al importar `install_mcp` | Importa cuando el instalador entre en el wheel. Sus valores por defecto siguen siendo los del árbol |

El nivel 1 es el puente con un juego ya arrancado. El nivel 2 es `dayz_test_run` con su lanzador sellado, que sigue saliendo del clon.

Aparte, `native_bundle.py:1139` y `process_lifecycle.py:1301` leen ficheros del propio paquete: valen en los dos modos. Desde el 18/11 (#411) hay un sitio más, solo de Linux: `linux_launcher.py:60-62` lee los dos locks de `tools/`. El modo instalado es solo de Windows y no lo usa.

### Fuera del paquete

El wheel lleva el paquete `dayz_mcp` y `mcp_capture.py`, nada más: 108 módulos el 11/11, y hoy `tools/dayz_mcp` tiene 124 ficheros `.py`. El instalador y su estado quedan fuera.

| Pieza | Hoy | Desde un wheel | Qué se hace |
| --- | --- | --- | --- |
| `mcp-grab.ps1` | El script de captura va junto a `mcp_capture.py`, en `tools/` (`mcp_capture.py:102`) | El wheel lleva el módulo y no el script: `capture_screenshot` da `capture_backend_failed` | Cambio: el script viaja en el wheel |
| `install_mcp.py` | Crea `tools\.venv-mcp` e instala en él. Genera la clave, escribe `dayz_mcp.json`, fija los CLI de Claude y Codex y registra con transacción y vuelta atrás | No va en el wheel, y su primer paso crea un venv dentro del árbol | Cambio: entra en el wheel. `setup` reutiliza la clave, la configuración, los CLI fijados y el registro, y se salta el venv |
| `p0s_gate.py` | Copia de seguridad de `runs` que el instalador exige antes de registrar | No va en el wheel | Cambio: entra en el wheel |
| Registro del cliente MCP | `host_config.py` exige que `command` sea el `python.exe` del venv, el mismo que ejecuta al cliente, y que `args` empiece por `-m dayz_mcp` | Igual | Nada: `setup` registra el `python.exe` del venv, no el lanzador `dayz-mcp.exe` |
| Dos clientes | El daemon solo arranca con el mismo registro en Claude y en Codex | Igual | Nada en la fase 2. Es la fase 3 |
| Clave | Por defecto `tools\.dayz_mcp.key`. El registro la pasa siempre con `--keyfile` | La ruta va en el registro | `setup` la crea en `%LOCALAPPDATA%\DayZ_MCP` |
| Resto del estado | Ya vive en `%LOCALAPPDATA%\DayZ_MCP`: `runs.json`, coordinación, buzón, diario del registro, CLI fijados y Knowledge Pack | Igual | Nada |
| Knowledge Pack | `git clone` del Pack. Pide git | Igual | Nada: ya tiene `git_missing` con su remedio y `--skip-knowledge-pack` |

### Lo que no se toca

`tools/packaged-modules.lock.json` fija el hash de 19 módulos del paquete, los que viajan dentro del lanzador sellado. Cambiar uno obliga a rehacer ese lock, recompilar el lanzador con MSVC y renovar el registro en cada caja, y esa PR la revisas y mergeas tú.

- De los 14 sitios, solo `daemon_contract.py` está en esa lista, y vale tal cual. Tampoco hace falta tocar `host_config.py` ni `server_cli.py`, que también están.
- Los cambios de la fase 2 caen en `daemon.py`, `playbook_tool.py`, `doctor.py`, `mcp_capture.py`, `install_mcp.py`, `pyproject.toml` y `make_release.py`. Ninguno está en el lock.
- El punto de entrada `dayz-mcp` es un módulo nuevo que reparte entre `setup` y `doctor`. El servidor se sigue registrando como `python.exe -m dayz_mcp`.
- El nivel 2 no se mueve: registro, candado, recibos y bundle siguen junto al paquete, en el clon.
- La fase 3 sí toca `host_config.py`: aceptar un solo cliente cambia la regla que exige los dos registros.

De aquí sale un criterio nuevo para la fase 2: `packaged-modules.lock.json` queda byte a byte igual.

### Instalación con hashes

`uv tool install` no puede exigir hashes, y el instalador de hoy sí exige el de psutil. Por eso la receta pasa a un venv propio e instala con `uv pip install --require-hashes`, que cubre el wheel y todas sus dependencias.

Medido el 12/11 con uv 0.11.32, en Linux y con un paquete de prueba:

| Prueba | Resultado |
| --- | --- |
| `uv tool install` con `UV_REQUIRE_HASHES=1` y ningún hash | Instala, salida 0 |
| `uv tool install --with-requirements` con un hash equivocado | Instala, salida 0 |
| `uv tool install --constraints` con un hash equivocado | Instala, salida 0 |
| `uv tool install "pkg @ file://...whl#sha256=<hash malo>"`, medido el 11/11 | Instala, salida 0 |
| `uv venv` y `uv pip install --require-hashes -r`, con el hash correcto | Instala, salida 0 |
| Lo mismo con un hash equivocado | Salida 1, nada instalado |

El lock que iría en la release:

- Windows necesita 32 paquetes. El proyecto fija la versión de 4 (`mcp`, `Pillow`, `psutil` y `pywin32`); los otros 28 se resuelven hoy en el momento de instalar.
- `uv pip compile --generate-hashes --python-platform x86_64-pc-windows-msvc` saca el lock desde Linux en 1,7 s: 32 paquetes, 610 hashes, 54 KB.
- Las 32 versiones son las mismas para Python 3.11, 3.12 y 3.14. Para 3.11 y 3.14, las dos que prueba el CI, todo se instala desde wheels ya compilados.
- El hash del psutil vendorizado (`EB7E8143…D988`) está en el lock: es el mismo fichero que publica PyPI.
- El lock lleva además la línea del wheel de dayz-mcp con su sha256.

Con esto desaparece el paso de comprobar el wheel a mano, y un byte cambiado en cualquiera de los 32 paquetes para la instalación.

El Python lo pone `uv`. Medido el 12/11 en Linux: `uv venv --managed-python --python 3.12` baja CPython 3.12.13 (32,6 MiB) y crea el entorno en 2,5 s, aunque la máquina ya tenga otro Python 3.12. En tu PC, `uv python install 3.14` bajó CPython 3.14.7 (21,7 MiB) en 1,7 s, con LIVE en su Python 3.14.3 de siempre. Así todas las instalaciones corren sobre el mismo intérprete.

### Lo que midió la sonda S4 en tu PC

S4 corrió el 12/11 en tu PC, sobre `main` (`c77ebef`), con `uv` 0.12.15 y el CPython 3.14.7 que baja `uv`. Resultado: el wheel instalado en un venv propio arranca cliente y daemon, pero con tres correcciones y no con una. Todo pasó dentro de `C:\dzmcp-dist`, con una instancia propia (`dists4`, puerto 8853) y el perfil del proceso redirigido a esa carpeta. No arrancó el juego ni tocó LIVE, y tus registros de Claude y de Codex quedaron como estaban.

| Comprobación | Esperado | Visto en tu PC |
| --- | --- | --- |
| 1. `uv venv --managed-python` y `uv pip install --require-hashes -r <lock>` con el wheel de `main` | Salida 0, sin compilar nada | **Pasa.** Salida 0 y todo desde wheels. Con un dígito de un hash cambiado, salida 1 y nada instalado |
| 2. El `Scripts\python.exe` del venv es un fichero x64, sin enlaces ni junctions en su ruta, e igual a `sys.executable` | Sí a las tres | **Pasa, con un matiz.** Es el lanzador propio de `uv` (47 104 B), no el de Python (241 152 B) |
| 3. El lanzador del venv es el padre directo del intérprete, con el mismo argv | Sí, como en el venv de hoy | **Pasa.** La identidad del daemon se captura |
| 4. Daemon con el wheel sin tocar | Se para con `daemon_python_not_approved`, salida 78 | **Falla el mapa.** Sale antes, con `playbook_runner_missing` y salida 1. Con los playbooks puestos a mano sí da la salida 78 |
| 5. Daemon con los playbooks puestos y el intérprete aprobado cambiado solo en el venv de la sonda | Arranca, y `bridge_status` responde por el cliente, sin juego y sin estar listo | **Pasa, con una corrección.** Escucha a los 9 s y sale solo por inactividad. `bridge_status` no levanta el daemon: lo levanta `session_status`, en 15 s, y entonces `bridge_status` contesta |
| 6. Ficheros nuevos bajo el venv tras arrancar y pasar `doctor` | Solo `__pycache__` y `approved-launchers.lock` | **Pasa.** Solo aparece `approved-launchers.lock`. `doctor` da `NATIVE_BUNDLE_MANIFEST_UNREADABLE`, como decía el mapa |
| 7. Actualizar con el daemon vivo: otro venv para la versión nueva, y registrar | Queda escrito qué hace el cliente nuevo ante el daemon viejo | El cliente nuevo responde `daemon_reaccreditation_failed_open_new_session` y no levanta otro daemon. Cuando el viejo sale por inactividad, levanta el suyo en 19 s |

Lo que S4 encontró y el mapa no tenía:

- **Los playbooks bloquean antes que el intérprete.** Sin ellos no arranca ni el cliente. Pasan a ser el primer cambio de la fase 2.
- **El lanzador de `uv venv` estorba a las demás instancias.** Al arrancar, cada daemon mira qué otros procesos `dayz_mcp` hay y de qué instancia son, y lo deduce de su carpeta de trabajo. El lanzador de `uv` trabaja desde otra carpeta y queda sin identificar. Pasados por el clasificador de `main`, los procesos de la sonda dan ese resultado para LIVE, experimental y xai: un daemon suyo que arrancara mientras tanto esperaría 30 s y saldría con 75. En la sonda no llegó a pasar, porque experimental y xai arrancaron fuera de los dos ratos en que hubo daemon de la sonda. La corrección que propongo no toca código: crear el venv con el módulo `venv` de Python sobre el Python de `uv`, que usa el mismo lanzador que el venv de hoy. La segunda pasada de S4 lo comprobó: está justo debajo.
- **`uv` enlaza el venv con su caché.** 1759 de los 1921 ficheros instalados son enlaces duros a la caché de `uv`: cambiar uno en el venv lo cambia también en la caché. La receta llevará `--link-mode=copy`.
- **`doctor` exige `--daemon-policy`.** Sin ese argumento sale con 2. `dayz-mcp doctor` lo pasará él.
- **El daemon viejo puede durar 30 minutos.** Es su tiempo de inactividad por defecto. Mientras viva, el cliente de la versión nueva da el error de la fila 7, y `setup` tiene que decirlo con su remedio.

Sin medir: el arranque del daemon por WMI, que es el de una instalación real y que la sonda apaga para no salirse de su carpeta; `uv` instalado con `winget`; y qué hacen Claude Code y Codex con el daemon al cerrarse. La sonda ocupa 389 MiB en `C:\dzmcp-dist` y no he borrado nada. El informe completo está en `C:\dzmcp-dist\s4\report\S4.json`.

#### Segunda pasada: el venv del módulo venv de Python

Corrió el 12/11 en tu PC, en `C:\dzmcp-dist\s4b`, con otra instancia propia (`dists4b`, puerto 8854) y el arranque por WMI apagado, como la primera. Mide la corrección de la receta. Esta vez clasificó los procesos en frío antes de arrancar ningún daemon, para no repetir el estorbo de la primera pasada.

- **El venv.** `python -m venv --without-pip` sobre el CPython 3.14.7 de `uv` tarda 0,7 s, y `uv pip install --link-mode=copy --require-hashes` instala los 33 paquetes en 1,7 s, sin compilar nada. `Scripts\python.exe` es byte a byte el lanzador de Python (241 152 B), `pyvenv.cfg` apunta a la carpeta real de 3.14.7 y ningún fichero queda enlazado a la caché.
- **Ojo con la ruta.** `uv python find --managed-python 3.14` da un junction (`cpython-3.14-windows-x86_64-none`), no la carpeta de la versión. La receta resuelve la ruta real antes de crear el venv.
- **La carpeta de trabajo.** El lanzador y el intérprete trabajan los dos desde `site-packages`. El lanzador de `uv venv` se iba a `%TEMP%`.
- **El clasificador, en frío.** Con el `_migration_disposition` de `c77ebef`, el daemon de este venv da `not_writer` para LIVE y para experimental, y `blocker` solo para su propia instancia, que es lo que debe dar. El de `uv venv` daba `blocker` para las tres.
- **Arranque.** Sin parche, salida 78 (`daemon_python_not_approved`). Con el intérprete aprobado cambiado en el venv, escucha a los 8 s, `/status` responde 200 y sale solo por inactividad. Mientras vive, el escaneo de las otras instancias no lo cuenta entre los que bloquean.
- **Con el cliente.** `session_status` levanta el daemon en 18 s y `bridge_status` contesta, sin juego y sin estar listo. Al cerrar el cliente, el daemon sale solo a los 23 s.
- **Al acabar.** No queda vivo ningún proceso de la sonda, tus registros de Claude y Codex están igual y no hay nada fuera de `C:\dzmcp-dist`, que ocupa 580 MiB con SteamCMD.

Las órdenes que usó la sonda, en orden:

```
uv python install 3.14
uv python find --managed-python 3.14
<python que da find> -c "import os,sys; print(os.path.realpath(sys.executable))"
<python real> -m venv --without-pip <carpeta>
uv pip install --python <carpeta>\Scripts\python.exe --link-mode=copy --require-hashes -r <lock>
```

Para arrancar desde un wheel siguen haciendo falta los dos cambios de código de arriba: los playbooks dentro del wheel y el intérprete aprobado tomado del propio venv.

Sobre WMI, deducido del código y del registro de arranque de otra instancia, sin medir aquí: con el arranque por defecto, el daemon lo crea WmiPrvSE, fuera de cualquier Job del cliente, así que cerrar el cliente no lo mata, y nace con el entorno del usuario y no con el del cliente. Si WMI falla se vuelve a Popen, y entonces el Job del cliente sí le alcanza.

El informe está en `C:\dzmcp-dist\s4b\report\S4b.json`: 19 filas, 18 pasan y una es informativa.
