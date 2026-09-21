# Review escéptico — DayZ-MCP

## 1. Los tres motivos de descarte en los primeros 60 segundos

**(a) Pin de Python sin explicación.** *"Python **3.14** — the installer asks `py` for 3.14 specifically"* y en el Quickstart: *"3.10–3.13 cannot install through it even though `pyproject.toml` declares `>=3.10`"*. Que el instalador exija la versión más reciente del lenguaje mientras el manifiesto declara otra cosa, y que el autor lo documente como rareza conocida en vez de arreglarlo, huele a herramienta construida alrededor de UNA máquina (la suya) y probada solo ahí. Es el clásico "works on my machine" escrito con orgullo.

**(b) Toda la evidencia se cita a sí misma.** *"Measured, not claimed. Numbers from in-game runs. Each citation is the line that records the figure"*. Suena bien hasta que miras qué cita: `product-spec.md:42`, `tools/poc-verdict.json:117`… archivos del mismo repo, controlados por el autor. El veredicto lo notariza el acusado. Ni un vídeo, ni una captura, ni un artefacto externo de todo el proyecto "medido". No verificable desde el texto, y eso es justo lo que un escéptico no perdona en un proyecto cuya propuesta de valor es precisamente generar evidencia.

**(c) La documentación filtra el árbol de desarrollo del autor.** El Quickstart abre con *"This recipe targets a full clone of the public repository"* y dos líneas después mete: *"The development tree sparse-excludes `addon/` … so step 3 has nothing to pack there"*. Si mi receta va sobre un clone completo, esa frase no me aplica; es memoria de sesión del autor colada en instrucciones para el lector. Sumado a que el propio README pre-excusa sus tests rotos (*"a single flaky run reports up to five `TimeoutExpired` errors from that module alone. Errors from anywhere else are worth reporting"*), el conjunto transmite: docs generadas desde el estado mental del autor, no escritas desde la posición del que llega.

## 2. Donde suena a IA

- *"Two things fall out of that, and both are new for this game"* — montaje retórico de lista-canónica. Humano: "¿Para qué sirve? Dos casos: iterar mods sin tocar el teclado, y operar un server".
- *"It is one; the two loops above are what the blades add up to."* — anticipa la crítica y le da la vuelta con gracia. Sobra entera; un humano la borra.
- *"Data an agent can assert on, not pixels to squint at."* — paralelismo de folleto. Humano: "devuelve JSON asertable".
- *"Fail-closed from the first line"* — marketing de seguridad.
- *"Everything else in this repo is optional until this works."* — frase-consuelo de LLM.
- Densidad anómala de rayas (—) y tríadas en todo el texto.
- Lo más sospechoso: el género "honestidad perfecta". Cada limitación documentada resuelve limpia en una decisión arquitectónica: MakeScreenshot roto → window-grab externo; coche no movible desde server → ruta client-owner; exec bloqueado → breakglass opt-in. Los proyectos reales tienen líos sin cerrar; aquí todos los fracasos cierran el círculo a favor del diseño. Y el encabezado *"Measured, not claimed"* es, irónicamente, la señal de virtud epistémica más típica que existe. Un humano titula "Números" y punto.
- Matiz que me hace desconfiar de mi propio escepticismo: el conteo de *"54 tools"* cuadra exactamente con la lista (los conté: 54), y las APIs citadas (`CreateObjectEx`, `RaycastRVProxy`, `SetTimeMultiplier`, `RestContext.SetHeader` solo Content-Type) son coherentes con el scripting real de DayZ. Esto no es slop analfabeto.

## 3. Promete y no demuestra

- *"an agent can now change, build, run, measure and fix a mod on its own"* — no hay una sola transcripción end-to-end de ese bucle. Es la promesa central y es no verificable desde el texto.
- *"Point an agent at a server and it can *operate* it"* contra *"there is no remote mode and no multi-user mode, by design"* — el caso "director de eventos/admin" queda reducido al localhost del autor.
- *"builds twice to prove the output reproducible"* — sin par de hashes publicado; y *"sealed against the machine that built it"* significa que todos tenemos que recompilarlo igualmente, así que ¿qué compró el aparato de supply-chain?
- Daemon con leases y *"audits what each holder did"* — ni una línea de muestra del audit trail, ni la lista blanca real de comandos.
- *"edited and re-measured in seconds instead of one repack-and-boot per change"* — cero timings.
- Coste real del polling HTTP en los FPS del server con jugadores: no medido (A2 mide que no bloquea el tick, no lo que cuesta).

## 4. Qué me falta para probarlo

- ¿Arranco retail o Diag? El Quickstart dice *"Retail works for everything here"* y el README dice *"the server talks to `DayZDiag_x64`".* Resolvedme la contradicción antes del paso 4.
- Firma del PBO: nada de `.bikey`/keys para servers con `verifySignatures=1`.
- ¿Qué esquema tiene un `vehicle_trace` de 20 Hz y cómo lo consumo como fixture de regresión?
- ¿Qué espera `dayz_test_run(project, mode)` exactamente — estructura del "proyecto"? Modos no enumerados.
- ¿Por qué exactamente 3.14 y qué rompe en 3.10–3.13?
- ¿El cliente de captura necesita sesión interactiva de Steam? ¿Y si mi server ya corre con otros 5 mods?

## 5. Lo que sí está bien

- Admitir que descartó su propia medición por tautológica: *"An earlier harness compared the bridge to its own spawn write and printed 0.000 m; that comparison was discarded."* Eso es disciplina real, no decorativa.
- Sección *"Not tested — not claimed"*: listar APIs no conducidas (navmesh, `OverrideMovementSpeed`) en vez de presumir de ellas.
- Publicar el resultado negativo B3 con la decisión derivada (`actionstartengine.c:51-58`).
- Citas `fichero:línea` sistemáticas: verificables si clono, aunque hoy no pueda hacerlo.
- Quickstart corto, con troubleshooting nominalizado (`no_run`, `server_poll_stale`, `version_mismatch`) y licencia MIT limpia.

**Veredicto:** no cierro la pestaña por IA-slop — técnicamente respira aire de DayZ. La cierro porque la evidencia es un espejo interno, el entorno está pinado a la máquina del autor, y la promesa central (el bucle autónomo) es la única cosa sin demostrar.