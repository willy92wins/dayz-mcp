# HANDOFF — DayZ-MCP

<!-- LIVE-STATE:START -->
> # XII 2026-08-28 CIERRE - `9a58256` PUBLICADO - SUSTITUYE AL XI EN LO QUE TOCA
>
> **`main` = `9a58256`, empujado. Dos repos al dia.** El trabajo del clon
> `repos/dayz-mcp` esta aterrizado salvo `hands`.
> **Handoff**: `AI\30_Sessions\2026-08-28-DayZMCP-dos-repos-launcher-y-pareja-grok-codex.md`
>
> ## Lo publicado
>
> - **`0aeb22b`** el poll del cliente y los pines v8->v9. `PollContextUrl` construia
>   la base sin barra final: el motor concatena literal, asi que **todos** los polls
>   salian `<host>:<port>poll?...` -> `EREST_ERROR_SERVERERROR`. Ademas watchdog de
>   poll, `DetachBridge`, `observed_this_generation`, y `action_use` con el `ItemBase`
>   real en manos. **La suite publica pasa de 10 rojos a 5.**
> - **`9a58256`** D2 de `product-spec.md` dice ahora lo que el codigo hace.
> - **Knowledge Pack**: `4da9bc3..eec69f9`, 5 commits de skills.
>
> ## ⚠ `hands` NO esta publicado, y es a proposito
>
> Vive en `fichas/hands-watchdog` @ **`78fa310`** (clon `C:\Users\guill\repos\dayz-mcp`).
> Toca `MCPBridge.c` (1 linea) y `MCPMessages.c` (`bool hands;`), y el centinela de
> `test_task9_spawn_phase_markers` **solo se recongela contra un estado gateado
> in-game**. Ese gate no ha corrido. La fuente viva de build
> (`DayZ Projects\DayZ_MCP\`) SI la lleva y es **byte a byte identica** al addon de
> esa rama, los 19 ficheros.
>
> ## ⚠ EL EDITABLE-INSTALL DE LA MAQUINA ESTA PODRIDO
>
> `%APPDATA%\Python\Python314\site-packages\__editable___dayz_mcp_tools_1_0_0_finder.py`
> tiene `MAPPING = {'dayz_mcp': '...\Temp\nocturno-verify2\repo\tools\dayz_mcp'}`,
> un workspace **borrado**. Rompe 5 tests de `bug046` en CUALQUIER clon de esta caja.
> Sintoma: `daemon_python_not_approved` con `FileNotFoundError [WinError 3]`.
> Rojo-verde medido: venv sano -> **21 passed, 0 fallos**; python del sistema -> **5 failed**.
> `packctl` igual, apuntando a `pack-rewrite3`.
> **El reapuntado NO esta hecho**: Codex tumbo el runbook por 3 defectos (preflight que
> busca el literal `C:\Python314\python.exe` y se salta `python.exe -m dayz_mcp`;
> desinstala antes de verificar el destino; el rollback mete `pip-show-before.txt`
> dentro de `site-packages`).
>
> ## Launcher nativo: reconstruido y refirmado
>
> `--verify-reproducible` con los 3 modos identicos. PE `57495CB2`->**`9AB63F75`**,
> `app.pyz` `19564729`->**`D1050F97`**. Registro actualizado, `NATIVE LAUNCHER
> REGISTRY OK`, 71 transiciones. El drift era `native_process_snapshot.py`, cambiado
> el 24-ago en `d73da6c` con el bundle construido el 23.
>
> ## D2 prometia un gate que no existe (corregido en el SPEC, no en el codigo)
>
> `choose_stable_frame` es un **argmin** sobre deltas adyacentes: elige el menos
> inestable y **siempre devuelve**. `DEFAULT_STABILITY_THRESHOLD = 0.03` esta definido
> en `mcp_capture.py` y **no se lee en ningun sitio**. Medido: 4 frames con deltas
> `1.0/0.667/0.667` devuelven `Image`. La fila decia ademas «PNG» (es `jpeg`) y
> «readiness-gated» (el gate es de estado de lifecycle).
> **`DEFAULT_STABILITY_THRESHOLD` sigue viva como constante muerta**: decidir si se borra.
>
> ## Abierto
>
> 1. Reapuntar el editable-install (runbook a reescribir con los 3 arreglos).
> 2. **2 de 16 tests portados son tautologicos**: con `choose_stable_frame` reducido a
>    `frames[len//2]` los 16 pasan igual. Faltan casos de 4/5 frames donde el par minimo
>    no contenga el centro.
> 3. La tabla de 11 «huecos» **mezcla tres categorias** (hueco de producto / politica del
>    driver Task9 excluido / defensa en profundidad). Recategorizar antes de decidir.
> 4. Gate in-game de `hands` + `action_use` -> recongelar centinela -> publicar `78fa310`.
> 5. Volcar las 4 corridas al `council-scorecard.md`.
>
> ## Metodo - lo que costo esta sesion
>
> - **El clon `repos/dayz-mcp` estaba 26 commits por detras** de `main` y ahi vivia todo
>   el trabajo. Mirar SOLO el arbol de OneDrive daba «nada pendiente».
> - **`main` publicado estaba rojo**: 10 fallos, 5 de ellos pines a v8 olvidados por
>   `d73da6c`.
> - **CRLF otra vez**: 4034 de 4034 lineas «cambiadas» con contenido identico. Normalizar
>   ANTES de concluir perdida, sobre todo antes de un borrado.
> - **El heredoc de Bash muere ~6 KB** sin avisar, dejando el comando sin cerrar.
> - **Una frontera del orquestador puede matar un gate**: prohibi el 8765 y con eso impedi
>   un subtest. Lo declaro el delegado; lo cerre yo.

> # XI 2026-08-28 noche (gauntlet grok-codex) - P2 STALE-TARDIO CERRADO POR IDENTIDAD - complementa al X
>
> **Rama `fichas/hands-watchdog` @ `78fa310`** (2 commits nuevos sobre dce04f2):
> `457e6cc` fija el fix del leak de Grok (token m_PollStaleGeneration, P2 declarado en el
> mensaje) y `78fa310` CIERRA el P2: descarte por IDENTIDAD de objeto (AbandonInFlightPoll
> nullea m_PollCallback; huerfano retenido en refs hasta SU ReleaseCallback; eventos con
> `if (!m_Bridge.IsActivePollCallback(this)) return;`; generaciones ELIMINADAS; leak residual
> 1 obj/abandono). Gauntlet: Grok implemento (r4 $0.10 + r5 $0.10, workspace %TEMP% con
> baseline de hashes), Codex R21 adversarial (160k tokens) trajo 2P2+4P3 arbitrados — el P2
> del ciclo bridge<->callback en Shutdown (preexistente) mitigado con DetachBridge() sobre
> AMBOS arrays; polaridad de la suite fijada con mutante InvertedIdentityBridge; P3 de allocs
> RECHAZADO (coste de deserializar). Suite 8/8 en workspace y repo; intocables por hash.
> **PENDIENTE DUENO (sin cambios + 1 nuevo): port tools + MERGE de la rama (3 commits) +
> DEPLOY del PBO del addon — el runtime vivo lleva solo el slash fix: el leak de callbacks
> sigue activo in-game hasta ese deploy.** Buzon: 9 entradas (+2 hoy: bad_dayz_test_request
> opaco fb-...-91ed; width/height del run sin efecto fb-...-2899). El `reviews/` untracked
> del repo es ajeno (drainfix 19-ago), sin trackear a proposito.
> Detalle: `AI/30_Sessions/2026-08-28-DayZMCP-gauntlet-identidad-y-LFPG-vuelo-F-fuentes.md`.
>
> # X 2026-08-28 tarde (sesion sorter-V4) - HOTFIX AL PBO DEL GAUNTLET + LEAK NUEVO FICHADO - complementa al IX
>
> **El PBO del IX rompia TODOS los polls del CLIENTE**: `PollContextUrl` (base sin
> slash final) producia URL malformada `...8765poll?...` → error=7 eterno; la suite
> headless del gauntlet no ejercita la concatenacion del ENGINE y no pudo verlo.
> **Hotfix desplegado y verificado in-game** (peers verdes, poll 0.26 s): contexto
> compartido, el watchdog protege por `m_PollGeneration`. PBO 456.569 B (backup
> `.bak_pre_pollctx_20260828`), commit `dce04f2` EN la rama `fichas/hands-watchdog`,
> ficha `fb-20260828-113005-fdcc`. El "legacy_blocked stale" del IX NO era stale.
> **Held-item validado end-to-end** (wiring con CableReel via Port actions, vuelo E
> sorter). **Leak nuevo fichado** `fb-20260828-125854-964f`: 11.798x
> MCPClientPollCallback vivos al shutdown (~80 min; degradation) — para la proxima
> ronda del dueno junto al port del lado tools (que SIGUE pendiente).
>
> # IX 2026-08-28 (sesion DayZ Projects, gauntlet fichas) - PBO DEL BRIDGE REDEPLEGADO + RAMA REVISADA VERDE - complementa al VIII, no sustituye sus pendientes
>
> **PBO en `Mods\@DayZ_MCP\Addons` REEMPLAZADO** (455.888 B, 2026-08-28 03:26; backup del
> anterior en `C:\Users\guill\dayz_re_scratch\fichas_run_20260827\_backups\`): addon vivo +
> fichas `fb-20260827-202324-8ab3` (watchdog del primer poll sin callback + AbandonInFlightPoll)
> y `fb-20260827-202308-5d7a` (action_use pasa el item REAL en mano). Fuente: addon compilable
> sincronizado con la rama. Gauntlet Grok(implementa)->Codex(revisa), VERDE 0/0/0 en 6 rondas.
>
> **Rama `fichas/hands-watchdog` @ 26a4955 en `Repos\dayz-mcp`** (checkout quedo EN la rama).
> OJO: la rama nace de main local `29a8e83`, que va POR DETRAS del arbol vivo (23 .py
> divergentes; el vivo ya esta en bridge v9 y ya expone dest=hands) y por detras del propio
> tag v1.0 (`bb33b39`). **PENDIENTE (dueno del proyecto): portar el lado tools de la rama
> (veredicto por-generacion de bridge_status + 3 ficheros de tests) al ARBOL VIVO
> `DayZ_MCP_dev\tools`, y publicar por `tools\publish` — nunca copiando el arbol.** Las 2
> fichas del buzon quedan ABIERTAS hasta ese port. Probado in-game (run 60e8899a):
> dest=hands ok + hands_occupied; held-item end-to-end pendiente de escenario con tool-action.
> Detalle completo: `AI/30_Sessions/2026-08-28-DayZ_MCP-gauntlet-fichas-verde-y-vuelo-D.md`.
>
> # VIII 2026-08-25 - `bb33b39` + tag `v1.0` - RELEASE FUERA + PLAYBOOK DE DEMO LISTO - SUSTITUYE A TODOS LOS ANTERIORES
>
> **v1.0 PUBLICADA: https://github.com/willy92wins/dayz-mcp/releases/tag/v1.0** (tag en
> bb33b39; PBO 5C3CC5FE... bridge 9 byte-trazable al commit; VERSION.json + SHA256SUMS).
> Sobre el PBO de release: site gate PASS (rc=0) + Grok 146.0 m + GPT-5.6 117.4 m; el 3/3
> de la lane gratuita queda en el build hermano 91A542 (flake del cargador MCP de opencode,
> ficha eeab re-enrutada a AI_Pipeline). Buzon a CERO (294/294). Vault: matriz con
> RELEASE_v1.0_PUBLICADA + correccion x4300/r13 (auto-colision de sonda).
>
> **Playbook de rodaje LISTO** (variante A desbloqueada):
> `10_Projects/DayZ_MCP/demo-rodaje-v1.md` + artifact privado
> https://claude.ai/code/artifact/a05ad83d-d28e-4323-809c-0e4cab9d54e2 - preflight,
> 9 escenas con args probados, congelados literales, retakes medidos, hoja E3.
>
> ## Pendiente (decisiones del usuario)
>
> 1. **Grabar la demo** con el playbook (yo levanto juego y canto escenas). Aprobar o no
>    la escena 6b (agente conduciendo solo; adicion post-pack).
> 2. **Contacto covalschi E1**: borrador listo (privado, bench cruzado; ventana 'misma
>    semana del tag'). NO ENVIADO - espera OK explicito.
> 3. Metricas E3 a 7 dias (2026-08-31): stars/forks, clones, CTR video->Release, issues,
>    time-to-first-tool-call. A 14: respuesta E1.
> 4. Backlog v1.1 (51 filas) + arco adopcion/reacreditacion. Usuario: claves fuera de
>    OneDrive + DACL.
>
> Nota de box al cierre 24d-final: :8765 con daemon idle de OTRA sesion activa (0 juegos);
> no tocado (foreign). Configs codex/grok/opencode verificadas limpias.
>

> # VII 2026-08-24 NOCHE - `bb33b39` + tag `v1.0` - RELEASE PUBLICADA - SUSTITUYE A TODOS LOS ANTERIORES
>
> **v1.0 FUERA: https://github.com/willy92wins/dayz-mcp/releases/tag/v1.0** (tag v1.0 en
> bb33b39; PBO 5C3CC5FE...9C460903 bridge 9, byte-trazable al commit; assets de
> make_release con VERSION.json y SHA256SUMS). Sobre el PBO de release: site gate PASS
> (rc=0) + Grok 146.0 m + GPT-5.6 117.4 m; la lane gratuita 3/3 quedo en el build hermano
> 91A542 (flake del cargador MCP de opencode, fichado y re-enrutado a AI_Pipeline).
> Buzon a CERO. Configs de CLI restauradas. Box limpio.
>
> ## Pendiente (decisiones del usuario)
>
> 1. **Contacto covalschi**: borrador E1 aprobado en el pack (privado, misma semana del
>    tag, bench cruzado; reciprocidad 4 semanas o cero). NO ENVIADO - espera OK explicito.
> 2. **Demo**: variante A (con clip de vehiculo) DESBLOQUEADA por el pack (G PASS);
>    falta grabar el footage en una sesion de juego + montar + YouTube + eco en X.
> 3. Metricas a 7/14 dias del checklist E3 (stars, clones, issues, respuesta E1).
> 4. Backlog v1.1 (51 filas) + arco adopcion/reacreditacion. Usuario: claves fuera de
>    OneDrive + DACL.
>

> # VI 2026-08-24 NOCHE - `d73da6c` - BUZON CERO + CERTIFICACION MULTI-AGENTE - SUSTITUYE A TODOS LOS ANTERIORES
>
> **`master` = publico `main` = `d73da6c`, CERO sin empujar.** Handoff:
> `AI/30_Sessions/2026-08-24d-DayZ_MCP-ola-v9-buzon-cero-y-certificacion-multiagente.md`
>
> ## BUZON A CERO (294/294) + OLA v9 + CERT MULTI-AGENTE 3/3
>
> Ola v9 (bridge 8->9): object_anim/inspect por object_id, SetAnimationPhaseNow,
> clearance gate en player_teleport, reliability en entities_query, crash logs fuera
> del launch scan, same_path con samefile. Validada in-game 7/8 + PBO 91A542E1...
> **3 agentes externos condujeron el juego con brief minimo y PASS 3/3**: Grok 4.6
> (solo-MCP) 163 m, GPT-5.6 (codex) 101 m, Ox Alpha (opencode) 133 m - spawn ->
> fixture -> asiento -> drive -> puerta por object_id -> delete -> release.
> Vaciado: 48 al backlog v1.1 + 16 re-enrutadas + 7 ola cerradas + 3 hallazgos
> nuevos (auto-colision de sonda ARREGLADA d73da6c; relectura same-tick documentada;
> keepalive anti-muerte-de-cliente verificado). Centinela re-congelado verde.
>
> ## Proximo (por orden)
>
> 1. **Decision usuario: tren v1.0** (con la ola dentro): PBO del commit del tag +
>    site gate sobre ESE PBO + make_release + tag + Release + demo B + covalschi.
>    Claim nuevo: 'certificado por 4 agentes de 3 familias'.
> 2. Backlog v1.1 (50) y reroutes (16/9 proyectos) en el vault del proyecto.
> 3. Arco post-v1.0: adopcion/reacreditacion durable + doctor fiel.
> 4. Usuario: claves fuera de OneDrive + DACL.
>

> # V 2026-08-24 TARDE - `d4b885a` - GATE G FORMAL CERRADO - SUSTITUYE A TODOS LOS ANTERIORES
>
> **`master` = publico `main` = `d4b885a`, CERO sin empujar** (2 commits de la sesion c
> sobre 8f683d9). Handoff:
> `AI/30_Sessions/2026-08-24c-DayZ_MCP-gate-g-re-especificado-y-sitio-canonico-certificado.md`
>
> ## GATE G RE-ESPECIFICADO: PROTOCOL_SITE_CERTIFIED
>
> Sitio canonico **NWAF [4200.0, 0.0, 10650.0]** certificado in-game (13 rondas) por
> `tools/g0_site_gate.py` + `docs/VEHICLE_TESTING.md` (0b81621): canopy 0.0, pasillo
> completo sin truncar, delta_2s_xz 3,216 m, trazas PASS, teardown verificado, release
> confirmado. Ciclo de calidad: Codex RECHAZADO 11 hallazgos -> 11 aplicados ->
> re-certificado con el driver revisado (que DEGRADO el certificado previo x4300:
> fail-closed funcionando). Ficha 2509 RESUELTA; matriz + BUG-108 causa raiz en vault.
>
> ## Hallazgos de superficie fichados (la ola siguiente sale de aqui)
>
> - fb-...-1bc1: teleport sin canopy gate entierra al jugador en edificios.
> - fb-...-638e: entities_query lejos de jugador = bimodal 0/cap (solo vale con jugador).
> - fb-...-ecf5: replica server del fixture congelada en spawn + acciones inyectadas con
>   mitad server inerte -> get-out incumplible site-independent (causa raiz BUG-108);
>   object_anim/inspect necesitan object_id.
>
> ## Proximo (por orden)
>
> 1. **Decision usuario: tren v1.0** (G ya verde): PBO del commit del tag + make_release
>    + tag + Release + demo (guion B listo) + contacto covalschi (borrador listo) - gated.
> 2. Ola siguiente: adopcion/reacreditacion durable + doctor fiel; 9287; 413a; + 1bc1/638e/ecf5.
> 3. Usuario: mover claves fuera de OneDrive + DACL (pendiente).
>

> # V 2026-08-24 AMANECER - `8f683d9` - TODO EMPUJADO - SUSTITUYE A TODOS LOS ANTERIORES
>
> **`master` = publico `main` = `8f683d9`, CERO sin empujar** (6 commits de la
> sesion b sobre e951956). Suite 2.227 tests, 0 fallos deterministas del repo
> (26 errores task9 privado preexistente; 1 flake de carga task7 anotado,
> verde x2 en solitario). Handoff:
> `AI/30_Sessions/2026-08-24b-DayZ_MCP-council-ola-respuesta-y-la-abba-que-encontro-el-sitio.md`
>
> ## G0 RESUELTO EN LO MATERIAL: los vehiculos CONDUCEN; el sitio era el bug
>
> Primera ABBA A-B-B-A completa (9 intentos, escalera en
> `reviews/2026-08-24-g0-veredicto-nocturno.md`): celdas CONTROL sobre el
> sitio rojo historico [6063,1931] reproducen la firma congelada (delta_2s
> 0,114-0,118 m = 0,115-0,126 historico); el mismo coche condujo 91 m x2 en
> z1971. **La drivability sigue al SITIO, no a la config.** Fila formal
> INCONCLUSIVE por comparabilidad de rumbos (11,6 grados; gate correcto).
> SIGUIENTE (decision usuario): re-especificar gate G sobre el protocolo de
> vehiculos (fb-20260824-025758-2509: spot canonico + checklist).
>
> ## Council de 6 + ola respuesta ATERRIZADA (todo en main)
>
> - Council COMPARABLE 2 rondas: v1.0 delgado <=14 dias; P2 time-boxed; P3
>   fuera del tren; Pack por DEFECTO. SINTESIS + lanes + r2 en
>   `reviews/2026-08-24-council-amenaza-competencia/`.
> - 82b3d16 REA docs (What-this-is-NOT, menu 3 caminos, COMPARISON.md,
>   templates con doctor) - e94662e REB release (make_release + RELEASE.md) -
>   61c9c32 P2 knowledge (find/show, 2.164 entries reales, 54->56 tools) -
>   3b27fab P1 pairing (Pack default + doctor pairing) - 421b90a seguimiento
>   (remedio sin .ps1) - 8f683d9 driver ABBA superviviente (Codex
>   APTO-CON-CAMBIOS, 4 aplicados).
> - Promo pack FINAL en `reviews/2026-08-24-promo-pack-FINAL.md` (ciclo
>   Grok->Codex RECHAZADO 11 bloqueantes->15/15 aplicadas). NADA se envia ni
>   publica sin aprobacion del usuario. Variante B (sin vehiculos) es la
>   publicable hasta formalizar G.
>
> ## Buzon 64 abiertos (triaje Grok verificado; 10 resoluciones fichadas)
>
> Top-3: adopcion de daemon rota (fb-20260824-032050-9aed: spawn propio
> acredita, adopcion no; broke-away muere con el arbol del comando ->
> memoria + runner de proceso unico `lanes/2026-08-24/g0_full_abba.py`);
> watchdog P:-vs-C: (9287); wait_for ciego a crash logs (413a).
>
> ## Proximo (por orden)
>
> 1. Decision usuario: re-spec gate G + spot canonico (2509).
> 2. Tren v1.0: PBO del commit del tag + make_release + tag + Release + demo
>    (guion B listo) + contacto covalschi (borrador listo) - todo gated.
> 3. Ola siguiente: adopcion/reacreditacion durable + doctor fiel; 9287; 413a.
> 4. Usuario: mover claves fuera de OneDrive + DACL (pendiente de anoche).
>

> # V 2026-08-24 MADRUGADA - `e951956` - TODO EMPUJADO - SUSTITUYE A TODOS LOS ANTERIORES
>
> **`master` = `e951956` y el publico `main` = `e951956`: CERO sin empujar** (dos
> pushes, 20 commits sobre `1a3fd89`). Arbol trackeado limpio. Suite completa
> 2.180 tests / 0 fallos del repo (26 errores = task9 privado no rastreado,
> import mcp_client, preexistente). **Handoff**:
> `AI/30_Sessions/2026-08-24a-DayZ_MCP-ola-adelantada-abba-y-el-punto-flotante.md`
>
> ## La noche: ola 1 (5/5) + ola 2 (4/4) + driver, todo en pareja Codex->Grok
>
> - `ed83c43` CAP identidad de captura - `2f97874` TT bridge_mod_missing (+R7 al
>   vecino) - `d6ebba1` LOOP reason tipada en el 409 - `b4108e3` SRV 5 verdades
>   (multiplier_applied=None sin readback) - `b3e11b7` ENUM alias grok->unknown
>   (**D-64 APLICADA**) - `21509a1` S10 tabla declarativa (98 bad_args fuera) -
>   `c6775f7` README+Knowledge Pack - `59db355` SRV2 42 bad_args mudos->0 +
>   unidades verificadas contra vanilla + reason en el recipe - `c334745` SRV3
>   marker en wait_for + blocked_on + TTL - `ad7fa43` re-pin supply chain (lock
>   stale desde 9b0aed2, cazado por la suite completa; bundle+registry re-anclados
>   local) - `e951956` driver ABBA G0 (15 tests de logica pura).
> - **GPREP**: diagnostico 601 lineas en `reviews/2026-08-24-g0-diagnosis.md` -
>   REFUTA is_authority_owner=0 (control positivo 35,49 m con ese patron).
> - Grok ~1,85 USD en 10 revisiones instrumentadas; Codex se llevo las rondas
>   caras (reparto por cuotas pedido por el usuario).
>
> ## Claves ROTADAS (24/24 configs vivas; fingerprint c8f2aa2fe1c8)
>
> Evidencias congeladas y 332 run-dirs quedan con la vieja (muerta). Validada
> end-to-end (daemon /status 200). Residual: la nueva sigue bajo OneDrive ->
> mover + DACL sigue pendiente (usuario).
>
> ## (!) ABBA G0: lista, parada en DOS gates fail-closed QUE FUNCIONAN
>
> PBO reconstruido+desplegado, SHA `28226C93...D700B`. Intento 1: daemon a mano
> no acredita (argv debe ser EXACTO al de build_daemon_argv, con
> --idle-timeout 1800.0 y el orden canonico). Intento 2: juego de run-s0-gate =
> `legacy_unbound` (pre-fencing; el fence exige dayz_test_run). Ambos verdicts
> tipados INCONCLUSIVE_SETUP_FAILED con cleanup limpio: el driver se comporta.
> **Reanudacion en 4 pasos**: RUNBOOK seccion 'Sesion ABBA G0' (runner MCP
> staged en TEMP dayz-mcp-lanes-2408/g0_managed_run.py). OJO: quedo un server
> s0 huerfano PID 6956 (cierre manual) y el daemon sano PID 16404 (se apaga
> solo por idle al cerrar 6956).
>
> ## (!) CAUSA RAIZ del MCP desconectado: UN `.0` (fb-20260824-000930-7af8)
>
> host_config pina tool_timeout_sec como INT 604800; el config.toml de codex
> tenia 604800.0 (round-trip TOML del CLI) -> daemon_provenance_conflict -> el
> sidecar --client muere al arrancar. MITIGADO (float->int, textual). El doctor
> daba [] con el mismo config: NO reproduce el gate. Tras el fix, session_status
> aun devuelve daemon_reaccreditation_failed (familia client_policy_untrusted
> del 23-08). Fix durable = lane futura (aceptar float integral + doctor fiel).
>
> ## Proximo (por orden)
>
> 1. ABBA con run gestionado (4 pasos del RUNBOOK; el driver HTTP crudo NO
>    necesita la reacreditacion del sidecar).
> 2. Triaje del buzon: 72 abiertos, varios cerrados de facto por los commits.
> 3. Ola 3: A-12, pin float+doctor, reacreditacion, resto de A-8.
> 4. No-lanes de diseno: puerta retail, S-4, S-7, S-9.
>

> # V 2026-08-23 NOCHE C - `9b0aed2` - NUEVE SIN EMPUJAR - SUSTITUYE A TODOS LOS ANTERIORES
>
> **`master` = `9b0aed2`. NUEVE commits locales sin empujar** sobre el publicado
> `1a3fd89`. Arbol trackeado limpio. Cero gasto en delegacion esta noche.
> **Handoff**: `AI/30_Sessions/2026-08-23c-DayZ_MCP-aterrizaje-lote-D-y-aparato-ola-1.md`
>
> ## Los 6 commits de esta noche
>
> - **`7a139d7`** QUICKSTART: fuera el parrafo del sparse-checkout que `5ebd3fb`
>   volvio falso (mandaba al lector a un "arbol hermano" inexistente). El gate
>   `SparseAddonDocsTest` existia pero SE SALTA con addon/ presente: derivo en
>   el silencio de un skip verde.
> - **`6591536`** product-spec:13: parentesis sin cerrar del parche 11->54.
> - **Lote D aterrizado en 4** (Grok implemento, Codex reviso anoche;
>   re-verificado por sustitucion antes de entrar - rojo en D-1/D-2/D-4,
>   D-3 por mutacion del centinela 4097->4096):
>   `83c068d` audit_failed ya no se traga al saturarse el semaforo -
>   `cd11fbc` _command_owner suelta la entrada al descartar -
>   `7613e65` centinela de overflow documentado y clavado con test -
>   `9b0aed2` redactor O(n^2) -> indexado, equivalencia 6.07M casos.
>   **159 tests verdes** tras aterrizar.
>
> ## Decisiones adjudicadas (usuario) -> decision-log D-63/D-64
>
> 1. **D-63**: el gate de D-4 mide PENDIENTE (<7x a 4x tamano), sin tope
>    absoluto de reloj. Aplicada en `9b0aed2`.
> 2. **D-64**: `grok` se normaliza en el limite del CLI (identidad cruda en
>    campo lateral de auditoria; formato persistente y consumidores intactos).
>    Pendiente de implementar: lane de la ola 2.
>
> ## (!) MANANA (resets Codex+Grok): la ola 1 esta LISTA PARA DISPARAR
>
> Aparato completo en `AI/10_Projects/DayZ_MCP/lanes/2026-08-24/`
> (00-RUNBOOK.md con los comandos; prep.sh; run_pair.sh; 5 briefs con citas
> verificadas el 23). **Codex implementa (brief por STDIN), Grok revisa con
> terminal (postura C); orquestador fuera hasta veredicto sin BLOQUEANTE.**
> Lanes disjuntas: SRV (A-1 retail invisible + A-14 PIN + C-2 + C-5 + A-5) -
> CAP (window/sha256 que capture_dual tira) - TT (B-1 bridge_mod_missing) -
> LOOP (R-3 reason tipada) - GPREP (diagnostico G0). Prioridad si Grok muere:
> SRV > GPREP > TT > LOOP > CAP. ANTES de nada: sonda stdin de codex (paso 0
> del runbook). prep.sh clona el master del momento -> llevan el lote D.
>
> ## Roadmap medido hoy
>
> - **Gate presentable ~1.5/6**: S-1/S-2 verificados CERRADOS; fase 0 casi
>   (falta push); gates 3-4 = lanes de manana; KNOWN-ISSUES.md sigue sin
>   existir; tercero-con-quickstart y corrida grabada requieren humanos/juego.
> - grep -c retail server.py = **0** (A-1 viva; SRV la ataca).
> - **Grupo G sigue en el plan** (usuario). G0 parado desde 06-28 sobre
>   is_authority_owner=0 + pos_delta 0.126 -> GPREP entrega diagnostico +
>   plan de UNA sesion in-game. `gear_shift` sigue sin existir.
>
> ## (!) Seguridad (sin cambios): rotar ANTES de mover/borrar
>
> Dos copias del secreto bajo OneDrive (tools/.dayz_mcp.key +
> _server/profiles/dayz_mcp.json campo key). OneDrive guarda historial.
>
> ## Entorno - pagado esta noche
>
> - **El Bash tool trunca comandos a ~6-7 KB** (firma: "line ~153 unexpected
>   EOF matching quote"). Ficheros grandes por APPENDS de <4 KB o builder .py.
> - **La capa de transporte colapsa barras dobladas a simples y fabrica
>   escapes octales**: contenido via heredoc con CERO barras invertidas
>   (rutas con barra normal; io.open las acepta en Windows). Dos escrituras
>   del HANDOFF salieron con chr(8)/chr(24) dentro y se restauraron del
>   backup; el assert de control-chars va ANTES del write desde hoy.
>
> ## Abierto
>
> 1. **Push de los 9** (del usuario). 2. **LIVE-STATE ~150K de 251K** (cierres
>    apilados; compactar = decision del usuario). 3. .git/info/sparse-checkout
>    aun contiene la regla de exclusion de addon/ (inerte, armado).
> 4. Buzon 68 abiertas; decisions/ sigue untracked (D-63/D-64 anadidos).
> 5. Trailer Co-Authored-By en commits: decision de presentacion del usuario.
>
> ## Cierre de protocolo: DEGRADADO documentado, caja limpia
>
> MCP dayz-mcp desconectado en la sesion -> session_status inejecutable.
> Evidencia directa 20:4x-21:0x: cero procesos DayZ, nadie en :8765,
> runs.json 4/4 EXITED con 0 procesos. **Nunca lease, nunca lifecycle,
> trabajo 100% offline.**

> # V 2026-08-23 CIERRE NOCTURNO - `5ebd3fb` SIN EMPUJAR - SUSTITUYE A TODOS LOS ANTERIORES
>
> **`master` = `5ebd3fb`. TRES COMMITS LOCALES SIN EMPUJAR** sobre el publicado
> `1a3fd89`. Arbol trackeado limpio.
> **Handoff**: `AI\30_Sessions\2026-08-23b-DayZ_MCP-council-y-cuatro-lanes-rechazadas.md`
> **Council**: `reviews/2026-08-23-council-showcase-benchmark.md`
>
> ## ⚠ CIERRE DEGRADADO - la caja no se pudo consultar
>
> `session_status` -> `client_policy_untrusted_open_new_session` (ficha `8da7`;
> duplica `2186`, archivada 9 min antes desde OTRA sesion). `bridge_status` ->
> `daemon_unavailable`: el daemon dejo de escuchar en `:8765` entre 18:1x y 19:0x.
> **Sustancia limpia**: `runs.json` (18:18:59) da los 4 runs en **EXITED con 0
> procesos**, incluido `e8454507`. Sin `DayZDiag_x64` vivo. Otras sesiones
> (`@Utopia_PC`, `@SUB_BRZ`) usaron la caja despues. **Nunca tuve lease.**
>
> ## Los tres commits locales
>
> - **`f168a10`** los relojes fijos de 8 s de `test_bug046` esperan la senal real
>   con tope `DAEMON_STARTUP_BUDGET_S` (`daemon.py:73`). **Sin gate rojo aqui**, y
>   el commit lo dice.
> - **`0c50706`** `fixture_daemon_cwd()`. Medido: en un clon real devuelve la
>   MISMA ruta que sustituye -> inerte para un usuario. Revertible sola.
> - **`5ebd3fb`** **manda `addon/`** (decision del usuario). Fuera la regla sparse
>   y fuera `DayZ_MCP` del orden de busqueda. Los 13 ficheros eran **identicos** al
>   hermano. **150 tests OK**; mutacion: la version vieja cae al hermano en
>   silencio, la nueva levanta `FileNotFoundError`. Ficha `9922` resuelta.
>
> ## ⚠ SEGURIDAD - lo mas urgente, y tiene ORDEN
>
> **DOS copias del secreto vivo bajo OneDrive**: `tools/.dayz_mcp.key` (44 B, del
> 5-ago, existe) y `_server/profiles/dayz_mcp.json` -> campo `key` de 43 chars en
> texto plano. **ROTAR ANTES DE MOVER O BORRAR**: OneDrive guarda historial de
> versiones, asi que migrar sin rotar no deshace la exposicion.
> No accionable como piden las auditorias ("dejar de hornear claves en configs"):
> el puente Enforce NO puede poner cabeceras, necesita leerla de un fichero.
>
> ## Reparto nocturno: 4 lotes, CERO lineas commiteables
>
> Diffs + cadena de revisiones en `scratchpad/_lotes2308/diff-{A,B,C,D}-final.patch`.
> **Si se limpia el scratchpad se pierden.**
>
> - **A** (`server.py`,`log_tail.py`) PARADO: frontera. Arreglar la mentira de
>   `ready` creo un `ready=True` falso en reinicios rapidos.
> - **B** (`test_bug046`) PARADO: 2/3 con gates causales; el 3o acabo en tautologia.
> - **C** (`inbox.py`,`control_client.py`,`server_cli.py`) PARADO: 3/4 cerrados; el
>   4o **lo hice imposible** al darle solo el productor.
> - **D** (`session_coordination.py`,`loopback.py`,`dayz_test_tool.py`,
>   `secure_launcher.py`) **4/4** salvo la forma del gate de tiempo.
>
> **Lo que la revision cruzada EVITO**: `grok` en el enum del productor sin los
> consumidores habria dejado a un cliente Grok **sin poder adquirir lease**; el
> decode global del buzon habria hecho que **un UTF-8 corrupto heredado bloqueara
> todos los appends**; un test que pasaba verde con la implementacion vieja
> sustituida; y un "off-by-one" que era un **centinela de overflow**.
>
> ## Estado fino de D
>
> - **D-1** `session_coordination.py:2371` `return False, False` en funcion tipada
>   `-> str`; los 3 consumidores (`:2004`,`:2292`,`:2348`) comparan cadenas -> al
>   saturarse el semaforo **el release omite `audit_failed`**. Aceptado.
> - **D-2** `loopback.py:1970` `.get` donde los hermanos hacen `.pop`. Aceptado.
>   (Los otros 3 `.get` en `:1812`,`:2104`,`:2169` **son legitimos**.)
> - **D-3** centinela restaurado, rojo al quitar la mitigacion. Resuelto.
> - **D-4** equivalencia sobre **6.066.620 casos** exhaustivos + 50.000 aleatorios.
>   **El codigo esta bien; el gate NO**: el tope absoluto de 0,55 s **acusa a la
>   implementacion correcta bajo carga** (pendiente valida 4,63x pero 0,666 s).
>
> ## ⚠ DOS DECISIONES QUE SON TUYAS
>
> 1. **`grok` en el enum**: normalizar en el limite del CLI (sin tocar formato
>    persistente) o propagar a los consumidores. Es frontera, no implementacion.
> 2. **El gate de tiempo de D-4**: la equivalencia ya prueba la correccion y no
>    depende de la maquina. Un umbral temporal en una suite unitaria es un banco de
>    pruebas disfrazado. ¿Debe existir?
>
> ## Auditorias externas - REFUTADO lo que no aguanta
>
> - **`SetHeader("Content-Type: ...")` es FALSO y su fix ROMPE el puente**:
>   `scripts/3_game/http/restapi.c:141` `SetHeader(string value)` toma **solo el
>   valor**, con `"application/json"` como ejemplo de Bohemia.
> - **`MCPDialogController.c +978 B`**: CRLF puro. 978 lineas, 978 CR, 0 en addon/.
> - **B-07 `_retail_quarantined`**: el daemon SI pone el probe (`daemon.py:525`,
>   `:588`); embedded no lleva `coordination`.
> - **`wait_for` doc-drift y tools 53->54**: caducadas, las cerro `9cdfc42`.
>
> **Patron**: cada hallazgo falso vino de **inferir en vez de abrir**, y las tres
> veces el informe marco bien su duda... y luego puso el item incierto en el top-3.
> Son generadoras de hipotesis, no emisoras de veredictos.
>
> ## Abierto (ademas de lo de arriba)
>
> 1. **`CLAUDE.md` del proyecto MIENTE**: `:14` "POC fase 0", `:18` "11 tools",
>    `:32` FastMCP "aun NO". Vivo: 54 tools, FastMCP corriendo, daemon 3 modos.
> 2. **`decisions/decision-log.md`: 0 commits.** D01-D21 solo en este disco. 111 `??`.
> 3. **Enforce** (`46b3`,`2cc9`,`1b40`,`3cc4`,`48bc`) espera **sesion con juego**;
>    tocar `MCPBridge.c` pone rojo el centinela de su SHA.
> 4. **Buzon: 68 abiertas.** F-01/`9287` y F-14/`dc90` siguen bloqueadas por rebuild.
> 5. **`gear_shift` no existe** (hueco real del roadmap).
>
> ## Metodo - lo que costo esta noche
>
> - **`codex exec` muere con 126/127 y CERO bytes si el prompt pasa de ~30 KB en
>   argv** (A 27.542 OK, B 20.211 OK, **C 32.394 FALLO**). La salida es **stdin**.
>   Ficha `b262`. Mi primer diagnostico (concurrencia) era **falso**.
> - **`-s read-only` bloquea crear temporales aunque pases `TMPDIR`**: los revisores
>   perdieron 45 y 19 tests en setup ajeno al codigo.
> - **HEAD detached DOS veces** durante preparacion de workspaces. Nada perdido.
>   **Comprobar `git -C <repo> rev-parse --abbrev-ref HEAD` tras cada prep.**
> - **REGLA**: **delegar defectos, no definiciones de frontera.** Si no puedes
>   escribir el criterio de fallo en una frase, no esta listo para una lane.

> # V 2026-08-23 CIERRE - `1a3fd89` - SUSTITUYE A TODOS LOS BLOQUES ANTERIORES
>
> **PUBLICADO.** `origin/main` = local = **`1a3fd89`**. **Ocho commits hoy.**
> **Handoff**: `AI\30_Sessions\2026-08-23-DayZ_MCP-el-flake-que-nunca-existio.md`
>
> ## ✅ LA CIFRA QUE IMPORTA: un clon del HEAD publicado sale VERDE
>
> Medido sobre `1a3fd89`, clon genuino (arbol trackeado + su propio `tools/.venv-mcp`
> + `pip install -e .`): **1775 tests, OK, 36 skipped, CERO fallos.**
> (1756 en `fa9c89f`, 1773 en `d4bb250` — verde las tres veces.)
> Frontera: **254 ficheros, 0 private hits**. Buzon: **48 abiertas**, 10 resueltas hoy.
>
> ## El flake NUNCA existio
>
> El README declaraba `test_bug046_startup_deadlock` como flake conocido. **No lo era.**
> El venv de DESARROLLO trae un finder editable con
> `MAPPING = {'dayz_mcp': '<...>/OneDrive/.../tools/dayz_mcp'}`, asi que cualquier
> proceso que lo use importa el paquete del checkout ORIGINAL este donde este su `cwd`.
> Correr una COPIA con el hace que el daemon hijo importe un arbol mientras su `cwd` es
> otro; la acreditacion no converge y expira el reloj de 12 s.
>
> **Descartado antes, cada uno con medicion**: layout (copia aplanada y copia fiel
> fallan igual), registro de lanzadores (mismos errores con los ficheros puestos),
> ficheros frios (2a pasada igual), carga (el spawn aislado sale en **5,1 s** contra un
> presupuesto de 12). La sospecha de antivirus se descarto al aparecer el mapping y
> **nunca se comprobo — no se afirma**.
>
> **`0225cee`** retira el aviso y repunta `FreshCloneFlakeDocsTest` a la invariante que
> si vale: la promesa de verde debe nombrar su precondicion (`tools/.venv-mcp`).
> **La lane de Grok que 'arreglo' el test NO se comiteo**: acoplaba el test al MAPPING
> del install editable — arreglaba el banco de pruebas, no el producto.
>
> ## ✅ Los ocho commits
>
> - **`a5f4bb9`** isla de fases 0-3 fuera (**4.805 lineas**) + la exencion de seguridad
>   que la sostenia. Debajo, **dos supresiones apiladas**: BOM UTF-8 en `mcp_client.py`
>   (rompe `ast.parse`, no `import`) sobre 4 hallazgos HTTP reales
>   (`:208/:209/:238/:240`). **Una exencion por NOMBRE DE FICHERO excluye ese fichero de
>   TODO, no solo del motivo por el que se concedio.**
> - **`dfae010`** guard de BOM/inparseables. Gate: con el BOM puesto, `import still OK`
>   y los dos tests rojos.
> - **`fa9c89f`** el watchdog de docs salta donde promete saltar.
> - **`de431fa`** el daemon lee su keyfile por `read_pinned_keyfile` (200 KB se leian
>   enteros antes).
> - **`5f0b50c`** floor de Python **3.11**. La ficha decia 2 sitios; **eran seis**. En un
>   3.10 real los cuatro modulos revientan al importar: `>=3.10` **era falso**.
> - **`d4bb250`** **7 citas rotas -> 0**. Nuevo `check_readme_cites.py`, que resuelve
>   **por git y no por disco**.
> - **`0225cee`** retirado el fantasma.
> - **`1a3fd89`** un clon sin construir recibe `launcher_policy_missing`, no `invalid`.
>   `list_projects` es la verb de descubrimiento. **Ningun test lo cazaba**: sale al
>   USAR el producto, no al probarlo.
>
> ## ⚠ Abierto
>
> 1. **Decision de diseno, TUYA**: los 15 tests de Enforce leen el arbol HERMANO
>    (`DayZ_MCP`), no el `addon/` publicado — con `addon/` sparse-excluido gana el
>    hermano en `_addon_paths.py:29`. Hashes coinciden HOY. O sale de la lista, o hace
>    falta guarda contra `git show HEAD:addon/...`. Ficha `fb-20260823-113719-9922`.
> 2. **F-01** (`9287`): `same_path` no resuelve el alias del subst; reproduce desde `P:`,
>    que es lo que manda el QUICKSTART. Toca `native_process_snapshot.py`, **del bundle
>    firmado** -> rebuild.
> 3. **F-14** (`dc90`): el bootstrap sube pip a "latest".
> 4. **`fb-20260823-040320-8575`**: el daemon exige que el venv se llame `.venv-mcp`.
> 5. **`architecture.md:246`** dice header `Authorization`; README y `MCPBridge.c:226`
>    usan `?key=`.
> 6. **F-12 PARCIAL**, no cerrada (correccion de un cierre mio): faltan los 18 verbos
>    de `_SCHEMALESS_COMMANDS`.
> 7. **Lote C** (rebuild) y **Enforce** (in-game) bloqueados. **Roadmap 35/38**; hueco
>    real: **`gear_shift` no existe**.
>
> ## Reparto
>
> **9 lanes de Grok ~$3,20** + Opencode gratis. **Las de SOLO LECTURA rindieron mas por
> dolar**: encontraron lo que ninguna suite vigila. **Opencode funciona sin herramientas**
> (SP-324 acota el fallo al tool-calling): leyo solo las docs, nombro el pin de Python
> como motivo n1 de descarte y disparo dos commits. **Qwen no se uso**: su tarea era un
> barrido determinista y el script costaba menos que el brief + la verificacion.
>
> ## Metodo — el instrumento mintio CINCO veces
>
> Layout aplanado · `addon/` sparse · CRLF vs LF · repo movido bajo el workspace · venv
> editable. **En dos estuve a punto de acusar a una lane de saltarse su alcance.**
> Regla: **antes de leer una diferencia como hallazgo, preguntar que mas cambio aparte
> de lo que estoy midiendo.** Control y candidato con el MISMO constructor, desde
> `git show HEAD:<path>`. Instrumental en `tools/_delegation/` (sin trackear).

> # V 2026-08-23 NOCHE - `0225cee` - SUSTITUYE A TODOS LOS BLOQUES ANTERIORES
>
> **SIN EMPUJAR.** Local = `0225cee`; `origin/main` sigue en **`fa9c89f`**.
> Cuatro commits de la noche esperan tu revision antes del push (decision tuya, tomada).
> **Siete commits hoy** en total.
>
> ## ✅ UN CLON SALE VERDE. El flake no existia.
>
> **Medido**: clon genuino con su propio `tools/.venv-mcp` (venv + `pip install -e .`),
> suite entera **1773 tests, OK, 36 skipped, CERO fallos** sobre `d4bb250`
> (y 1756 OK sobre `fa9c89f`). El modulo solo: 22 OK en 83,0 s.
>
> Los 5 errores que este HANDOFF y el README llevaban meses declarando como
> "known startup flake" **eran de mi banco de pruebas, no del producto**. Correr una
> COPIA del arbol con el venv de DESARROLLO mete al daemon por el finder editable de
> ese venv, cuyo `MAPPING` devuelve `dayz_mcp` al checkout original: el hijo importa
> un arbol mientras su `cwd` es otro, la acreditacion de arranque no converge y expira
> el reloj. Mismo codigo, misma maquina, verde en el arbol donde se instalo y rojo en
> una copia.
>
> **Descartado antes, cada uno con medicion y no con argumento**: layout (copia aplanada
> y copia fiel fallan igual), registro de lanzadores (mismos errores con los ficheros
> puestos), ficheros frios (2a pasada sobre la misma copia falla igual), carga (el spawn
> aislado sale en 5,1 s contra un presupuesto de 12 s).
>
> **`0225cee`** retira el aviso del README y repunta `FreshCloneFlakeDocsTest` — que
> obligaba a nombrar el flake — a la invariante que SI se sostiene: la promesa de verde
> debe nombrar su precondicion (`tools/.venv-mcp`). Gate: quitar la precondicion lo pone
> rojo. **La lane A de Grok arreglo el test y su diff NO se comiteo**: acoplaba el test
> al MAPPING del install editable, o sea al problema del banco de pruebas. Lo que se
> queda de ella es el diagnostico — fue quien encontro el mapping.
>
> ## ✅ Lo demas de la noche
>
> - **`5f0b50c`** — un solo numero de Python. La ficha decia 2 sitios 3.11+; **eran seis**
>   (`tomllib` en `host_config.py` y `playbooks/runner.py`; `datetime.UTC` en
>   `identity_migration`, `instance_fence`, `native_process_guard`,
>   `checks/fence_canary_probe`). En el 3.10 real de esta maquina los cuatro revientan al
>   importar: `>=3.10` no era optimista, **era falso**. Floor a **3.11**; el instalador
>   resuelve el mas nuevo por encima del floor en vez de pinear 3.14 y falla con error
>   nombrado si no hay `py`. `test_host_python_floor` (10) lo ancla.
>   **Un revisor externo que solo leyo las docs lo nombro su motivo n1 de descarte.**
> - **`de431fa`** — el daemon lee su keyfile por `read_pinned_keyfile`. 200 KB se leian
>   enteros antes, se rechazan ahora; `daemon.py`/`server.py` heredan sin cambiar.
> - **`d4bb250`** — **7 citas rotas -> 0**. El README citaba `poc-verdict.json`,
>   `HANDOFF.md`, `reviews/`, `decisions/` — **ninguno se publica**. Una decia que la
>   cifra era *"a clone can read for itself"*: falsa. Nuevo `check_readme_cites.py`, que
>   resuelve **por git y no por disco** (con `addon/` sparse, el worktree miente).
>
> ## ⚠ Abierto — por prioridad de impacto al clonar
>
> 1. **PUSH pendiente.** Cuatro commits sin publicar.
> 2. **F-01** (`9287`): `same_path` no resuelve el alias del subst; reproduce desde `P:`,
>    que es lo que manda el QUICKSTART. Toca `native_process_snapshot.py`, **del bundle
>    firmado** -> necesita rebuild.
> 3. **F-14** (`dc90`): `install-mcp.ps1:325` sube pip a "latest" -> dos clones del mismo
>    commit no son el mismo toolchain.
> 4. **`fb-20260823-040320-8575`** (nuevo): el daemon exige que el venv se llame
>    `.venv-mcp` y si no escupe `FileNotFoundError` crudo desde `identity_migration`.
> 5. **`architecture.md:246`** dice header `Authorization` mientras README y
>    `MCPBridge.c:226` usan `?key=`. Fuera del alcance de la lane H; sigue vivo.
> 6. **F-12 esta PARCIAL**, no cerrada: `548e91f` cerro los verbos CON schema; los 18 de
>    `_SCHEMALESS_COMMANDS` siguen aceptando cualquier clave. Correccion de un cierre mio.
> 7. **Lote C** (rebuild) y **Enforce** (in-game) siguen igual de bloqueados.
> 8. **Buzon**: triado entero. **13 VIVA, 4 cita movida, 6 cerradas, 27 no verificables**
>    sin juego u otros repos. 10 fichas resueltas esta noche.
>
> ## Reparto — que rindio y que no
>
> **7 lanes de Grok, ~$2,50.** Las dos de solo-lectura (auditoria de citas y triaje del
> buzon) dieron mas valor por dolar que las de escritura: encontraron lo que ninguna
> suite vigila. **Opencode funciono** — 5.077 tokens, coste 0 — pero **solo sin
> herramientas**: SP-324 acota bien el fallo al tool-calling. Su lectura adversarial del
> README es lo que disparo dos de los commits. **Qwen no se uso**: la tarea que le tocaba
> era un barrido determinista, y escribirle el brief y verificarle la tabla costaba mas
> que el script.
>
> ## Metodo — el instrumento mintio CINCO veces
>
> Cada comparacion workspace-vs-repo de la noche fallo por una causa distinta: layout
> aplanado, `addon/` sparse, CRLF-vs-LF, el repo movido bajo el workspace, y el venv
> editable. **En dos estuve a punto de acusar a una lane de saltarse su alcance.**
> La regla que sale de esto: **antes de leer una diferencia como hallazgo, preguntar que
> mas cambio aparte de lo que estoy midiendo.** Guardado en memoria.

> # V 2026-08-23 - `fa9c89f` - SUSTITUYE A TODOS LOS BLOQUES ANTERIORES
>
> `origin/main` = **`fa9c89f`**. Tres commits hoy. **SO-01 CERRADA.**
> Un clon: **1756 tests, 5 errores, 39 skips** — y los 5 son `test_bug046_startup_deadlock`,
> el modulo que el README declara. **Nada mas rojo.** Frontera: 251 ficheros, **0 private hits**.
>
> ## La isla no era solo codigo muerto: tapaba 4 hallazgos de seguridad
>
> `a5f4bb9` retira `mcp_client.py` + `mcp_server.py` + los cuatro `run-*.ps1`
> (**4.805 borradas**). El argumento no eran las lineas sino un circulo: `boundary.py`
> publicaba los runners porque la auditoria los nombraba, y la auditoria solo los
> nombraba como `evidence` de la exencion que eximia a `mcp_client.py` de ser auditado.
>
> Y debajo habia **dos supresiones apiladas**. `mcp_client.py` empezaba con **BOM UTF-8**:
> `import` funciona (Python se lo come) pero `ast.parse(read_text("utf-8"))` muere, que
> es justo el par sobre el que se construye `audit_runtime_http`. Medido:
>
> | estado | hallazgos |
> |---|---|
> | tal como se publicaba | **0** |
> | sin exencion, con BOM | 1 `parse_error` |
> | sin exencion **y sin BOM** | **4** (`sensitive_body` + `urlopen`, `:208/:209/:238/:240`) |
> | sin exencion y sin la isla | **0** |
>
> **La leccion, que es lo que vale**: una exencion indexada POR NOMBRE DE FICHERO excluye
> ese fichero de TODO, no solo del motivo por el que se concedio. Concedida por X, cubre
> gratis un Y desconocido. Ficha `fb-20260822-225038-134e`.
> `dfae010` fija el defecto en origen (cero BOM, cero inparseables en los 248 `.py`),
> con gate que demuestra la asimetria: con el BOM puesto, **`import still OK`** y los dos
> guards rojos.
>
> ## ❌ Corregido de MI PROPIO handoff anterior (no lo heredes)
>
> - **`LoopbackServer` NO es historico.** `server.py:41/:541` lo instancia. El bloque de
>   ayer lo daba por retirable; borrarlo habria roto el modo embedded. Solo se fue
>   `loopback.main()` (sin invocacion documentada).
> - **`run-step0.ps1` / `run-s0-gate.ps1` NO habia que tocarlos**: el primero usa
>   `mcp_server_step0.py` (otro fichero), el segundo cita `run-fase3` en un comentario.
> - **Los 163 tests "limpios" NO son publicables.** Montados contra un arbol solo-trackeado
>   **fallan los 163**, 8 de 8 ficheros: cuatro por `_session_coordination`, uno por
>   `_broker`, uno por `h8_distributed_codex_gate`, y `test_task9_protocol_docs.py:11`
>   **hardcodea `C:\Users\guill\ObsidianVault\...`**. No es cobertura retenida: es
>   cobertura de andamiaje interno que correctamente no viaja. **El README no necesita
>   decir nada** — presumir de 340 tests que el lector no puede ver seria peor.
>
> ## Metodo — el error que casi cuesta un diagnostico entero
>
> **Construi mal el instrumento y lei 26 regresiones que no existian.** El workspace
> aplanaba `tools/` a la raiz (correcto para que la lane hiciera `import mcp_client`,
> equivocado para medir la suite), asi que todo test que sube a la raiz buscando
> `CLAUDE.md`, `playbooks/`, `addon/` erraba por causa ajena. Y reconstruirlo desde el
> worktree se dejaba los **13 ficheros de `addon/`** que son tracked pero skip-worktree
> por sparse checkout, que un clon SI tiene.
> **La forma correcta**: control y candidato con el MISMO constructor, desde
> `git show HEAD:<path>`, distintos solo en el delta; y diffear las dos listas de fallos.
> Salieron identicas (7 y 7) -> el cambio no rompio nada. Sin control no se distingue una
> regresion de un artefacto del instrumento.
>
> ## ✅ Tercer commit: honestidad del clon (`fa9c89f`)
>
> Ese mismo experimento revelo que **un clon veia 7 errores y el README declaraba 1**.
> Dos eran del watchdog de docs leyendo ficheros fuera del boundary y reventando con
> `FileNotFoundError` donde el README promete skip con motivo. El de citas vanilla ya
> guardaba... sobre `P:/scripts`, **que todo modder de DayZ tiene montado porque el
> QUICKSTART lo exige**: fallaba justo para el publico objetivo. Ahora saltan.
> Los 5 restantes son `TimeoutExpired` del daemon bajo carga; el README dice el numero
> y la causa en vez de "one known flake".
> **El watchdog de ayer se gano el sueldo**: cazo que `PROJECT-MAP.md` seguia listando
> los cuatro runners como entry points. Nadie mas lo habria visto — esta fuera del
> boundary, asi que no estaba ni en el workspace de la lane ni en los clones.
>
> ## ⚠ Abierto
>
> 1. **Lote C, bloqueado por el REBUILD**: `_mods` (listo en `scratchpad\_pending_mods\`,
>    7 tests) + F-01, F-04, F-05, F-14, P-03. Los cinco tocan `_APP_PACKAGED_MODULES`
>    (los 14 de `native_bundle.py:65`; **`loopback.py` NO esta ahi**, por eso hoy no hizo
>    falta rebuild). **Un ciclo de build cierra los seis.**
> 2. **Enforce (`.c`)**: F-08, F-09, P-01, P-02, P-04. `addon/` sparse-excluido y
>    `MCPBridge.c` con SHA congelado (`test_full_source_hash_is_frozen`). Ciclo in-game.
> 3. **F-01 sigue vivo**: `same_path` no resuelve el alias del subst; reproduce **desde `P:`**.
> 4. **El buzon viola su premisa** (`fb-20260822-193753-6271`): 5 registros >4 KiB sobre
>    una premisa de "<4 KB". Ahora **50 abiertas**.
> 5. **Roadmap 35/38.** Solo G0/G1/G3, gates **in-game**. Hueco real: **`gear_shift` no existe**.
> 6. **Sin resolver, de baja prioridad**: `boundary.py` reporta 14 "reads excluded
>    artifact"; son estaticos y ya no producen rojo en un clon (los tests saltan), pero
>    el check no distingue una referencia guardada de una sin guardar.

> # V 2026-08-22 NOCHE - `548e91f` - SUSTITUYE A TODOS LOS BLOQUES ANTERIORES
>
> **Handoff**: `AI\30_Sessions\2026-08-22-noche-DayZ_MCP-tres-auditorias-y-el-alias-de-P.md`
>
> `origin/main` = **`548e91f`**. **12 commits hoy.** Suite **2095** (1 fallo: el
> flake `test_bug046` que el README declara). Frontera: 258 ficheros, **0 private
> hits**. Buzon **49 abiertas** (entraron 19 de auditoria validada).
>
> ## El hallazgo de la noche: los dos mediamos bien, en mundos distintos
>
> Codex dijo que `test_parent_watchdog` falla; yo medi **14 OK**. Grok resolvio la
> contradiccion: **Codex corria desde `P:` y yo desde `C:`**. `same_path`
> (`native_process_snapshot.py:59-64`) compara texto y **no resuelve el alias del
> subst**; `os.path.samefile` si. Reproducido: desde `P:`, **1 fallo**.
> Y **`QUICKSTART.md:16-19` te dice que uses `P:`** -> la configuracion que falla
> es la documentada. El `README.md:228-230` solo exceptua el `test_bug046`.
> **"No reproduce" no es una refutacion hasta saber en que entorno midio el otro.**
>
> ## ✅ Publicado hoy
>
> - **`9cdfc42`** — `wait_for` en timeout devuelve `ok:true`. Cuatro sitios
>   llevaban **dos contratos**: los dos READMEs decian `ok:true`, y el codigo *y la
>   descripcion de la tool* lo contrario. **El watchdog de docs NO lo cazaria**:
>   vigila conteos y citas, **no promesas de comportamiento**. Ademas `ui_dialog`
>   entra al README (conteo **54**): el documento y `PublicToolCountDocsTest`
>   **se daban la razon omitiendo la misma tool real**.
> - **`548e91f`** — cinco fichas auditadas, dos lanes repartidas **por fichero**:
>   lifecycle atrapado en `repairing`; un hilo por cada TCP ocioso sin techo (24
>   conexiones mudas -> de 2 a 26 hilos); resultados sin TTL; claves extra; y un
>   audit **lento** reportado como **fallido**.
> - **`0087bb5`** watchdog de 18 aserciones · **`8d5d34d`** `Handler.timeout=35.0`
>   anclado **por AST** al tope del `/wait`.
>
> ## Las tres auditorias aguantan
>
> **Cuando Codex da un numero, el numero esta bien**: `validate_command_args` 389
> lineas y rango `:262-650` exacto; `acquire` 431 desde la `:265` exacto; los dos
> bridges 3.492 / 3.444 (±1 de convencion). **6 de 6.** De sus 19 hallazgos, 18
> encolados y solo P-05 refutado ("no compile / no jugue" no es un bug).
>
> ## ❌ Descartado con medicion
>
> - **Opencode Zen**: 4 intentos, **0 llamadas y 0 tokens** en las 3 tareas
>   sustanciales; la sonda sin herramientas responde en 23 s con el mismo `--dir`.
>   **Es su tool-calling**, no el servicio.
> - **Gemini** (council): `limit: 0`. **Composer**: ni skill ni CLI ni proveedor.
>
> ## ⚠ Abierto
>
> 1. **La isla historica (SO-01)**: `mcp_client.py` + `mcp_server.py` + los cuatro
>    `run-*.ps1` = **4.533 lineas**, ninguna doc publica los cita. El argumento
>    fuerte no son las lineas: `security_runtime_audit.py:67-81` tiene una entrada
>    `RUNTIME_HTTP_EXCLUSIONS` que **exime a `mcp_client.py` de una auditoria de
>    seguridad**. **NO es un borrado, es un refactor**: hay que actualizar
>    `test_dependency_lock`, `publish/boundary.py`, `run-step0.ps1` y
>    `run-s0-gate.ps1`. *Mi grep de `mcp_server` esta inflado (casa con
>    `mcp/server/` del venv): separarlo antes de actuar.*
> 2. **Lote C, bloqueado por el REBUILD**: `_mods` (listo en
>    `scratchpad\_pending_mods\`, 7 tests) + F-01, F-04, F-05, F-14, P-03 — los
>    cinco tocan `_APP_PACKAGED_MODULES`. **Un ciclo de build cierra los seis.**
> 3. **Enforce (`.c`)**: F-08, F-09, P-01, P-02, P-04. `addon/` sparse-excluido y
>    `MCPBridge.c` con **SHA congelado**. Requieren ciclo in-game.
> 4. **El buzon viola su premisa** (`fb-20260822-193753-6271`): justifica el diseno
>    sobre "append <4 KB" y permite 8.000 chars. **5 registros >4 KiB, mayor 7.295 B.**
>    Integridad intacta (441 lineas, 0 ilegibles). Id de 16 bits: 0 colisiones en
>    245, pero **hoy hubo rafaga de 18 en el mismo segundo**.
> 5. **336 tests sin publicar**: 7 limpios (publicarlos), 2 fuera a proposito por
>    `cfa353b`. Decidido: **decirlo en el README**.
> 6. **Roadmap 35/38.** Solo faltan **G0/G1/G3**, gates **in-game**. Hueco real:
>    **`gear_shift` no existe** y G1 lo exige.
>
> ## Metodo — errores mios de esta noche
>
> - **Cambie lo que estaba midiendo a mitad de la medicion** (apliqué 8 ficheros
>   con la suite corriendo): 3 fallos que eran mios y pasaban aislados.
> - **Un grep no es una dependencia**: escale un "import" que era **una cita en un
>   docstring**; el barrido AST dio **cero imports reales**.
> - **`&` dentro de una tarea ya en background** deja vivo al hijo y mata el aviso.
>   Hermano de SP-314 al reves: comprobar si el proceso vive antes de darlo por muerto.
> - **No filtres la suite por `tail`**: perdi el detalle de 4 errores.

> # V 2026-08-22 TARDE - `fc682b1` - SUSTITUYE A TODOS LOS BLOQUES ANTERIORES
>
> **Handoff**: `AI\30_Sessions\2026-08-22-DayZ_MCP-tarde-la-mitad-que-no-era.md`
>
> `origin/main` = **`fc682b1`**. Suite **2069 OK (skipped=4)**. Frontera: 256
> ficheros, **0 private hits**. Buzon **95 -> 29** abiertas. Arbol limpio.
>
> ## El hilo: dos commits mios arreglaron la mitad que NO era
>
> `a0bf94f` cerro el caso "dos runs vivos" del `min()` de `_run_start_epoch` —
> que yo mismo habia medido **inalcanzable** (126 runs acumulados, nunca dos con
> procesos). La otra rama del mismo `if` es la que ocurre: **sin ningun run vivo**
> el epoch es `None`, `_current_launch_logs` cae al fallback y **ese no mira la
> edad**, asi que `wait_for` escanea una corrida muerta y puede dar
> `satisfied:true` sobre una linea de hace horas. Confirmado en la maquina: 2
> runs, los dos EXITED, epoch `None` en ese instante.
>
> **Y uno de mis 11 tests fijaba ese fallback como correcto.** Pase un gate de
> mutacion de 4 inversiones y las 4 se cazaron — pero ninguna cubria el camino
> real. Un gate prueba que tus tests vigilan **lo que rompiste a proposito**, no
> que hayas elegido bien que romper.
>
> Lo encontro **Grok triando el buzon** (`fb-20260821-232055-c90e`), citando
> `process_lifecycle.py:2370` (`list_runs()` sin filtrar estado; comparese `:1195`
> que si filtra). Y escribio el arreglo: **`fc682b1`, 14 turnos, $0,14**.
>
> ## ❌ RETRACTADO - A1 y A2 del audit, que yo habia dado por CONFIRMADOS
>
> - **A1** ("cero `timeout` en la clase `Handler`"): **falso, hay 7**, todas del
>   `timeout_s` del `/wait`. Sobrevive algo mas estrecho: no hay atributo de clase
>   `timeout` y `BaseHTTPRequestHandler.timeout` es `None` (medido ejecutandolo),
>   asi que `readline` bloquea ante un cliente que calla. **No es de una linea**:
>   `socketserver` lo aplica a la conexion entera y `/wait` bloquea hasta 30 s.
> - **A2** (`_condition.wait()` sin plazo): **10 de 11 mutaciones de la FIFO
>   llevan `notify_all`**; la 11a es un `append` que no debe despertar a nadie. Y
>   son **56 `notify_all`, cero `notify`**. No es riesgo de cuelgue.
>
> ## ✅ Publicado hoy
>
> - **`15db113`** — 30 parches a 7 docs publicas. QUICKSTART prometia "Python
>   3.10+" y `install-mcp.ps1` pide `py -3.14` con fallback **solo si falta el
>   lanzador**; `README-mcp` tenia el gate de canopy **al reves**; y **9 tools
>   mutantes** no salian en ninguna lista de lease.
> - **`fc682b1`** — guarda `no_active_run`. Los fixtures se movieron porque
>   describian **un estado imposible** (run vivo sin procesos, que `validate` solo
>   permite en EXITED). Que la guarda los pusiera en rojo es la guarda funcionando.
>
> ## ⚠ ABIERTO — los dos primeros son DECISION DEL DUENO
>
> 1. **El puente no se inyecta**: `dayz_test_worker.py` `_mods` nunca anade
>    `@DayZ_MCP`. Esta en **`_APP_PACKAGED_MODULES`** (`native_bundle.py:65`):
>    obliga a reconstruir y refirmar el launcher.
> 2. **`Handler` sin plazo de socket** (ver A1 arriba). Cualquier valor <=30 s
>    rompe el `/wait`.
> 3. **29 fichas** abiertas. Graves sin dictamen: `9486`, `9359`, `c931`, `00c4`.
> 4. **Watchdog de docs** de GLM, con gate medido, en scratch. Antes de meterlo,
>    quitarle la clase que su autor declaro FUERA del gate.
> 5. `product-spec.md:369` cita `server.py:50`; esta en la **52**, y ya estaba mal
>    antes de los parches (verificado contra el backup).
>
> ## Delegacion — lo aprendido, con numeros
>
> - **Una afirmacion falsa POR LANE, en las cuatro que entregaron.** Y **Qwen y yo
>   dimos la misma respuesta equivocada sobre A1**, por razones distintas: tomar
>   esa coincidencia por corroboracion la habria dado por doblemente confirmada.
> - **Grok es el mejor ejecutor medido**: buzon 95->29 por **$1,64**; el arreglo
>   por **$0,14** con gate de mutacion y las tres salidas pegadas.
> - **Acotar el alcance de un brief oculta call-sites.** Le prohibi correr la
>   suite; sus tres modulos salieron verdes y la suite dio **4 errores** con el
>   mismo fixture imposible. El brief debe decir *"grepea quien mas usa esto"*.
> - **No filtres la suite por `tail`**: perdi el detalle de esos 4 errores.
> - Descartado con evidencia: **Composer** (ni skill ni CLI ni proveedor del
>   council), **Zen** (3 corridas, 0 entregas), **Gemini** (`limit: 0`),
>   **Ornith** (424 s vs 250,5 s con 1,5x menos ventana; su `num_ctx` es un
>   escalon: 64k -> 18,25 tok/s, 98k -> **3,31**).
> - Nuevo al ledger: **SP-313** (el gate de citas falla por el NOMBRE del campo),
>   **SP-314** (Job Object mata la corrida larga; rodeo por WMI), **SP-316**
>   (`tool_grep` trunca a 80/8 **sin marcarlo** — origen de la A1 falsa).
>
> `NEXT-SESSION-PROMPT.txt` reescrito: llevaba desde el **9 de junio** mandando
> arrancar la Fase 3.

> # ADENDA 2026-08-22 tarde - `a0bf94f` - CORRIGE los puntos 2 y 6 del bloque de abajo
>
> Dos cosas que el bloque de la noche daba por sabidas y no lo estaban. Las dos
> se cayeron al medirlas, y las dos eran mias.
>
> ## ✅ Cerrado: el `min()` de `_run_start_epoch` (`a0bf94f`)
>
> El bloque de abajo lo pone **#2 por coste** con "alcanzabilidad sin medir". Ya
> esta medida: **no es alcanzable**. `RunRecord.validate`
> (`process_lifecycle.py:397-408`) obliga a que `EXITED` lleve `processes` vacio
> y a que `RUNNING`/`RUNNING_IDLE` lo lleven lleno, asi que **solo un run vivo
> aporta tiempos**; en el almacen vivo mas sus seis backups (**126 runs**
> acumulados en el mayor) la cuenta de runs con procesos fue **0, 1, 0, 0, 0, 0,
> 1** - nunca dos. Yo lo sobrevalore.
>
> Cerrado igualmente porque cuesta cero: con un solo run vivo, `max` sobre los
> arranques por-run y `min` sobre la lista aplanada **devuelven el mismo float**.
> Es una guarda, no una reparacion, y el commit lo dice.
>
> Lo que si era un agujero: `_run_start_epoch`, `_current_launch_logs` y
> `_wait_for_script_log_paths` **no tenian ni un test**. Ahora 11, con gate de
> mutacion de 4 inversiones, cada una cazada por el test que la nombra y el
> fichero restaurado byte a byte. Suite **2055 -> 2066**; el unico fallo es
> `test_bug046_startup_deadlock` (flake conocido, no menciona ningun simbolo
> tocado, verde 3/3 aislado). Frontera: 255 ficheros, **0 private hits**.
>
> ## ✅ Cerrado: Ornith vs Qwen - **gana Qwen**, y el benchmark publico no transfirio
>
> Instrumentado por Grok (postura C, 35 turnos / 45,5 min / **$0,42**).
>
> | | `qwen3.8:98k` | `ornith-ab:64k` |
> |---|---|---|
> | ventana / GPU | 98.304 / **100%** | 65.536 / 93,66% |
> | llamadas / turnos / reloj | 70 / 31 / **250,5 s** | 57 / 31 / 424,0 s |
> | fichas con hecho | 8/8, las 8 `VERIFICADO` | 8/8, 7 + 1 `NO_VERIFICADO` |
> | citas inventadas (a mano) | **0** | **0** |
>
> Qwen: **1,7x mas rapido con 1,5x de ventana**. **DeepSWE 42,2 vs 22,0 NO
> transfirio** a verificar-y-citar. Para un problema de longitud, Ornith no es la
> salida en esta tarjeta.
>
> **La escalera de `num_ctx` de Ornith es un escalon, no un degradado** (los
> cuatro peldanos cargan; `size` apenas crece porque el KV de un MoE A3B es
> diminuto):
>
> | peldano | %GPU | tok/s en caliente |
> |---|---|---|
> | 32k | 97,45 | 21,09 |
> | 48k | 96,03 | (sin medir) |
> | 64k | 93,66 | 18,25 |
> | 98k | 90,29 | **3,31** |
>
> Desbordar un 6% sale casi gratis; el siguiente escalon cuesta **6x**. Mi
> descarte de ayer ("desborda al 81%") medía un tag **sin fijar** que cogia los
> 262.144 de arquitectura. Queda `ornith-ab:64k`; los otros tres borrados.
>
> **Una corrida por modelo, sin varianza.** Es la limitacion del resultado.
>
> ## Nuevo al ledger de skills
>
> - **SP-313**: `verify_citations.py` da **`NO_FILE` en las 8 por el NOMBRE del
>   campo** - `field()` (`:58-64`) busca `**fichero**` y el brief pide
>   `**fichero:linea**`. Tercer modo de falso negativo, y **ninguno es
>   invencion**: a mano, 0 inventadas / 7 formato / 1 ausencia real, **identico en
>   los dos modelos**. Un gate que no separa formato de fabricacion es peor que no
>   tenerlo, porque tiene la autoridad de un numero.
> - **SP-314**: una corrida local larga **muere al cerrarse el shell que la
>   lanzo** (Job Object con kill-on-close; la pertenencia se hereda y
>   `Start-Process` no salva). Se rodea creandola por WMI
>   (`Win32_Process.Create`). Se comio la primera corrida de A y parecia un
>   cuelgue del modelo.
>
> ## ⚠ Sigue abierto (renumerado)
>
> 1. **A1 / A2 del audit**: `Handler` sin `timeout` en `loopback.py`;
>    `session_coordination.py:671`, `:770` y **`:2298`** con `wait()` sin
>    deadline. Concurrencia del daemon.
> 2. **El puente no se inyecta**: `dayz_test_worker.py:204-210` nunca anade
>    `@DayZ_MCP`, y ninguno de los 10 proyectos lo tiene en `default_base_mods`.
>    **Reproducido por los dos modelos locales** en este A/B (ficha `486b`), sobre
>    lo ya verificado por mi.
> 3. **90 fichas** del buzon sin triar.
> 4. **Lane de docs-truth-check** sin correr; brief listo.

> # V 2026-08-22 CIERRE - `f86d1c3` - SUSTITUYE A TODOS LOS BLOQUES ANTERIORES
>
> **Handoff completo**: `AI\30_Sessions\2026-08-22-DayZ_MCP-la-noche-de-los-fallos-mudos.md`
>
> ## Estado
>
> `origin/main`: `90d29a0` -> **`f86d1c3`** (5 commits). Suite **2020 -> 2055 OK
> (skipped=4)**. Arbol limpio. Frontera: 0 private hits, **0 rutas del autor publicadas**.
>
> Hilo de la noche: **cinco defectos que compartian forma — la herramienta falla y no
> dice que fallo**. Costaron dos tardes a dos sesiones antes de que nadie mirara el
> mecanismo.
>
> ## ✅ CERRADO
>
> - **`wait_for(log_matches)`** (`397212d`): `pattern` es SUBCADENA y no estaba
>   documentado (medido: `\[DayZ-MCP\]` -> 0 hits, `[DayZ-MCP]` -> 1); las lineas de
>   arranque eran inalcanzables (posicion **20 de 132.632**, tope 2000, y **0
>   apariciones en el RPT** de 165.669 porque espeja `SCRIPT` ~16 s tarde). Nuevo
>   `lookback_from="launch"` + campo `scanned`.
> - **`dayz_test_run` mudo** (`f0ecb5f` + `ba31648`): fallback estructural por token con
>   forma de identificador; gate AST de 3 a 8 modulos; y la causa completa al log LOCAL
>   — el comentario ya decia "for LOCAL diagnosis" y **nada la imprimia**.
> - **Fuga de ruta del autor en `capture_screenshot`** (`a09d92c`): `mcp_capture`
>   interpola `GRAB_SCRIPT` (ruta absoluta), stderr crudo y texto de excepcion, y
>   `server.py` lo reenviaba al cable. Gemelo en runtime de lo que la frontera para.
>
> ## ❌ HIPOTESIS DESCARTADAS CON SONDA PROPIA — no las repitas
>
> - **Logs en UTF-16 / cp1252**: 48 logs, 3 mods, ambos lados -> **los 48 UTF-8 estricto**.
>   Esto REFUTA el hallazgo **A9** del audit del 22-ago.
> - **`stat` atrasado por metadatos NTFS**: `stat` vs `fstat` vs `seek(END)`, 6 rondas,
>   coinciden siempre. *No crecian durante la sonda: probe concordancia, no bajo escritura.*
> - **La avalancha de `OnStoreLoad`**: 16.886 lineas, **12,7%** del fichero, no «7,6 MB».
>
> ## ⚠ ABIERTO, por orden de coste
>
> 1. **A1 / A2 del audit, confirmados por mi**: `Handler` sin `timeout` en `loopback.py`
>    (grep de "timeout" en la clase: cero) y `session_coordination.py:671` y `:770` con
>    `wait()` sin deadline — mas **`:2298`, que el audit no vio**. Concurrencia del
>    daemon: no es trabajo de lane.
> 2. **`_run_start_epoch` usa `min()`** (`server.py:1521`): con varios runs listados coge
>    el arranque mas viejo y deja pasar logs de corridas anteriores -> **falso
>    `satisfied:true`**. Peor que un falso timeout. Mecanismo confirmado, **alcanzabilidad
>    sin medir**.
> 3. **El puente no se inyecta**: `dayz_test_worker.py:204-210` (`_mods`) nunca anade
>    `@DayZ_MCP`, y **ninguno de los 10 proyectos** lo tiene en `default_base_mods`. Solo
>    llega por casualidad en el proyecto `DayZ_MCP`. En los otros nueve un run gestionado
>    arranca **sin puente** salvo `extra_mods`. Localizado por Qwen, verificado y ampliado
>    por mi.
> 4. **82 fichas** del buzon sin triar (219 entradas / 110 cerradas / **89 abiertas**).
> 5. **Lane de docs-truth-check** sin correr; brief listo.
> 6. **Prueba Ornith vs Qwen** pendiente (ver abajo).
>
> ## Delegacion: lo aprendido, con numeros
>
> - **Ninguna pasada estatica es una lista de hallazgos hasta que alguien mide.** Cayo 1
>   de 4 del audit, 1 de 4 de GLM, 0 de 4 de Grok. La convergencia entre lanes NO es
>   verificacion.
> - **GLM `/goal`**: **135/135 citas OK, cero deriva**, parada por condicion buena en la
>   vuelta 14. El bucle funciono; la clave fue criterio contable + ledger append-only.
> - **Grok**: 20 veredictos aplicados, $0,47. Sus 3 citas de RESUELTO, exactas.
> - **Qwen**: `junior_agent.py` **no pasa `think: false`** aunque su skill lo llama «el
>   ajuste que decide si entrega o no». Primer intento: muerto a los 1800 s en UNA
>   llamada. Con el parche: **64 llamadas / 925 s / $0**. Tampoco pasa `num_predict`.
> - **`verify_citations.py` dio 0/8 por FORMATO, no por fabricacion** (rangos y bloques
>   multilinea). Su veredicto `NO_LITERAL` no distingue «me diste un bloque» de «te lo
>   inventaste», y eso empuja a desconfiar de trabajo bueno.
> - **Ornith vs Qwen**: descarte `ornith-1.5:35b` por desbordar al 81% GPU y **me
>   equivoque** — es MoE de 3B activos y desbordar no cuesta lo mismo. Medido en casa:
>   2,4 min al 81% contra 5,8 min al 43% del 27B denso. Publico: **DeepSWE 42,2 vs 22,0**.
>   Antes de probarlo hay que **cocer el tag con `num_ctx` fijado**: los tres candidatos
>   (`ornith-1.5:35b`, `:9b`, DeepSeek 9B) estan **sin fijar** y cogerian los 262.144 de
>   arquitectura.
>
> ## Gotchas nuevos
>
> - Un gate de `test_dependency_lock` escanea **constantes AST** buscando `.ps1`/
>   `powershell`/`pwsh`. Un docstring con esa palabra pone la suite roja. **Cambia la
>   prosa, no el gate.**
> - `PROJECT-MAP.md` manda leer 161 lineas de un bloque vivo que acaba en la **1642** (el
>   **6%**), y esta guardado con la codificacion rota (`lÃ­neas`).
> - Raiz del proyecto: **12 GB**, de los cuales `_fase3` 4,2 · `_fase2` 2,5 · `_s0` 2,0 ·
>   `_poc` 1,5. Borrarlos es destructivo y lo decide el dueno.
> - **No hay CI**: ni `.github/`, ni `tox.ini`, ni `Makefile`, ni `conftest.py`.

> # V 2026-08-21 CIERRE DE SESION - `90d29a0` - SUSTITUYE A LOS TRES BLOQUES DE HOY
>
> **Handoff completo**: `AI\30_Sessions\2026-08-21-DayZ_MCP-fase-0-cerrada-y-fase-1-a-medias.md`
> Aqui solo el estado y lo que esta abierto.
>
> ## Estado
>
> `origin/main`: `e106cf2` -> **`90d29a0`** (5 commits). **253 ficheros publicados, CERO rutas
> privadas, suite `Ran 2020`.** Arbol limpio, nada sin commitear. Frontera: 252 ficheros,
> 0 private hits, `import check: clean`.
>
> Fase 0 **cerrada**. Fase 1 hecha salvo **P-3 (instalador unico)**, que queda fuera a
> proposito: es rediseno, no mecanica, y merece plan propio.
>
> ## ✅ CERRADO: `wait_for(log_matches)` — `397212d`, empujado
>
> Eran DOS defectos, y **ninguno de los tres que sospechabamos**. Replay del
> matcher contra los ficheros reales, sin juego:
>
> - **`pattern` es SUBCADENA, no regex** (`pattern in line`). El contrato no
>   estaba escrito en ningun sitio. Las dos sesiones mandaron un patron
>   regex-escapado. Medido: `r"\[DayZ-MCP\]"` → **0 hits**, `"[DayZ-MCP]"` →
>   **1 hit**, mismo fichero, misma linea.
> - **Las lineas de arranque son inalcanzables**: la linea estaba en la
>   **posicion 20 de 132.632**, y `lookback_lines` topa en 2000. Tampoco estaba
>   en el RPT de ese arranque (0 hits en 165.669 lineas) porque el RPT solo
>   empieza a espejar `SCRIPT` a los ~16 s.
>
> Las tres hipotesis del handoff anterior quedan **descartadas y medidas** — no
> las repitas: la inundacion de `OnStoreLoad` (son 16.886 lineas, el 12,7%, no
> lo que reporte); el `mtime` NTFS perezoso; y "el matcher solo sigue RPT" — esta
> ultima venia de que `observed` devuelve `lines[-1]`, la ultima linea del
> fichero mas reciente, asi que el script log SI se leia.
>
> **Arreglado**: contrato documentado en la tool y los dos README;
> `lookback_from="launch"` (opcional, default = comportamiento de hoy) escanea
> el log de este arranque desde el byte 0 con techo de 64 MiB y
> `scan_truncated`; `scanned` en la respuesta dice que ficheros se leyeron y
> cuantas lineas dio cada uno. 21 tests nuevos, **8/8 mutaciones cazadas**,
> suite **Ran 2041 OK** (antes 2020), frontera 253 ficheros / 0 private hits.
>
> **PENDIENTE, unico**: gate in-game. El escaneo de arranque no se ha probado
> contra un juego vivo. Y **el daemon corriendo lleva el codigo viejo** hasta
> que se reinicie — no se reinicio a proposito: puede haber un peer usandolo.
> Buzon: `fb-20260821-200958-f30c` (el arreglo), con `-132319-5034` y
> `-160709-86a6` resueltas.
>
> ## ✅ CERRADO: `dayz_test_run` ya no se traga el nombre de su fallo — `f0ecb5f`
>
> `dayz_test_failed:ValueError` pelado costo DOS HORAS a la sesion LFPowerGrid, que
> ademas llego con una teoria falsa (un PBO reemplazado habria roto un anclaje).
> **Medido: nada ancla el SHA del PBO staged** — la policy solo fija rutas con
> `allow_root_junction`, y el grep de `.pbo`/`pbo_sha`/`staged` en los cuatro
> modulos de la ruta da CERO. No habia nada que re-anclar. Y su llamada exacta
> **me funciono desde otra sesion** (`succeeded`, 0,941 s), asi que era su proceso
> cliente, no el proyecto.
>
> Causa del silencio: `server.py` envuelve `execute_dayz_test_run` en
> `except Exception` y re-lanza **solo el tipo** (a proposito: el mensaje puede
> llevar rutas del host), y el mapa de tokens tenia 9 entradas. `control_client.py`
> tiene 7 sin mapear, en la ruta del lease interno.
>
> Arreglado sin lista que mantener: un token con forma de identificador llega
> **con nombre**; lo que lleve separador, punto, espacio o dos puntos sigue mudo.
> El gate AST pasa de 3 modulos a 8 y exige el RESULTADO, no pertenencia a un dict.
> 4/4 mutaciones cazadas, suite **Ran 2047 OK**.
>
> ## ⚠ ABIERTO: la familia `test_bug046_*` es flaky bajo carga
>
> Dos modulos distintos hoy, rojos en la suite completa y verdes aislados
> (`startup_deadlock` 22-22, `audit_fault_recovery` 44-44 **en 1,7 s** -> contencion, no
> lentitud). Hipotesis sin medir: la 2a fue con `box.occupied=true`, `ports_in_use=[2302]`
> por una corrida ajena; son los tests que mas daemons reales levantan. Para decidirlo: N
> corridas con la caja libre vs N con la caja ocupada.
> Buzon: `fb-...-152733-ce78`, `fb-...-182936-1c14`.
>
> ## ☑ RESUELTO SOLO: el par de ForzaDayZ ya no existe
>
> Hay un par **servidor+cliente de ForzaDayZ/WRX** vivo (PIDs 18372/45168, padre muerto ->
> `PROCESS_UNREGISTERED` en el doctor) con **un jugador conectado** (`Player "Dev" is
> connected`, 20:16). Ocupa la caja (`ports_in_use=[2302]`). La sesion `lfpowergrid-88` pidio
> matarlos creyendolos huerfanos de un launch muerto; **NO se hizo**: huerfano (padre muerto)
> no es lo mismo que inactivo, y un peer no autoriza saltarse la regla del lifecycle guard.
> Lo cerro quien lo abrio: a las 20:36 los PIDs 18372/45168 ya no existian y la caja
> pasa a un run GESTIONADO de `@SUB_BRZ` (`foreign: []`). **No queda nada que decidir**;
> se deja escrito por la leccion, no por la accion.
>
> ## Gotchas de entorno (detalle en el handoff de sesion)
>
> - **AddonBuilder no empaqueta `.c` por defecto**: sin `include.lst` el PBO sale de 571 bytes.
>   Sin argumentos abre GUI y no vuelve. Escribe progreso a stderr -> con
>   `$ErrorActionPreference='Stop'` PS 5.1 aborta un build que va bien; solo decide el exit code.
> - **`dayz_test_stop` vuelve antes de que el proceso muera** -> `Device or resource busy`
>   sobre el PBO justo despues.
> - **Restaurar el bundle con `rm -rf` + `cp -r` NO restaura**: el registro pinea el
>   `root_file_id` NTFS. Tocar `PACKAGED_MODULES` obliga a reconstruir Y re-registrar
>   (secuencia verificada 3 veces, en el handoff de sesion).
> - **`addon/` esta bajo sparse-checkout**: se prepara por indice y hay que **restaurar
>   `skip-worktree` despues**, o un `git add -u` lo borra del repo.
>
> ## Siguiente
>
> 1. `wait_for` (arriba). 2. La flakiness de `test_bug046_*`. 3. P-3, el instalador unico.
> 4. **Gate de presentable n.2, ya accionable**: un tercero llegando a `bridge_status` verde
>    con el `QUICKSTART.md` sin preguntarte. Es el unico que depende del calendario de otro.
>
> ## Aviso de metodo
>
> El `.bak` dentro del PBO ya estaba en el buzon desde las 00:43 (`fb-20260821-004303-bde5`,
> otra sesion) y lo re-derive sin verlo. **Leer el buzon antes de investigar** ahorra rodeos.


> # ✅ ADDENDUM 2026-08-21 20:35 — arranque managed REAL: fb-7902 CERRADA; skill v3 entregada
>
> Con la caja libre: lease propio → plantilla `-Mod DayZ_MCP -Mode server` → run
> `7cb19d65-1df6-40cd-93fe-eaaa4d87d3b3` RUNNING con `launch_acknowledged=true`, mision
> cargada SIN errores de modstorage (boot con CF sobre storage CF-flavored, sin rotar),
> **bridge del MCP polleando** (`last_poll_age 0.14 s`, BOUND, instance `5f3bbffc`) y
> stop via `-Kill` → EXITED. Lease liberado con la caja entregada a la sesion en cola
> (lane BRZ root-bone). `fb-...-112112-7902` RESUELTA con esa evidencia;
> `cleanup_degraded=[audit_failed]` del release es el sistematico conocido
> (`fb-20260818-133024-45e8`). **dayz-test-ingame v3 empaquetada del sabor personalizado
> y entregada** (guard + via managed nueva; 6 entradas, 0 junk, 0 placeholders).
> Pendientes que siguen: re-copiar la plantilla a los `_dev\tools` viejos
> (LFPowerGrid_dev, etc.) y `SUB_WRXSTI` en request-policy (`fb-...-a46b`).
>
> ---

> # 🔧 ADDENDUM 2026-08-21 19:15 — via managed de dayz-test.ps1 RECONSTRUIDA (falta solo un boot real)
>
> `Invoke-LifecycleCli` reescrito al contrato vigente (`--daemon-policy normal` +
> `start --request-stdin`; keyfile/puerto los resuelve `load_daemon_policy`), con
> finalizador Python que estampa `new_run_id`/`launch_operation_id`/`launch_request_sha256`
> (JSON canonico sort_keys/compacto) y request por `Start-Process -RedirectStandardInput`
> — **el pipe de string de PS 5.1 mete BOM UTF-8 + CRLF** aunque `$OutputEncoding` sea
> UTF8 sin BOM (medido con sonda hex) y `json.loads` rechaza el BOM; leccion en la
> memoria `ps51-scripts-need-utf8-bom`. Verificado offline: parser 0/0, finalizador
> extraido del artefacto aceptado por `_read_start_request` (CLI) y `_parse_start_request`
> (daemon) con control negativo de sha, y **E2E de plantilla con creds dummy contra el
> daemon vivo muere exactamente en `reason=invalid_identity`** — round trip completo
> menos el arranque. **Pendiente: UN boot managed con lease real cuando la caja quede
> libre → cierra `fb-...-112112-7902`; despues, empaquetar dayz-test-ingame v3** (la
> `.skill` entregada hoy lleva guard pero no esta reescritura). Pack: `c2d289f` +
> `f235b10` (el censo de placeholders no registraba las dos sondas ODOL de hoy; la
> suite de raiz aborta en 6 errores de coleccion ajenos y nadie lo veia). Buzon:
> `fb-...-165044-a514`. Copias por-proyecto de la plantilla (LFPowerGrid_dev, etc.)
> siguen viejas: re-copiar desde la skill.
>
> ---

> # 🌙 ADDENDUM 2026-08-21 18:30 — pack sincronizado, guard anti-store, buzon triado
>
> Pack en `33a2e44` (pusheado): adoptado `check_odol_bone_refs.py` (dayz-vehicles, scrub
> `<you>`), `dayz-pbo-build` alineado con el veredicto (el interruptor es la cabecera de 7
> columnas, no el tamano del addon; `.skill` re-entregado) y `packctl validate` de vuelta a
> **PASS 0 findings** (los 8 que habia eran de los commits de la tarde). `dayz-test-ingame`:
> 108 ficheros de runtime (run WRX de las 16:02 ejecutado in-place desde el store) movidos a
> `~\.claude\skill-store-quarantine\2026-08-21-dayz-test-ingame-runtime\`, y la plantilla
> gana guard anti-skill-store (probado rojo/verde con el fichero real); `.skill` re-entregado
> con el sabor PERSONALIZADO (el de las 17:38 llevaba los placeholders del repo publico).
> **VERIFICADO `fb-...-112112-7902`: la via managed de `dayz-test.ps1` esta muerta en TODAS
> las copias, incluida la plantilla canonica** (template:169-170 manda
> `--keyfile/--port/--request-file`; `lifecycle_cli.py:59-60` solo acepta `--daemon-policy` +
> `start --request-stdin`). Reescribirla exige probar contra el daemon con la caja libre —
> pendiente, arriba de la shortlist. Buzon: triaje vespertino archivado
> (`fb-20260821-162603-7c6f`): **6 cerradas con evidencia** (ValueError→8db6, storage→67b8,
> UI-01→a845, 9552 RESUELTO verificado en arbol), shortlist de 5 para el dueno, y gap de
> paginacion `limit<=100` archivado (`fb-20260821-162529-1c53`). Quedan ~106 abiertas.
>
> ---

> # 🏁 CIERRE 2026-08-21 17:40 — ABIERTO-UI-01 CERRADO DEL TODO: el miembro es la CABECERA DE 7 COLUMNAS
>
> Segunda biseccion volada el mismo dia (mini-escalon solo-CSV, 9 variantes + controles):
> **columnas manda, todo lo demas irrelevante** — 2 filas x 7 col resuelve; 263 filas x 4 col
> cruda. V7 cerrado: el dialecto de `$PBOPREFIX$` es inocuo bajo MakePbo (header + in-game).
> Regla publicada en la skill `dayz-ui-development` v2 (csv del template arreglado a 7 col,
> generador de paneles on-demand en `scripts/`). Demo chat->UI volada (feed simulado);
> `-adminlog` pedido al buzon con diseno de allowlist fail-closed (`fb-...-5977`).
> **Los 17 mods del escalon RETIRADOS de `P:\Mods`** (~921 MB; reproducibles desde
> `C:\codex-ws\lfpg\build\*` — builders + manifests + pbos). Storage rotado dos veces
> (regla: en la mision compartida, o cargas CF o rotas). Detalle:
> `reviews/2026-08-19-ui-reload-layout/VERDICT-stringtable-ladder.md`.
>
> ---

> # V CIERRE 2026-08-21 (noche) - FASE 1 A MEDIAS, SUITE 2018 VERDE. `fc561c4`
>
> Cuatro commits hoy: `cfa353b` (fase 0) -> `f377c9c` (tag S-5) -> `15416f3` (quickstart)
> -> **`fc561c4`**. 252 ficheros publicados, cero rutas privadas, `Ran 2018 - OK`.
>
> ## Lo que cerro de la fase 1
>
> | Fila | Estado |
> |---|---|
> | `QUICKSTART.md` (N-7, N-2) | **hecho**: 44 lineas, un solo camino, recorrido entero en esta maquina |
> | Receta de empaquetado (B-2) | **hecho**: `tools/pack-addon.ps1` + `addon/include.lst` |
> | Rutas de DayZ Tools (P-2) | **hecho**: `dayz_mcp/dayz_tools_paths.py`, cadena env -> registro -> literal |
> | dependency-lock (P-4) | **hecho**: fuera el artefacto que no viaja; el test ya no hace `continue` |
> | Instalador unico (P-3) | **PENDIENTE, y a proposito**: es rediseno, no mecanica. Merece plan propio |
>
> ## ★★ EL HALLAZGO DEL DIA: los .bak viajaban al Workshop
>
> **AddonBuilder no empaqueta `.c` por defecto.** Sin lista de inclusion el PBO sale de
> **571 bytes**. Con `*.c;*.layout` sale de **209 KB**. El que estaba desplegado pesaba
> **453 KB**: la diferencia eran **244 KB de fuente vieja del bridge**, tres `.bak_` que se
> publicaban al Workshop en cada release.
> **La prueba de que Enforce ya los ignoraba**: tras quitarlos la mision compila
> `217x files; 508x classes`, EXACTAMENTE el mismo conteo que con el PBO inflado. Solo el
> PBO los cargaba.
>
> ## Claves de registro MEDIDAS (no deducidas), host WILLY, Win11
>
> ```
> HKCU\Software\Bohemia Interactive\DayZ Tools  path        -> ...\common\DayZ Tools
> HKCU\Software\Valve\Steam                     SteamPath   -> c:/program files (x86)/steam
> HKLM\SOFTWARE\WOW6432Node\Valve\Steam         InstallPath -> C:\Program Files (x86)\Steam
> HKLM\SOFTWARE\Valve\Steam                     InstallPath -> C:\Program Files (x86)\Steam
> ```
> La de Bohemia va PRIMERA: nombra las Tools, no Steam, asi que sobrevive a mover la
> biblioteca. `SteamPath` vuelve en minusculas y con barras normales.
>
> ## Gotchas de AddonBuilder aprendidos ejecutando
>
> - **Sin argumentos abre GUI y no vuelve.** Fuente y destino siempre posicionales.
> - **Escribe progreso a stderr**, y bajo `$ErrorActionPreference='Stop'` PS 5.1 lo
>   convierte en `NativeCommandError` terminante que aborta un build que va bien. Solo
>   decide el exit code.
> - `dayz_test_stop` **vuelve antes** de que el proceso muera: una operacion de fichero
>   sobre el PBO justo despues da "Device or resource busy".
>
> ## Lecciones que me costaron tiempo
>
> - **Restaurar el bundle con `rm -rf` + `cp -r` NO restaura**: el registro pinea el
>   `root_file_id` NTFS y copiar crea ids nuevos. Reconstruir + re-registrar es la unica via.
> - Editar cualquier modulo de `PACKAGED_MODULES` obliga a reconstruir Y re-registrar.
>   Secuencia verificada 2 veces: `build_native_launcher.py --offline --verify-reproducible`,
>   `launcher_registry_update rollback-last` (imprime el sha), `install-dayz-test-v1
>   --expected-sha256 <ese sha>`.
>
> ## Buzon
>
> - `fb-20260821-132319-5034`: `wait_for(log_matches)` falso negativo con el log inundado.
> - `fb-20260821-152733-ce78`: **`test_bug046_startup_deadlock` es FLAKY bajo carga** -
>   rojo en la suite completa, verde 2/2 aislado. Sin diagnosticar. Importa antes de
>   anunciar "clona y corre los tests".
>
> ## Siguiente
>
> Fase 1: el instalador unico (P-3). Y el gate de presentable n.2 -- **un tercero llegando
> a `bridge_status` verde con el QUICKSTART, sin preguntarte** -- ya se puede empezar:
> el documento existe y el camino esta recorrido.


> # ✅ VEREDICTO 2026-08-21 16:30 — el escalon VOLO: familia causal = **forma del corpus del CSV**
>
> Corrige el cierre de las 13:55 de mas abajo: el launcher se recupero (recovery del
> propio check, ejecutada por el vecino a las 15:09) y el escalon volo a la cuarta.
>
> - **Patron valido** (fotograma `capture_20260821_160911_939.jpg`, run `cf3dddd3`):
>   vanilla «Rueda» + V0-V5 resuelven, **V6 cruda**, V7 cruda (NO MEDIBLE). Primera
>   transicion V5->V6 = familia causal **corpus/forma conjunta del CSV** (LADDER.md:62).
> - **Confounds cerrados**: V6 conservaba la columna `spanish` del cliente y su fila
>   con texto (bytes del PBO); los 8 PBOs cargaron a nivel engine (census del crash
>   report del vuelo 1); el layout se aplico (`nodes=20`). Los logs CALLAN sobre
>   stringtable — juez Grok solo-logs + re-verificacion propia: 0 hits.
> - **Entierra las 13 teorias estructurales** y explica las sondas: su CSV minimo era
>   la forma que falla. V5 resuelve con el CSV completo (265 filas x 7 columnas).
> - **Vuelo: 4 ciclos, 3 causas raiz cerradas**: (1) faltaban las base mods de
>   LFPowerGrid — CF + Dabs (`request-policy.json`); (2) `fly_templates.run_is_alive`
>   exigia `RUNNING` cuando `RUNNING_IDLE` es lo NORMAL post-lanzamiento
>   (`process_lifecycle.py:711`) — corregido con discriminador de crash por RPT,
>   probado rojo/verde; (3) storage compartido con modstorage CF -> cascada
>   `Scripted variables corrupted` (diagnostico del usuario), rotado dos veces.
> - **⚠ TRAMPA VIVA del storage compartido** (`dayzOffline.chernarusplus` de
>   DayZServer): en cuanto un server CON CF guarda, un server SIN CF que arranque
>   encima entra en tormenta de VME (el boot de 16:10, `-mod=@DayZ_MCP` a secas,
>   acumulo 15.688 y murio). **Regla: en esa mision, o cargas CF o rotas antes.**
>   Rotado a `storage_1.bak-20260821-1610-cfless-storm`; el boot siguiente (run @SUB_BRZ del usuario, ~17:45, su policy lleva CF) ya regenero un `storage_1` virgen;
>   el proximo boot crea uno virgen.
> - **Pendiente si se quiere el miembro exacto**: mini-escalon DENTRO del CSV (filas
>   2/8/64, columnas 4->7, registros vacios) — no ejecutado. V7/dialecto: A/B aparte.
> - Veredicto completo: `ObsidianVault/AI/10_Projects/DayZ_MCP/reviews/2026-08-19-ui-reload-layout/VERDICT-stringtable-ladder.md`.
>
> ---

> # V CIERRE 2026-08-21 (final) - FASE 0 COMPLETA. SUITE VERDE ENTERA. `f377c9c`
>
> **`Ran 1994 tests - OK (skipped=4)`. Cero rojos**, por primera vez en toda la sesion.
> `origin/main`: `e106cf2` -> `cfa353b` -> **`f377c9c`**.
>
> ## S-5 cerrado CON gate in-game
>
> Tag `[MCP-POC]` -> `[DayZ-MCP]` en los 7 ficheros, **21 sitios**, tres formas de uso
> (literal `.Contains()`, regex con corchetes escapados, y codigo Enforce generado).
> Gate: **run `2b710858-ca97-4772-bd98-79846e4817d9`**, diag 1.29.163709, PBO repackeado.
> `Module: Mission; loaded 217x files; 508x classes`, cero errores. El script.log imprime
> `[DayZ-MCP] config loaded ... poll_hz=5` y **cero `[MCP-POC]`** en sus 7,6 MB ni en el RPT.
> Centinelas re-congelados sobre ESE estado. `poc-verdict.json` y los logs de spike0 conservan
> el tag viejo a proposito: registran corridas que si lo imprimieron.
>
> ## El launcher: RECONSTRUIDO Y RE-ACREDITADO
>
> `check_native_launcher_registry.py` -> **NATIVE LAUNCHER REGISTRY OK**, pe
> `A8F59199...`, 68 transiciones ancladas. Tres builds independientes dieron los mismos
> hashes. Registro respaldado en `scratchpad/_backups_fase0/approved-launchers.json.pre_reregister`.
>
> **Dos cosas que aprendi por las malas, y estan gateadas ahora:**
> 1. `native_bundle._APP_PACKAGED_MODULES` es una copia a mano de `PACKAGED_MODULES` del
>    constructor - deliberada, el verificador viaja dentro de `app.pyz` y no puede importarlo.
>    Nada las ataba: anadir `win32_fileinfo.py` a una sola produjo un bundle que construia
>    limpio y era **rechazado al instalar** con `invalid_native_launcher_bundle`, error que no
>    nombra ninguna de las dos listas. Cerrado con `PackagedModuleListsAgreeTest`.
> 2. **Restaurar el bundle con `rm -rf` + `cp -r` NO restaura**: el registro pinea el
>    `root_file_id` NTFS, y copiar crea ficheros nuevos con id nuevo. Mi "restauracion" de la
>    manana dejo el launcher igual de inacreditado. No habia estado seguro al que volver.
>
> ## HALLAZGO ABIERTO: los .bak viajan DENTRO del PBO
>
> `DayZ_MCP.pbo` son 453 KB de 469 KB de fuente: AddonBuilder empaqueta la carpeta entera,
> **incluidos `MCPBridge.c.bak_pre_fencing_20260819`, `MCPBridge.c_bak_infdrive_20260820` y
> `MCPClientBridge.c.bak_pre_fencing_20260819`** (~220 KB de fuente vieja del bridge). No
> rompen la compilacion - Enforce compila por extension `.c` - pero se distribuyen al
> Workshop. Es la MISMA clase de defecto que la frontera de publicacion tenia esta manana,
> en un tercer sitio. Arreglo: sacar los `.bak` del arbol del addon (son backups del usuario,
> no los muevo yo) o excluirlos en el empaquetado.
>
> ## Buzon
>
> - `fb-20260821-112947-9552` (bug): la lista de modulos empaquetados no era cerrada.
> - `fb-20260821-132319-5034` (finding): **`wait_for(log_matches)` da FALSO NEGATIVO** cuando
>   el script.log se inunda. `lookback_lines` cuenta LINEAS: con 7,6 MB de avisos
>   `OnStoreLoad` de persistencia sucia, 400 lineas no alcanzan el arranque. Me devolvio
>   `satisfied:false` con el patron presente. Lo que funciono: `grep -a` sobre el fichero.

> # ⚠ 2026-08-21 (cierre) — S-5 APLICADO, SIN COMMITEAR, ESPERANDO UNA CORRIDA IN-GAME
>
> **El tag ya está renombrado en los 7 ficheros**: `[MCP-POC]` → `[DayZ-MCP]`, **21 sitios**,
> verificado por conteo en los dos lados (1 en `MCPBridge.c:3490`, y 7/4/3/4/1/1 en `run-poc`,
> `run-fase1/2/3`, `run-s0-gate`, `spike0/mcp-grab-diag`). Los 6 `.ps1` parsean sin errores
> (`PSParser::Tokenize`, 0 en cada uno). Los **3 edits de S-6 apartados** también aplicados,
> en la misma edición del bridge. `poc-verdict.json`, los logs de `_grabdiag` y los `.bak`
> **no se tocaron**: son evidencia de corridas que SÍ imprimieron `[MCP-POC]`.
>
> **Suite: `Ran 1992`, 4 rojos** = los 2 del bundle del launcher (ya conocidos, se saltan en un
> clon) **+ los 2 centinelas del bridge, rojos A PROPÓSITO.** No se re-congelan sin gate in-game:
> el prefijo del log es justo lo que los seis `.ps1` parsean, o sea comportamiento, no cosmética.
>
> ## Qué falta, y está en un comando
>
> Tras cualquier corrida in-game (no merece una propia — que viaje con la siguiente que haya):
>
> ```
> python "plans\s5-close\close_s5.py" --gated "<run id + qué se vio>"
> ```
>
> Lee los hashes nuevos **del propio test** (la única implementación autoritativa del stripping
> de marcadores), re-congela con la nota, y comprueba que queda verde. Luego
> `plans\s5-close\sync_addon.py` + commit + `git push origin master:main`.
>
> **Qué mirar en la corrida**: que el `script.log` del servidor imprima `[DayZ-MCP]` y ninguna
> línea `[MCP-POC]`. Con eso el gate está pasado. Backups pre-S-5 en `plans\s5-close\backups-pre-s5\`.
>
> ⚠ **Requiere repack del PBO**: el bridge cambió, así que la corrida tiene que llevar el
> `@DayZ_MCP` reconstruido, no el que hay desplegado.


> # 📌 NOTA DE GIT 2026-08-21 14:05 — el vecino commiteo y se llevo dentro mi `ui_focus`
>
> Comprobado al cerrar, y **corrige lo que dicen los bloques de mas abajo**:
>
> - **`ui_focus` YA NO esta sin commitear.** El commit `cfa353b` ("Close phase 0: pin the
>   boundaries, publish the bridge, drop the author's paths") barrio mi trabajo dentro junto
>   con el suyo. Verificado en HEAD: `loopback.py` y `server.py` traen `ui_focus`, el README
>   dice 53 tools, `MCPClientBridge.c` lleva +107 lineas, y hasta mi asercion endurecida del
>   gate (`README disagrees with itself`) esta ahi. Era exactamente el enredo que avise: la
>   mezcla estaba DENTRO de mis ficheros, asi que no habia forma de separarlo. Se resolvio
>   solo, pero no como yo habria elegido — mi trabajo va en un commit cuyo mensaje habla de
>   otra cosa.
> - **NO esta publicado.** El remoto `origin` es el repo publico
>   `https://github.com/willy92wins/dayz-mcp.git`, pero la rama local es **`master` SIN
>   upstream** y el remoto solo tiene **`main`**. Publicar NO es un `git push` directo:
>   hay que decidir la relacion master↔main primero. El arbol trackeado esta **limpio**
>   (0 ficheros modificados).
> - **El canario sigue rojo tras ese commit**: `dayz_test_run` con un proyecto inexistente
>   sigue dando `ValueError` pelado en vez de `bad_project`. Commitear no arreglo el launcher.
>
> ---


> # ⛔ CIERRE 2026-08-21 13:55 — escalon de stringtable CONSTRUIDO Y DESPLEGADO, **sin poder volar**
>
> Detalle: `30_Sessions\2026-08-21-DayZ_MCP-escalon-stringtable-construido-y-sin-poder-volar.md`
>
> ## 1. Lo que hay listo y verificado
>
> Ocho variantes `@LADDERMODV0..7` desplegadas en `P:\Mods`, sha256 comprobado en origen **y en el
> fichero desplegado**. Bisecan DESDE `@LFPowerGrid` hacia abajo (D-65), con V0 sustituyendo al mod
> real, no conviviendo con el. Renombrado iso-longitud `LFPowerGrid`(11 B) -> `LADDERMODVn`(11 B).
>
> | V | clave | corte | PBO |
> |---|---|---|---|
> | V0 | `#STR_LADDER_V0_A701` | control completo namespaced | 483.661.525 B |
> | V1 | `#STR_LADDER_V1_B812` | `scripts\` + `class defs` | 480.921.464 B |
> | V2 | `#STR_LADDER_V2_C923` | `data\` completo | 360.462 B |
> | V3 | `#STR_LADDER_V3_D034` | `gui\` + `model.cfg` + `include.lst` | 148.956 B |
> | V4 | `#STR_LADDER_V4_E145` | cuerpo de config | 60.862 B |
> | V5 | `#STR_LADDER_V5_F256` | riqueza `CfgPatches`/`CfgMods` | 58.892 B |
> | V6 | `#STR_LADDER_V6_A367` | corpus + idiomas del csv | 719 B |
> | V7 | `#STR_LADDER_V7_B478` | endpoint minimo — **NO MEDIBLE** | 755 B |
>
> Inventarios: V0 460 entradas (148 `.c`, 296 `data/`), V1 312 (0 `.c`, 296 `data/`), V2 16,
> V3-V7 3; las OCHO con exactamente un `stringtable.csv`. Preflight del vuelo **30/30**.
> Clave de lectura normativa: `C:\codex-ws\lfpg\LADDER.md`.
>
> ## 2. NO VOLO — tres vias, tres cierres, ninguno causado por este trabajo
>
> - **`dayz_test_run` roto para CUALQUIER entrada.** Discriminador: un proyecto **inexistente**
>   devuelve `ValueError` pelado en vez del `bad_project` tipado (`dayz_test_tool.py:72`) -> el
>   fallo es ANTES de seleccionar politica. Daemon FRESCO (12:16:03, posterior al rebuild de
>   12:08:33) y bundle coherente: no es cache, es el refactor del launcher nativo que otra lane
>   tiene SIN COMMITEAR. `fb-20260821-112037-8c7c`.
> - **`dayz-test.ps1` desfasado vs `lifecycle_cli`**: envia `--keyfile --port --request-file`; el
>   CLI exige `--daemon-policy` y recibe por **stdin**. El desfasado es el script — `lifecycle_cli.py`
>   no esta modificado (22-jul). `fb-20260821-112112-7902`.
> - **Arranque manual NO sirve, por diseno.** El juego carga y entra en mision, pero todo verbo
>   MUTANTE se rechaza con `unbound_after_restart`: solo el lifecycle da instance binding.
>   `ui_reload_layout` es mutante. **Sin launcher gestionado NO hay plan B.**
>   `fb-20260821-115024-2af8`.
>
> ## 3. Lo que SI se gano
>
> **El renombrado iso-longitud es tolerado por DayZ** — era incognita declarada del diseno (toca
> bytes dentro de los `.p3d`). Medido in-game: `[L0_PowerGrid] [L0PG_Balance_Native]` inicializa su
> modulo Enforce, World 2387 ficheros / 6243 clases / 14.351 kB (42%), Mission 316 / 691 / 5.405 kB
> (16%). V0 carga y corre: el modo de fallo que habria invalidado el escalon entero esta descartado.
>
> Y dos hechos de entorno: **offline NO da peer de cliente** (registra el de SERVIDOR, cero lineas
> `MCP-CLIENT`), y **`capture_screenshot` SI funciona sin binding** (window grab, no pasa por el puente).
>
> ## 4. Trampas que costaron tiempo hoy
>
> - **MakePbo no puede con este mod**: con `data\` rechaza `.p3d` MLOD (exit 11), sin `data\`
>   rechaza por referencias ausentes (exit 11). Se empaqueta con **FileBank** (pack-only).
> - **FileBank NUNCA lee `$PBOPREFIX$`** (cruce de 4 celdas leyendo los bytes de cabecera): el
>   prefijo sale de `-property`, o de la RUTA de la carpeta si no se pasa. Por eso V7 no mide.
> - **Construir contra OneDrive falla** al limpiar el staging (`PermissionError`, solo-lectura
>   heredado). Usar la copia local `C:\codex-ws\src-lfpg` (hashes de procedencia verificados).
> - **Lanzar DayZ a mano exige entrecomillar**: `DayZ Projects` y `@Dabs Framework` llevan espacios;
>   sin comillas el juego recibe la lista de mods partida y se clava en la pantalla de carga.
> - **La mision de `LFPowerGrid_dev` pide `LFPG_ElecGraph`**, que V0 renombro. Usar la vanilla de
>   `DayZServer\mpmissions`.
>
> ## 5. Como se retoma (una orden)
>
> Canario: un proyecto inexistente debe dar `bad_project` tipado. Cuando lo de, con el python del
> **venv del MCP** (no el del sistema):
> `<venv>\python.exe reviews\2026-08-19-ui-reload-layout\fly_ladder.py`  (`--dry` = solo preflight).
> Nada hay que reconstruir. Los ocho PBOs siguen desplegados.
>
> ---

> # ✅ CIERRE 2026-08-21 (tarde) — FASE 0 CERRADA Y PUBLICADA: `cfa353b`
>
> `origin/main` pasó de `e106cf2` a **`cfa353b`** (74 ficheros, +2680/-10402). Verificado
> **contra `origin/main`, no contra el árbol**: 0 rutas `C:\Users\guill`, 0 `ObsidianVault`,
> 245 ficheros, y el bridge publicado ya trae `DispatchInfectedDrive`.
>
> ## ☑ Lo que cerró
>
> | | Evidencia |
> |---|---|
> | **Los 9 supervivientes de la mutación** | `tools/tests/test_boundary_values_are_pinned.py` (21 tests) + el CLI de `PASS_WITH_WARNINGS` en `test_playbook_runner`. **15 mutaciones re-corridas → 15 cazadas, 0 supervivientes** |
> | **El bridge publicado no tenía `infected_drive`** | 75.794 B publicados vs 78.494 del árbol. La tool estaba registrada en `server.py` y documentada en el README: un clon respondía `unknown_command`. `addon/` sincronizado desde `..\DayZ_MCP\` |
> | **8 rutas del autor, PÚBLICAS** | `task9_build_a_smoke.py` (6) + su test (2), subidas por `e106cf2`. `git rm --cached`: **siguen en disco, fuera del repo**. ⚠ el historial aún las contiene (decisión tomada: no reescribir) |
> | **README** | dos secciones nuevas: 4 mediciones con `fichero:línea` y los límites con su causa de motor. `exec_enforce` fuera del titular (N-5) |
> | **S-6** | **123 de 126** edits en 35 ficheros. Más 3 líneas de `place_safely.toml` que apuntaban al vault y estaban en castellano sin acentos — el barrido de la lane no las cazaba |
> | **Centinelas del bridge** | re-congelados. El motivo del rojo NO era el fencing: `29a8e83` lo cerró el 20-ago 00:18 **y re-congeló el fichero**; el bridge cambió a las 00:35 con `infected_drive`, y la nota del cierre de esa sesión nació desfasada |
>
> Frontera de publicación: **0 private hits** (eran 5), `import check: clean`, 244 ficheros.
>
> ## ⏳ LO QUE QUEDA, y por qué no se hizo
>
> **1. S-5 — el tag `[MCP-POC]` → `[DayZ-MCP]`. Necesita UNA corrida in-game.** Lo imprime
> `MCPBridge.c` y lo grepean 6 `.ps1` (`run-poc` 7, `run-fase3` 4, `run-fase1` 4, `run-fase2` 3,
> `spike0/mcp-grab-diag` 1, `run-s0-gate` 1). Los 7 ficheros se cambian a la vez o ninguno.
> Tocar el bridge rompe el centinela, y su contrato prohíbe re-congelar sin gate in-game
> — cambiar lo que se imprime al log ES comportamiento. **Agrupado con los 3 edits de S-6
> apartados** (`scratchpad\_lanes\s6_apartados.json`), que también tocan `MCPBridge.c`.
> Una edición, una corrida, un re-congelado.
>
> **2. El launcher nativo — operación de seguridad, es tuya.** El arreglo S-2 dejó el bundle
> viejo respecto a sus fuentes (`app_module_drift`), y peor: movió la struct a
> `win32_fileinfo.py`, que **no estaba en `PACKAGED_MODULES`** — un `ModuleNotFoundError`
> esperando dentro de `app.pyz`. Arreglado en el fuente + `PackagedModuleClosureTest`, que
> lee fuente, corre sin bundle y falla si un módulo empaquetado importa uno que no lo está.
> **Reconstruir funciona y es reproducible** (dos builds independientes: `app_pyz 340A8F48…`,
> `pe A8F59199…`) pero cambia el PE, y el registro acreditado pinea el viejo (`438619D3…`):
> con el bundle nuevo sin re-registrar, `dayz_test_run` muere con `invalid_native_launcher_bundle`.
> **Restauré el bundle anterior** — el launcher sigue acreditado. Respaldo en
> `scratchpad\_backups_fase0\bundle_pre_rebuild\`. Secuencia en la hoja de ruta.
>
> ## ☑ SUITE
>
> **`Ran 1992` — 2 rojos, los dos `test_native_launcher_bundle`**, ambos `@requires_built_bundle`,
> o sea que **en un clon se saltan**: dicen exactamente que el bundle local está viejo (punto 2).
>
> ## ⚠ TRES FALLOS MÍOS, medidos
>
> - Dije que el `app_module_drift` era preexistente. **Era mío**, de S-2, y no era cosmético.
> - Convertí `loopback.py` entero de CRLF a LF con un arnés que leía en modo texto y escribía
>   con `newline=""`. Lo cazó la foto de 1808 hashes, no yo; restaurado byte a byte.
> - Programé una corrida de la suite que se solapó con mi propia mutación del README y dio un
>   rojo fantasma.
>
> ## ☑ LANES
>
> README $0,19 / 17 turnos · S-6 $0,71 / 29 turnos. **Cero intrusiones**: foto de 1808 hashes
> antes y después. La lane del README cazó que la hoja de ruta mandaba publicar
> `ticks_in_flight=4741` mientras el verdict del árbol dice **4668**; se publicó el 4668, que
> es el que un tercero puede leer clonando.


> # ✅ CIERRE 2026-08-21 04:00 — `ui_focus` EN SERVICIO (verificado en pixeles) · stringtable PARADO tras 13 hipotesis
>
> Detalle: `30_Sessions\2026-08-21-DayZ_MCP-ui-focus-y-el-stringtable-que-no-cede.md`
>
> (Lane distinta del cierre de las 02:42 que hay debajo — aquel es el council de producto y la
> fase 0. Coincidimos en el arbol: ver el aviso de git en el punto 1.)
>
> ## 1. `ui_focus` — el estado Focus deja de necesitar manos
>
> Verbo nuevo, **53 tools**. Da el foco de teclado a un widget por nombre. Existe porque el
> defecto que las plantillas de UI evitan (relleno blanco del estado Focus en
> `ButtonWidget/EmptyHighlight`) SOLO se observa con el widget enfocado, y eso exigia un humano
> al teclado. `ui_click` no vale: llama `OnClick` directo sin recorrer el camino del raton.
>
> **`result.ok` es un READBACK**: `result.ok = (focused == target)` con `focused = GetFocus()`
> (`enwidgets.c:702`). `found` separa "no existe el widget" de "existe y no se quedo el foco";
> `source` es quien lo tiene DESPUES de la llamada.
>
> **Medido con control en el MISMO fotograma** (dos botones identicos, se enfoca uno):
>
> | fotograma | FocusA | FocusB |
> |---|---|---|
> | sin foco | 0 | 0 |
> | `ui_focus(FocusA)` | **72.857** | **0** |
> | `ui_focus(FocusB)` | **0** | **72.809** |
>
> Y **`GetFocus()` NO es un eco**: al enfocar un TextWidget dijo `PlainLabel` **y FocusB se
> apago**. Un eco no habria apagado B.
>
> ⚠ **CAVEAT**: `enwidgets.c:697` dice que `SetFocus` necesita un widget con inputs. Un
> `TextWidgetClass` SI acepto el foco, asi que la rama `focus_not_taken` **no se dispara hoy y
> esta sin ejercitar**. Candidatos sin probar para dispararla: widget `disabled`, flag NoFocus.
>
> **Implementacion delegada a Grok** (10 turnos, 0,12 $). Cazo un error de mi encargo (le cite
> `DispatchUiClick:1150-1168` y esas lineas son de `DispatchUiSetText`). Le corregi un defecto de
> dialecto: concatenaba dos `bool` en una cadena; el fichero convierte antes con if/else
> (`MCPClientBridge.c:3221-3226`).
>
> **PBO desplegado y verificado**: `453.244 B`, sha `90A733ED95402363`, anclas comprobadas en el
> fichero DESPLEGADO. Docs a 53 tools (README + arquitectura). **268 tests verdes** en los
> modulos afectados; la suite entera NO se corrio (34 clientes MCP vivos, el cuelgue conocido
> aparece a partir de 22 — no se matan).
>
> ⚠ **SIN COMMITEAR NI PUBLICAR.** `DayZ_MCP_dev` **ES un repo git** del repo PUBLICO
> `willy92wins/dayz-mcp` (rama `master`, HEAD `e106cf2`) — la nota del buzon que dice que su
> `.git` esta vacio esta OBSOLETA. Mis cambios viven en `loopback.py` y `server.py` **mezclados
> con trabajo sin commitear de otras lanes** (`daemon_policy.py`, `pinned_keyfile.py`,
> `request_path_authority.py`, `identity_migration.py`, playbooks). Commitear o publicar ahi
> barreria trabajo ajeno. Y `git add -A` mete ~19.700 ficheros (`fb-20260820-220039-9e4c`).
>
> **Y es peor de lo que escribi al cerrar: acotar el `git add` a MIS ficheros tampoco vale.**
> Medido en el diff contra `e106cf2`: `loopback.py` lleva ademas `infected_drive`,
> `_SCHEMALESS_COMMANDS`, la validacion de `exec_enforce` y un tope nuevo de `radius`;
> `server.py` lleva `_bridge_error`, `VEHICLE_CONTROL_MAX_TTL_S`, `_CAPTURE_LIVE_RUN_STATES` y
> `_offset_before_last_lines_in_window`; y el titular del README lo subio la lane de
> `infected_drive`. **La mezcla esta DENTRO de mis ficheros, no en ficheros vecinos**, asi que
> no existe subconjunto de rutas que separe lo mio. Quien commitee tendra que ir por hunks
> (`git add -p`) o esperar a que las otras lanes cierren.
>
> ## 2. `ABIERTO-UI-01` — PARADO, 13 candidatos muertos
>
> **El addon SI se registra**: una clase que solo el declara spawnea por nombre y devuelve
> `object_id`. Su PBO monta y sirve ficheros. Puede llevar `config.bin` como los que funcionan. Y
> su `stringtable.csv` no se lee.
>
> Refutados por medicion, siempre con la clave de vanilla resolviendo en el mismo fotograma:
> `prefix` · `dependencies[]` · `CfgMods` completo con `units[]` · `class defs` con modulos de
> script · caja de la carpeta · `config.bin` vs `config.cpp` · colision de claves · csv de una
> fila · `hideName=0` · bytes/BOM/CRLF dentro del pbo · firma · numero de columnas · llegada al
> `-mod=`.
>
> **No bloquea nada** (las claves de mods reales resuelven). **Si se retoma, cambiar de
> direccion**: bisecar desde `@LFPowerGrid` hacia ABAJO quitandole piezas, no desde el addon
> minimo hacia arriba. Ver D-65.
>
> ## 3. Trampas para el siguiente
>
> - **`ExtractPbo` DERAPIFICA**: convierte `config.bin` en `config.cpp` al extraer. No sirve para
>   comparar en que forma va el config de un pbo — hay que leer los BYTES de la cabecera. Cerre en
>   falso una hipotesis por esto.
> - **El PBO del bridge lleva 237 KB de backups de su propio fuente** (5 ficheros `.bak` del 18-19
>   de agosto, mas de la mitad del pbo, en un mod PUBLICO). Retirado el mio; los cinco previos en
>   `fb-20260821-004303-bde5`.
> - **`capture_screenshot` devuelve la VENTANA (1302x776), no el cliente (1280x720)**, y no dice
>   donde empieza. Toda medida en pixeles nace desplazada 45 px. `fb-20260821-002222-2a24`;
>   mitigacion en `measure_shots4.py`.
> - **Arnes seco**: `dry_registry.py` corre el camino de una sonda con respuestas enlatadas, sin
>   juego. Se escribio tras perder TRES lanzamientos por campos y flags que no lei. Usarlo antes
>   de volar.
> - **Un `assertIn` no detecta una contradiccion.** El gate del conteo de tools comprueba que
>   la formula `N tools (+ ...)` **aparece** en el README. No comprueba que no aparezca otra
>   distinta. Resultado: el README quedo diciendo **52 en el titular y 53 en la seccion a la
>   vez**, en verde (el titular es `52 typed\ntools (+ ...`, con `typed` y un salto en medio,
>   asi que la formula no casa ahi ni por accidente). El numero malo era mio: la lane de
>   `infected_drive` subio el titular 51->52 y yo subi 51->53 solo la seccion. **Corregido**
>   (medido: 54 tools vivas, 53 publicas descartando `ui_dialog`) y el gate endurecido para
>   fijar TODOS los recuentos a la app instanciada, en README y en arquitectura. Probado por
>   mutacion: verde con el bueno, rojo con el titular a 52, README restaurado byte a byte.
>   Ficheros: `README.md`, `tools/tests/test_install_mcp.py` (+`import re`).
>
> ## 4. Desechable — RETIRADO
>
> Los cuatro mods de prueba (`@UITplStrings`, `@UITplDefs`, `@UITplProbe`, `@UITplRap`) **ya no
> estan en `P:\Mods`**: borrados al cerrar (402 -> 398 carpetas, con `@DayZ_MCP` y
> `@LFPowerGrid` verificados intactos despues). Se regeneran enteros con `build_variants.py`,
> `build_defs_mod.py`, `build_probe_mod.py` y `build_rap_mod.py`, conservados en
> `10_Projects\DayZ_MCP\reviews\2026-08-19-ui-reload-layout\`.
>
> ---

> # ✅ CIERRE 2026-08-21 (madrugada) — COUNCIL DE PRODUCTO + FASE 0 ARRANCADA
>
> **Objetivo**: dejar el MCP presentable ante el Discord de modders (público con sesgo
> anti-IA declarado). Council de **7 lanes ciegas** sobre el **clon limpio del repo público**
> `e106cf2`, no sobre el árbol de trabajo.
>
> **Entregables durables**:
> - `reviews/2026-08-21-council-producto-presentable.md` — 58 hallazgos con cita, severidad,
>   coste y lane de origen. 8 bloqueantes.
> - `plans/2026-08-21-hoja-de-ruta-presentacion.md` — 6 fases + gate de presentable.
> - `plans/2026-08-21-purga-fase0-secuencia.md` — N-8 especificado y **sin ejecutar**.
> - Artifact: https://claude.ai/code/artifact/52cbc79a-17bb-4bba-ad2b-e53fda2320b2
> - Buzón: `fb-20260820-232955-f941`.
>
> ## ✅ YA APLICADO AL ÁRBOL (verificado en runtime, no por "el Edit dijo OK")
>
> | Cambio | Evidencia |
> |---|---|
> | **P-1 (BLOQUEANTE)** `MIGRATION_DIR` deja de ser `P:\DayZ_MCP_dev\...` | `identity_migration.py`: nuevo `default_migration_dir(paths)` derivado de `RuntimePaths`. Medido: `C:\Users\guill\AppData\Local\DayZ_MCP\migration\P0S-IDENTITY-V2`. Antes, sin `P:`: `FileNotFoundError [WinError 3]` → daemon no arranca |
> | **S-1 (BLOQUEANTE)** clase muerta borrada | `daemon_policy.py` 15.985 → 13.400 bytes. Verificado en runtime que `AccreditedDaemonPolicy` sigue resolviendo a `daemon_policy_contract` |
> | **S-2 (GRAVE)** struct Win32 unificada | Nuevo `dayz_mcp/win32_fileinfo.py` + `tests/test_win32_fileinfo.py`. `sizeof=24`, `Directory` offset 21, `A is B is S` → True. **El test se probó rojo**: muté yo la struct a `BOOL` → `AssertionError: 32 != 24`; restaurada → verde |
> | S-1b wrapper huérfano | `host_config.py`: `canonical_existing_file` fuera (0 callers) |
> | tautología | `tests/test_client_mode.py`: fuera el `assertEqual(x, x)` |
>
> Regresión: 41 tests (identity_migration + daemon_policy + launcher_registry_update) y 19
> (win32_fileinfo + pinned_keyfile + request_path_authority) en verde. Backups en
> `%TEMP%\...\scratchpad\_backups_fase0\`.
>
> ## ⚠ SIN COMMITEAR NI PUBLICAR — deliberado
>
> Nada de lo anterior está commiteado. El `git push` al repo público se dejó fuera a
> propósito: es el primer paso de la fase 0 y se hace con cabeza fresca, mirando el diff.
>
> ## ⏳ PRÓXIMA ACCIÓN
>
> `plans/2026-08-21-purga-fase0-secuencia.md`, bloque A (7 ficheros, contenido) y luego B.
> **Dos falsos positivos ya cazados en el análisis, no los repitas**: `p0s_gate.py` NO se
> borra (lo llama `install_mcp.py:1089/1106/1179` en `--register`); y `spike0/` es un taller
> vivo en el árbol privado — solo sale de ahí `mcp-grab.ps1`, y dos scripts hermanos lo
> esperan por `$PSScriptRoot`.
>
> **S-5** (`[MCP-POC]` → `[DayZ-MCP]`, `MCPBridge.c:3490`) está bloqueado a propósito hasta
> que el bloque B borre los seis `.ps1` que grepean el tag. Ningún `.py` del runtime lo toca.
>
> ## ☑ MEDICIONES QUE VALEN COMO CREDENCIAL
>
> - **Clon fresco en verde**: venv nuevo, Python 3.14.3, solo las 3 dependencias declaradas
>   → `Ran 1770 tests in 247.252s — OK (skipped=29)`. En máquina que nunca vio el proyecto.
> - **Mutación (Grok, postura C con shell)**: 22 mutaciones en 10 módulos → **13 cazadas,
>   9 supervivientes**. Veredicto: *defendible con matices*. El superviviente caro es
>   `playbooks/runner.py:60`: quitar `PASS_WITH_WARNINGS` del frozenset deja los 24 tests del
>   módulo en verde, y `playbooks/README.md` promete públicamente exit 0 en ese estado.
>   Rehusó extrapolar el 41 % al total (muestra sesgada a cotas) — conservar esa honestidad.
> - **`x == x` real** en `tests/test_client_mode.py:237` (ya borrado).
>
> ## ⚠ CORRECCIÓN A UN HALLAZGO PROPIO
>
> **N-3 era mío, no del repo.** `PublicToolCountDocsTest` (`tests/test_install_mcp.py:1262`)
> ya existe, está verde y es mejor instrumento: instancia la app. Reales: **53 tools vivas**,
> el gate cuenta **52** descartando `ui_dialog` a propósito, README dice **52**. Mis
> "51/52/53" salían de contar decoradores `@app.tool` con regex (cuenta los condicionales)
> sobre un clon un commit por detrás. Rebajado a MENOR en los tres artefactos. Residuo real,
> y es pregunta abierta: `ui_dialog` es tool viva y llamable que el gate **obliga** a ocultar
> del README con `assertNotIn`.
>
> ## ⚠ INFRAESTRUCTURA — dos proveedores caídos y un fallo de sandbox
>
> - **Gemini**: `429 TerminalQuotaError`, free tier, 20 req/día en `gemini-3.5-flash`.
> - **Composer/Cursor**: `You've hit your usage limit`. Además con prompt largo por argv
>   devolvió **exit 0 con 1 byte**: un fallback que compruebe `-s` no lo caza.
> - **Codex**: completó el análisis (581k tokens, 16 hallazgos) pero su **escritura murió**
>   con `windows sandbox: helper_unknown_error: apply deny-read ACLs` en TODAS las rutas,
>   incluida la raíz declarada escribible con `-C ... -s workspace-write`. Recuperado con
>   `codex exec resume --last --skip-git-repo-check "<prompt>"` pidiendo stdout. **El flag va
>   DESPUÉS de `resume`**; `-s` ahí da exit 2, y sin `--skip-git-repo-check` da "Not inside a
>   trusted directory". `gemini` necesita `--skip-trust`; `cursor-agent`, `--trust`.
> - Los `tasks/<id>.output` de los subagentes quedaron a **0 bytes**: los informes de lane de
>   subagente solo sobreviven si se consolidan en el turno.
>
> ## ☑ SESIÓN COMPARTIDA
>
> No se adquirió lease (solo lecturas del buzón, que no lo exigen). El daemon en `:8765`
> (PID 18440) lo levantó esta sesión al leer el buzón y sigue vivo con
> `--idle-timeout 3600`. Ningún proceso DayZ corriendo. Ninguna lane tocó el árbol real: el
> clon de trabajo cerró con 242/242 hashes intactos.

> # ✅ CIERRE 2026-08-20 — `infected_drive`: EL SERVIDOR CONDUCE INFECTADOS (verificado in-game)
>
> Sesión de brainstorm (4 lanes ciegas: Grok, Fable, Qwen local, gemma4 supliendo a Gemini
> sin cuota) que acabó en un hallazgo verificable y verificado. **40 ideas** en
> `scratchpad\brainstorm\BRAINSTORM-40-ideas.md` (temporal; archivar si interesa).
>
> ★★ **HALLAZGO: la IA de criaturas es conducible server-side, y ninguna de las 4 lanes lo
> sabía.** No es teoría: vanilla ya lo hace en `plugindayzinfecteddebug.c:378-400`.
> - `DayZInfected.GetInputController()` → `DayZInfectedInputController` — `3_game/entities/dayzinfected.c:106`
> - `OverrideMovementSpeed` / `OverrideTurnSpeed` / `OverrideHeading` / `OverrideLookAt`
>   — `3_game/entities/dayzcreatureaiinputcontroller.c:3,6,9,17` (son SETTERS)
> - Combo canónico + conversión a radianes — `3_game/entities/dayzanimal.c:548-551`
>
> **Tool nueva `infected_drive(type, pos, heading, speed, mode)`**, promocionada al árbol.
> `heading` en GRADOS (0 = +Z norte, 90 = +X este); el bridge convierte a radianes.
> `mode="release"` devuelve el mando a la IA vanilla.
>
> **Medidas in-game** (run `1a1cb6e1-7230-43c4-9164-41ab3b0be936`, posición autoritativa vía
> `entities_query`):
> | Fase | Impuesto | Rumbo real | Error | Evidencia |
> |---|---|---|---|---|
> | 1 | heading 90° | **92,1°** | 2,1° | 76,6 m, **huyendo del jugador al que atacaba a 0,95 m** |
> | 2 | heading 270° | **270,1°** | 0,1° | 41,7 m en 46 s; desviación lateral 0,02 m |
> | 3 | `release` | — | — | deja de obedecer, gira a 171,7° solo, baja a 0,25 m/s |
>
> La fase 1 mata la hipótesis nula (lo apartó de atacar); la fase 2 mata la casualidad
> (invierte la marcha y traza una recta con 2 cm de desviación en 42 m).
>
> ★ **Dos hallazgos de diseño**:
> 1. **El override PERSISTE.** El plugin vanilla lo reaplica cada tick, así que se asumió
>    held-state + deadman en `OnUpdate`. **No hace falta**: una llamada y sigue caminando
>    minutos. El handler se quedó en 10 líneas sin tocar el bucle del bridge.
> 2. **`speed` NO es m/s.** Con `speed=3` se midió 0,91 m/s. Escala sin calibrar.
>
> **Ficheros tocados** (backups `*_bak_infdrive_20260820` al lado de cada uno):
> `MCPMessages.c` (campos heading/speed) · `MCPBridge.c` (dispatch `:496`, handler `:1238`) ·
> `loopback.py` (SERVER_COMMANDS + validación) · `server.py` (tool tipada) ·
> `README.md` + `dayz-mcp-architecture.md` (conteo 51 → 52 tools).
>
> **Suite: `Ran 1934`, 4 fallos.** 2 son los centinelas de `test_task9_spawn_phase_markers`
> (hash congelado de `MCPBridge.c`, rojos por diseño hasta cerrar el fencing — mi cambio los
> mantiene rojos con hash nuevo). Los otros 2 eran del gate `PublicToolCountDocsTest`, que
> cazó correctamente la tool sin documentar: **corregidos y en verde**.
>
> ✅ **`(a-bis)` del cierre anterior RESUELTO de paso**: el daemon murió por idle y se
> relanzó solo durante la sesión (`daemon_generation` `0624b595`, `daemon_modules.stale: []`),
> así que BUG-105 entró en servicio sin intervención.
>
> ⚠ **SIN PROBAR, y no dar por bueno**:
> - `AIWorld.FindPath` / `RaycastNavMesh` / `SampleNavmeshPosition` + `PGFilter.SetCost`
>   (`3_game/ai/aiworld.c:98,110,122,67`) — seguir waypoints en vez de rumbo fijo.
> - `new PGFilter()`: la clase no declara constructor privado (a diferencia de `AIWorld`
>   en `aiworld.c:72`), debería instanciarse, **pero no se ha probado**.
> - Vía survivors: `HumanInputController.OverrideMovementSpeed/MovementAngle/AimChangeX/Y`
>   (`3_game/human.c:234-243`) + la **FSM de bots vanilla en `4_world/systems/bot/`**.
>   Ojo: `HumanCommandMove` (`human.c:433`) es solo getters — la vía es el input controller,
>   no el command. Esto decide si "población sintética de survivors" es viable.
> - Calibrar la escala de `speed`.
>
> ⚠ **Cierre degradado**: `session_release` devolvió `cleanup_degraded: ["audit_failed"]` en
> las **dos** liberaciones de la sesión. No rompió nada observable, pero se repite.
>
> ✅ **DESBLOQUEO para la sesión del lane nocturno**: dejé de tocar `MCPMessages.c` y
> `MCPBridge.c`. El **D25** quedó sin promocionar por evitar colisión conmigo (ver su nota
> más abajo); ya se puede promocionar. Mis 2 rojos de `test_task9_spawn_phase_markers` son
> los que esa sesión identificó como ajenos: siguen rojos, ahora con hash nuevo.
> Nota: su cierre avisa de que `unittest discover` **se colgó** al final de su noche; **en mi
> corrida terminó** (`Ran 1934` en 229 s con `.venv-mcp`), así que el cuelgue no es constante.
>
> Buzón: `fb-20260819-230027-3d90` (tool_contribution, con todas las cifras y `path:line`).

> # ✅ CIERRE 2026-08-20 (noche) — LOTE PYTHON + DOCS DEL TRIAJE, CERRADOS Y PROMOCIONADOS
>
> Noche de delegación en el **modelo local (Qwen, coste 0 €)** con revisión y promoción por
> mi parte. **14 de los 62 defectos triados cerrados**, todos verificados contra el árbol
> vivo. Estado completo del lane, con las cifras y los repros:
> `%TEMP%\mcp-qwen-20260820\ESTADO.md`.
>
> ## Lo que entró en producción
>
> | id | sev | qué |
> |---|---|---|
> | **D05** | MAYOR | `capture_screenshot` apunta a la ventana **del run activo**: deriva el hermano `_client` como `cmdline_match`. Antes fotografiaba "la ventana DayZ más grande" y podía certificar otro mundo. |
> | **D09/D10** | MAYOR | el `object_id` que el puente sí manda en un timeout de spawn **sobrevive al error**, así que el agente puede borrar en vez de duplicar. Propagado a los **4** call-sites de resultado de puente. |
> | **D20** | MAYOR | `vehicle_control` rechaza `hold_ttl_s > 30`. El puente caía al default de **3 s** y respondía `ok`: pedir 45 s daba un falso "este coche no se mueve". |
> | D18 | MENOR | `validate_command_args` ya no aprueba por defecto lo que no reconoce; `exec_enforce` tiene rama propia. **Con test que falla nombrando el verbo** si alguien añade uno sin validación. |
> | D29 | MENOR | techo de 200 en `action_use.radius`, en las dos capas, igual que su hermana `entities_query`. |
> | D40 | MENOR | `_marker_rewound` lee solo la ventana de cola en vez del RPT entero. El arreglo lo remató yo tras tres rondas: lo difícil era la aritmética del borde, no la idea. Test que **cuenta los bytes leídos**, no solo el offset. |
> | D38, D39 | MENOR | `logs_since` reporta `errors[]` cuando un log no se puede leer, y el cursor implícito es **por run** (antes uno solo por proceso). |
> | D54, D55, D36, D50, D49, D24, D31, D42 | docs | ver abajo. |
>
> ## La pasada de documentación: revalidar primero salvó texto correcto
>
> **D37, D28 y D43 ya estaban arreglados** — corregirlos a ciegas habría estropeado lo que
> ya era verdad. De los vivos:
>
> - **D55** — `dayz-mcp-architecture.md:9` decía *"aún NO implementado"* de un sistema en
>   producción, y es el documento que el CLAUDE.md marca como punto de entrada. El spec decía
>   "11 tools" y daba el bump 7→8 por aplazado, con `MCPMessages.c:1` y `core.py:17` los dos
>   en `"8"`.
> - **D54 + gate** — el README listaba 52 tools y el código registra **53**: faltaba
>   `ui_dialog`. Medido construyendo la app (`list_tools()`), no con regex: hay tools que se
>   registran con `add_tool`. **Test nuevo** compara ambas listas en los dos sentidos.
> - **D50** — no eran dos cifras en conflicto sino **dos relojes distintos**: el techo
>   host-side de una petición MCP (7 días) y el auto-apagado del daemon (1800 s).
> - **D36** — el README prometía `version_blocked`; el código devuelve `game_not_ready:reason=…`.
> - **D49** — el instalador fuerza `py -3.14` mientras `pyproject.toml` declara `>=3.10`:
>   quien tenga 3.10-3.13 no puede instalar. Documentado; **la política la decides tú**.
>
> ## Lo que NO se hizo, y por qué (esto importa más que la lista de arriba)
>
> - **D11 — no es un parche, es protocolo.** El no-op de `abandon_bridge` en modo cliente
>   está documentado, pero **el comentario se apoya en mecanismos que no corren en ese
>   camino**: `COMMAND_TTL_S` y el reaping viven en `loopback.py`, que en modo cliente
>   ejecuta el **daemon**. El barrido salta desde `record_poll` (`loopback.py:1586`), o sea en
>   cada poll del juego → **el comando sigue siendo ejecutable hasta 30 s después de
>   encolarse** aunque el agente ya haya recibido su timeout; e indefinidamente si el juego
>   deja de sondear. Cerrarlo pide una ruta `POST /abandon`. Archivado: `fb-20260820-004708-232b`.
> - **D13** — no existe señal de "esta sesión tocó la cámara" análoga a `vehicle_active`, así
>   que encolar `restore_gameplay` al expirar el lease exigiría inventar una condición.
> - **D27** — el triaje decía que "el spec eligió bullet" para `scene_raycast`;
>   `product-spec.md` **no menciona ni `rvproxy` ni `bullet`**. Sin esa fuente, cambiar el
>   default de una tool sería inventarse una decisión de producto.
> - **D25** — el arreglo es un comentario en `MCPMessages.c`, listo y verificado como
>   solo-comentario en `out/05b-docs.md`. **Sin promocionar**: ese addon lo está editando otra
>   sesión (`29fbfda6-08d`, `infected_drive`) y un comentario no justifica la colisión.
> *(D40 empezó aquí y acabó cerrado — ver arriba.)*
>
> ## ⚠ El gate del proyecto dejó de dar veredicto al final de la noche
>
> `unittest discover` **se cuelga** en
> `test_bug046_startup_deadlock…test_real_run_daemon_crash_boundaries_recover_in_second_wave`
> cuando la caja acumula clientes MCP vivos (medido: **22 clientes, 0 daemons**). La misma
> suite pasó limpia cinco veces antes esa noche. Ese test lanza daemons reales y espera una
> elección que no converge compitiendo con ellos. **No maté los 22 clientes** (protocolo de
> sesión compartida). La verificación final se hizo con corrida acotada: `Ran 111, OK`.
> Archivado: `fb-20260820-004633-d541`. Un gate que no sabe terminar no se distingue de uno
> que no sabe fallar.
>
> ## ▶ SEGUNDA TANDA (mañana del 20-08): invariantes sin gate + triaje del buzón
>
> ### Dos agujeros de autoridad, encontrados por mutación y cerrados
>
> El método: el modelo local localiza una regla escrita en un comentario y propone **la mutación
> mínima** que la rompería; yo la ejecuto contra la suite y miro si algo se pone rojo. Una
> mutación que deja la suite verde **es** la definición de invariante sin gate.
>
> De 10 mutaciones sobre `loopback.py` + `session_coordination.py`: 3 cazadas, 4 no aplicables,
> **3 supervivientes**. Dos eran reales y están arregladas:
>
> - **`claim_dispatch`** aceptaba cualquier `command_id` del lease activo, aunque ese comando
>   nunca se hubiera comprometido bajo ese id.
> - **`commit_authorization`** no comparaba el `lease_id`: una sesión podía confirmar una
>   reserva emitida a otro lease.
>
> Pista de por qué pasaron desapercibidas: **`claim_dispatch` aparece en la suite solo como punto
> de inyección** (`coordinator.claim_dispatch = paused`, `test_task7_review_regressions.py:582`),
> nunca como sujeto de una aserción. Una función que solo se mockea es una función cuyo
> comportamiento nadie comprueba. Gate nuevo: `tests/test_authority_invariants_are_gated.py`
> (6 tests), con control negativo verificado.
>
> La tercera era **falso positivo, y distinguirlo exigió medir**: sin bindings, una mutación se
> rechaza ya en el ingreso (`409 legacy_unbound`), así que esa guarda es segunda capa de un
> fail-closed sobre un estado hoy inalcanzable. Lleva un comentario para que nadie la borre como
> código muerto.
>
> **Pendiente y listo para seguir**: 10 reglas más ya localizadas en `orphan_guard.py` +
> `process_lifecycle.py`, **7 de daño ALTO**, todas sobre no matar el proceso equivocado
> (reclamar por salud y no por parentesco, leer un acceso ambiguo como vivo, un 401 como vivo,
> anti-TOCTOU). Falta pasarlas por el bucle de mutaciones — es el siguiente trabajo natural.
>
> ### Triaje del buzón: 63 entradas dictaminadas, 14 cerradas
>
> De 102 abiertas, 63 son de DayZ_MCP. Todas triadas contra el árbol:
> **19 NO_VERIFICABLE · 15 VIVO · 15 RESUELTO · 14 INFORMATIVA**.
> Mapa por entrada: `%TEMP%\mcp-qwen-20260820	riage\MAPA.md`. Resumen archivado como
> `fb-20260820-100655-d94e`.
>
> Dos cosas que salieron del propio triaje:
> - **BUG-105 estaba reportado tres veces** desde síntomas distintos. El buzón acumula duplicados
>   que nadie reconcilia, así que "cuánto queda" exagera.
> - **`fb-20260818-131800-eee9` NO se cerró**: agrega tres incidencias y solo una está hecha. Las
>   otras dos son la misma tensión — `lookback_lines = 200` arregla el timeout *causando* el falso
>   positivo. No se cierra con un default, se cierra con otra primitiva (`fb-20260820-095528-a564`).
>
> **Verificar antes de cerrar cambió el resultado en 2 de 15.** Uno cerraba tres incidencias
> citando la evidencia de una; otro citaba el HANDOFF en vez del código, y al abrir el layout la
> explicación era inexacta (la solución no fue `ColorablePanel` sino un style propio).
>
> ### ⚠ Corrección de la tanda de noche
>
> **D54 estaba mal cerrado y ya está revertido.** Añadí `ui_dialog` al README y subí el conteo a
> 53; existía un test (`test_install_mcp.py:1298`) que **exige que `ui_dialog` NO aparezca** y
> descarta esa tool del conteo a propósito. La omisión era deliberada: el triaje se equivocó y yo
> "arreglé" una decisión consciente. También rompí `test_task9_launcher_migration` al nombrar
> `install-mcp.ps1` en la documentación, que ese test prohíbe. Los dos corregidos y verificados.
>
> **Causa raíz, que vale para la próxima**: cuando la suite completa empezó a colgarse verifiqué
> con un subconjunto elegido a ojo, y los dos tests que rompí no estaban en él. El gate bueno es
> la corrida ancha de 117 módulos saltando solo el que se cuelga: **129 s, `Ran 1928`**. La lista
> está en `%TEMP%\mcp-qwen-20260820\mods.txt`.
>
> ## ▶ TERCERA TANDA + COSTE (cierre 12:50)
>
> - **`is_pid_alive` fail-closed, cerrado.** Su docstring dice la regla —*indeterminate access errors
>   read as alive … never kills on ambiguity*— y nada la comprobaba: invertirla dejaba la suite verde.
>   Un pid que no se puede abrir por permisos no es prueba de muerte. Gate:
>   `tests/test_liveness_is_fail_closed.py`, 6 tests, control negativo verificado.
> - **`telemetry_read` ya dice qué argumento falla** (`bad_mode`/`bad_type`/`bad_radius`), con un test
>   que exige que los tres sean distinguibles entre sí.
> - **`doctor` NO se tocó, y esa es la conclusión.** Iba a añadirle detalle al error y apareció
>   `test_doctor.py:1370-1384`, que exige el payload exacto y comprueba
>   `assertNotIn("sensitive", …)`: **la opacidad es deliberada por seguridad**. Ficha cerrada como
>   "no procede", con la mitad barata (ejemplo en `--help`) declarada como pendiente aparte.
> - **Pendiente medido**: `release_all_running_owners` (`process_lifecycle.py:727`) sobrevive a su
>   mutación y no tiene test — `fb-20260820-110945-8625`. Y 4 mutaciones del ciclo de vida quedaron
>   **sin medir** (la línea propuesta no era única): eso no es "defendidas".
>
> ### Coste de la jornada, medido
>
> **$411,36** de orquestación (Opus 5) contra **0 €** de generación (Qwen local). Reparto:
> **73% cache read** ($301,60), 21% cache write ($85,58), **6% output** ($24,16).
> 1.283 llamadas, 470k tokens de contexto releídos de media, $0,32 por llamada.
> De 651 llamadas a herramientas, **268 (41%) fueron esperas y sondeos** (~$85).
>
> **Lo que escribí costó $24; los otros $387 son el precio de tener contexto vivo 12 h.** Delegar en
> el local ahorró la parte barata. Para abaratar de verdad: bloquear una vez en lugar de sondear
> diez, no repetir suites completas, y trocear sesiones largas.
>
> Handoff completo: `AI\30_Sessions\2026-08-20-DayZ_MCP-lane-nocturno-qwen-triaje-e-invariantes.md`
>
> ## Línea base para la próxima sesión
>
> Árbol vivo antes de la noche: `Ran 1944, failures=2`. Los **2 rojos son ajenos**
> (`test_task9_spawn_phase_markers`, hash congelado del bridge) y los provoca la sesión
> `29fbfda6-08d` al tocar `MCPBridge.c` para `infected_drive`. Tras la noche hay **6 tests
> nuevos**. Toda promoción se hizo con guarda de hash previa: habría abortado entera si esa
> sesión hubiera tocado los mismos ficheros.

---

<details><summary>Cierre anterior (2026-08-20, dia)</summary>

> # ✅ CIERRE 2026-08-20 — TODO CERRADO Y PUBLICADO. Esto MANDA sobre lo de abajo.
>
> **El gate del fencing (D-55.9) esta CERRADO.** El arbol esta **verde entero: `Ran 1760, OK`,
> cero rojos** — el centinela `test_task9_spawn_phase_markers` ya NO es un recordatorio, esta
> re-congelado a los valores reales porque el gate paso. Publicado en
> `willy92wins/dayz-mcp` como **`29a8e83`** (67 ficheros, +8704/-232, suite del clon
> `Ran 1597 OK`, **0 private hits** en las dos pasadas del escaner).
> Daemon en generacion `3b80f101`, `daemon_modules.stale: []`: **todo en servicio**.
>
> ## Lo que se cerro hoy
> | | |
> |---|---|
> | **Gate D-55.9** | sonda sintetica; `BOUND -> AMBIGUOUS`, mutacion **409 `instance_ambiguous`**, medido EN VIVO |
> | **BUG-105** | el fencing rechazaba TODO lanzamiento; verificado en produccion quitando el apaño |
> | **Agujeros 1, 2 y 3** | tombstones / persist skip / reaper despierta al coordinador |
> | **BUG-104** | el reaper ya corre bajo cuarentena, con traza de audit |
> | **Frontera de publicacion** | colaba 27 ficheros `.bak_*` al repo PUBLICO |
>
> ## ★★ EL CANARIO VIEJO ERA INEJECUTABLE — no lo reintentes
> `canary_fence.py` lanzaba un segundo DayZ con el mismo `instance`. **Dos clientes comparten
> Steam ID**: no pueden estar conectados a la vez y el segundo **desconecta al primero**. Nunca
> hay dos PIDs vivos sobre una instancia, que es literalmente la condicion de `AMBIGUOUS`.
> **El sustituto es `tools\checks\fence_canary_probe.py`** (ya en el arbol y publicado). Basta
> UN poll con el `inst=` registrado desde otro proceso: el fence se decide en el daemon y
> `resolve_poll_pid` no comprueba que quien sondea sea DayZ; el volcado es
> `source_pid != binding.pid` (`loopback.py`). Invocacion:
> ```
> .\.venv-mcp\Scripts\python.exe checks\fence_canary_probe.py --client-profiles <dir> --port 8765
> ```
> Exit 0=PASS, 1=FAIL, **2=UNMEASURABLE**. Necesita un `dayz_test_run` con el cliente dentro.
> ★ `AMBIGUOUS` **se cura sola** a `BOUND` cuando el unico PID vivo vuelve a ser el legitimo
> (`loopback.py:1765-1774`): por eso la sonda es repetible y no deja la caja degradada. Ojo,
> eso INVALIDA el discriminador ingenuo "la segunda pasada deberia salir UNMEASURABLE" — el
> bueno es disparar con un `instance` NO registrado, que da FAIL.
>
> ## Lo que queda, por orden
> 1. **La pasada de documentacion**: 14 de los 62 defectos triados caen de una vez. Es lo que
>    mejor relacion trabajo/cierres tiene en el buzon.
> 2. **Reconciliar los dos `decision-log.md`** (arbol vs vault, ~808 lineas de deriva). Es
>    fusion con criterio y toca contenido de otras sesiones; no es mecanico.
> 3. **Refinar la sonda** (menor): con un `instance` no registrado devuelve `FAIL`, y sera mas
>    honesto `UNMEASURABLE` — el tratamiento no se pudo aplicar. No bloquea nada.
>
> ## Trampas que costaron tiempo HOY, para no repetirlas
> - **Antes de instrumentar codigo de produccion, abre el log que el sistema ya escribe.**
>   Declare `worker_failed` "indiagnosticable" y el audit trail lo nombraba desde el primer
>   intento (`instance_config_missing`, 7 eventos).
> - **El revisor muta el CABLEADO, no el cuerpo.** Las 6 mutaciones del lane murieron y aun
>   quedaban 2 ramas sin test; una anclaba el arreglo al DOBLE de test en vez de a produccion.
> - **Un gate tiene que saber fallar.** El canario viejo leia `status["peers"]`, que no existe:
>   su medida decisiva era `None` por construccion. Exige control negativo SIEMPRE.
> - **Pon la cifra exacta de la linea base en todo brief de lane.** Ese gate hizo parar al lane
>   ante un rojo que era mio (workspace refrescado a medias: `dayz-mcp-architecture.md` viejo).
> - **Un refresco parcial de workspace se sincroniza por lo que los tests LEEN**, no por lo que
>   crees que es el codigo: hay tests que leen ficheros de FUERA de `tools\`.
> - **La copia en `%TEMP%` NO aisla sola**: el venv copiado trae un finder editable que apunta
>   al arbol REAL. Repuntar el `MAPPING` y comprobarlo desde un cwd neutro.
> - **`reviews/` en el repo publico**: nunca ha estado trackeado y no esta en la frontera. Un
>   `git add -A` a secas lo publica. Desestacalo.

> ## ▶ PLAN, POR PRIORIDAD (escrito 2026-08-19 al cierre)
>
> **1. CERRAR EL FENCING. Es lo unico que esta a medias en produccion.** Esta en servicio y sin
> gate desde hoy (D-60), y ese estado no debe durar. Tres pasos, en orden:
> - **(a) ~~Hacer hablar a `worker_failed`~~ — RESUELTO 2026-08-19, y la premisa era FALSA.**
>   `worker_failed` NO nace en `server.py:2205` (ahi nace `dayz_test_failed:<Tipo>`): se acuña en el
>   worker sellado y viaja como campo del JSON terminal. Y no hacia falta instrumentar nada: el audit
>   trail (`%LOCALAPPDATA%\DayZ_MCP\audit\events.jsonl`) lo decia por su nombre desde el primer
>   intento — **`instance_config_missing`**, o sea **mi propio fencing** rechazando el lanzamiento.
>   Ver **BUG-105**: arreglado en el arbol con 7 tests y 6 mutaciones sin supervivientes, suite
>   `Ran 1702` (2 rojos = centinelas). ★ **Leccion**: antes de instrumentar codigo de produccion,
>   abrir el log que ya existe.
> - **(a-bis) PENDIENTE: reiniciar el daemon** para que el arreglo entre en servicio. El desbloqueo
>   de hoy fue escribir a mano el config que faltaba, asi que MI proyecto ya lanza, pero cualquier
>   otro dev_root sin ese fichero sigue roto hasta el reinicio. La caja estaba ocupada por otra
>   sesion (lease `70a941b2`, sesion `911d5b74-887`), por eso no se reinicio.
> - **(b) El canario — INTENTADO 19-08 noche, veredicto INCONCLUSO (NO es un PASS).**
>   Lo que SI quedo medido: `dayz_test_run` → `succeeded` en 12,9 s, **primer arranque desde las
>   16:50Z**, y el fencing acuño instancia para los DOS roles (`d5f8a7ba` servidor,
>   `4926f46e` cliente) — confirmacion in-vivo de BUG-105 con el mismo daemon y el mismo
>   codigo, cambiando solo el config que faltaba.
>   ★★ **Lo que lo tumbo fue el propio canario, no el fencing.** Cuatro defectos, archivados
>   en `fb-20260819-203819-48e4` y **ya arreglados** en `canario\canary_fence.py`:
>   (1) GRAVE — `fence_view()` leia `status["peers"]`, que `/status` **no devuelve** (trae
>   `client_peer`), asi que `client_binding_state` era **None por construccion** y el guion
>   **nunca podia ver AMBIGUOUS**, que es justo el desenlace que el gate espera;
>   (2) no exigia `-mod` al intruso — sin el no carga el puente, no sondea, no presenta el
>   `inst=` duplicado, y el `camera_set` aterriza limpio: **parece un PASS sin haber medido
>   nada**. Casi lo canto. Lo delataron `instance_ambiguous` en 0 y
>   `unaccredited_polls_by_class` sin moverse un solo poll;
>   (3) su `taskkill` por PID no fia en DayZDiag (launcher-pid != window-pid);
>   (4) `--port`/`--key` obligatorios pudiendo autodescubrirse.
>   El guard de (2) esta **probado disparandolo**. Evidencia de la pasada vacua en
>   `canario\evidencia-VACUA-sin-mod-20260819\` (no borrada, es el registro del casi-falso-pase).
>   ⚠ **El segundo intento murio por entorno**: la otra sesion cogio el lease, levanto su
>   pareja en el 2402 (`ForzaDayZ\navprobe_run`) y su "Restart pair" barrio la caja entera,
>   mi run incluido. **Reintentar solo con la caja EN EXCLUSIVA.**
> ★★ **19-08 noche — CAUSA RAIZ DEL BLOQUEO, aportada por el usuario: el canario es
> INEJECUTABLE POR DISEÑO en esta maquina.** Dos clientes DayZ **comparten el Steam ID**, asi
> que no pueden estar conectados a la vez — y al conectar el segundo, **el primero se
> desconecta**. No es un relevo simultaneo: es un relevo EXCLUYENTE.
> Consecuencia exacta: el puente de cliente vive en `5_Mission` y necesita estar EN mision para
> sondear; con el legitimo dentro el intruso no entra, y si entra echa al legitimo. En ningun
> instante hay **dos PIDs vivos presentando el mismo `instance`**, que es literalmente la
> condicion de `AMBIGUOUS`. El desenlace que el gate espera **no puede darse por esta via**.
> ⚠ Esto supera mi hipotesis previa ("al intruso le falto tiempo de carga"): no le faltaba
> tiempo, no podia entrar nunca. Y probablemente cada intento **expulso a mi propio cliente**.
> **Gate DIFERIDO por indicacion del usuario.** Veredicto INCONCLUSO, nunca PASS.
> Vias de rediseno, sin probar (`fb-20260819-204959-e4cf`), la 3 es la que mas cubre por menos:
> (1) que el intruso no conecte — antes hay que ver DONDE arranca el polling del cliente;
> (2) usar el rol SERVIDOR como intruso, que no compite por Steam ID;
> (3) **sonda sintetica**: un proceso que hable HTTP al daemon con el `inst=` copiado, sin DayZ
> de por medio — el fence se decide en el daemon (`resolve_poll_pid` + bindings), no en el juego,
> asi que dos PIDs simultaneos con el mismo instance son triviales de montar;
> (4) segunda cuenta de Steam o segunda maquina (caro).
> - **(b-original) El canario** (`canario\canary_fence.py`, 3 fases + 5 `camera_set` en `lookat`).
>   Criterio: `wrong_target_canary_count == 0`. Necesita la caja libre.
> - **(c) Re-congelar `test_task9_spawn_phase_markers`** (las dos mitades) sobre el
>   `MCPBridge.c` ya gateado. **NUNCA antes del canario**: su rojo es el unico recordatorio
>   mecanico de que el fencing esta en produccion sin verificar.
>
> **2. ~~AGUJEROS 2 Y 3 DEL COORDINADOR~~ — HECHOS Y PROMOCIONADOS (19-08 noche).**
> Lane Grok postura B + 2 rondas; revision del ORQ con mutacion propia del CABLEADO (2
> ramas sin test que su bateria no vio, ambas arregladas). Suite en el arbol **1724, 2
> rojos = centinelas**. `process_lifecycle.py` entro **MERGEADO** (3 lineas) porque otra
> sesion tenia encima `_last_start_error` y un `never_started`. Entrega y logs en
> `%TEMP%\mcp-laneCOORD-20260819\`; backup pre-promocion en `PRE-PROMOTION-BACKUP\`.
> ⚠ Como el fencing, esta en el arbol pero **NO en servicio**: necesita reinicio de daemon.
> Detalle del arbitraje en `council-scorecard.md` corrida 52.
>
> **2-bis. (texto original del plan, ya cumplido)** El spec exigia "fencing promocionado"
> como paso 0 y hoy se cumple, verificado en el arbol: `_retire_run_bindings` presente (agujero 3
> se engancha al lado), `instance_fence`/`_bound_queues` y `_persist_coordination` en
> `loopback.py` (agujero 2 se escribe contra el fichero real), y `session_coordination.py` sin
> fencing (sin choque). Spec completo en
> `10_Projects\DayZ_MCP\reviews\2026-08-19-triaje-nocturno\spec-coordinador.md`, con **9 tests
> que hoy salen rojos**. Orden del propio spec: **2 (persist skip) → 3 (reaper wake)**. Es trabajo
> puramente offline y con contrato escrito: candidato ideal para lane + revision ciega, en
> paralelo con el punto 1.
>
> **3. ~~BUG-104~~ — CERRADO Y EN SERVICIO (19-08 23:05).** Lane Grok + mi revision; el reaper
> ya corre bajo cuarentena y audita el paso. Lo que impide la reincidencia es un test **AST**
> (`test_reap_dead_runs_locked_cannot_reach_terminate`) del que **verifique que dispara**, plantando
> un `terminate` en el camino del reap. 9 mutaciones, 0 supervivientes. Suite **1734, 2 rojos**.
> Detalle en el ledger y en `council-scorecard.md` corrida 53.
>
> **3-bis. (texto original del plan, ya cumplido)** `reap_dead_runs` sale con `[]` bajo
> cuarentena antes de mirar un solo run, y la cuarentena esta activa siempre que haya un DayZ
> RETAIL vivo. La reja es mas ancha de lo necesario: el reaper **no mata nada**, solo retira runs
> con cero procesos propios. Arreglo: dejarlo correr bajo cuarentena por ser demostrablemente sin
> efectos sobre procesos, o como minimo registrar el salto para que el fantasma sea diagnosticable.
> Con test.
>
> **4. La pasada de documentacion**: 14 de los 62 defectos triados caen con un solo repaso. La
> mejor relacion trabajo/cierres que queda en el buzon.
>
> **Fuera de la cola por ahora**: reconciliar los dos `decision-log.md` (808 lineas de deriva,
> `fb-20260819-181535-c09a`) — es fusion con criterio y toca contenido de otras sesiones.

> ## ⚠ ESTADO 19:00 — EL FENCING ESTA VIVO Y DESPLEGADO. FALTA EL CANARIO.
>
> **Ya no hay trampa armada** (el aviso anterior, de las 18:40, decia que el daemon no lo habia
> cargado — ya lo ha cargado). Lo que hay ahora es un fencing **en servicio pero SIN GATEAR**.
>
> Hecho y verificado por conducta, no por "reinicio y ya":
> - **Arbol**: `delta.diff` aplicado (34 ficheros, 0 hunks fallidos) + los **3 ficheros nuevos**
>   que un diff con cabecera `a/` no puede crear (`instance_fence.py`, `fence_helpers.py`,
>   `test_instance_fence.py`; sin ellos el arbol NO IMPORTA — `fb-20260819-163133-3ccf`).
> - **Suite en el arbol**: `Ran 1695`, 2 fallos, que son los DOS previstos del centinela de hash
>   de `MCPBridge.c`, rojos **por diseño** hasta que el canario pase. Correcto asi.
> - **Daemon reiniciado**: generacion `66e91900` -> **`766b911c`**, `daemon_modules.stale` vacio,
>   `watched_count` 39 -> 40, y el payload publica ya el bloque **`fence`** con sus 9 contadores
>   de rechazo; los peers reportan `binding_state: LEGACY_UNBOUND`.
> - **PBO desplegado**: `DCC8730FEB98FF2A` (205.008 B). Dentro: `&inst=` x4 **y**
>   `ui_reload_layout` x3 — el fencing y el tool de la sesion de UI conviviendo. Respaldo del
>   anterior en `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo.bak_pre_fencing_7F19456C_20260819`.
>
> ## LO QUE FALTA: el canario, y no pudo correr
>
> `dayz_test_run(project="DayZ_MCP", mode="all")` devolvio **`worker_failed` a los 2,7 s** con la
> caja LIBRE y la coordinacion limpia. El lease NO fue el problema: el log del daemon muestra
> `enqueue 202` -> `wait 200` -> `release 200`. Falla el worker que lanza el juego, y
> `worker_failed` es un **catch-all** (`dayz_test_worker.py:110`) que se traga la excepcion: no
> esta en el log del daemon ni en `_audit\`. Indiagnosticable desde fuera (`fb-20260819-165154-6b32`).
>
> **Sospecha sin verificar**: este daemon lo arranque a mano porque el spawn perezoso del cliente
> no lo resucito (`daemon_unavailable` x2). Puede faltarle entorno que si hereda el spawn normal.
> **Lo primero que hay que probar** es matar el daemon y dejar que lo levante el cliente, y
> reintentar el run. (`P:` SI es visible, ya descartado.)
>
> **Hasta que el canario pase**: NO re-congelar `test_task9_spawn_phase_markers`. Su rojo es la
> señal de que el PBO no ha pasado el gate.
>
> **Para revertir** el fencing: `patch -R` con `reviews\2026-08-19-lane-fence\delta.diff`,
> borrar los 3 ficheros nuevos, restaurar el PBO desde su `.bak_pre_fencing_7F19456C_20260819`
> y reiniciar el daemon.


# DayZ_MCP - Estado vivo · snapshot 2026-08-19 tarde

**El objetivo del PROYECTO sigue siendo el fencing por `instance` (D-57)** — cerrado offline por la
lane de al lado (su seccion, intacta, mas abajo) y pendiente de tanda in-game. Lo de hoy fue el frente
de UI, que queda **resuelto y en positivo**.

## PROXIMA ACCION

**1. EL VUELO DE PIXELES ESTA CERRADO. La sonda que quedaba se hizo, y contesto mas de lo que preguntaba.**

**PROXIMA ACCION**: nada de UI bloquea. Lo unico que sigue abierto es por que un addon que
SOLO trae stringtable no registra sus claves, y **no bloquea nada** (las claves de mods de verdad
resuelven). Los mods de prueba son DESECHABLES: borrar `P:\Mods\@UITplStrings`, `@UITplPrefix`,
`@UITplDeps` y `@UITplFull` lo deshace entero. Conductores en
`ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-08-19-ui-reload-layout\`; las sondas de
esta noche (`probe_mount.py`, `probe_tp2.py`, `probe_st3.py`, `measure_shots4.py`) estan en el
scratchpad de la sesion.

**LO QUE CERRO LA NOCHE DEL 21** (3 vuelos autonomos, ni una puerta de operador, ~5 min cada uno):

★★ **`text_proportion` depende de la CLASE del widget, y la plantilla publicada llevaba un
atributo muerto.**

| clase | `text_proportion` | evidencia |
|---|---|---|
| `TextWidgetClass` | **lo honra**, lineal | midio 80 / 16 / 16 / 47 px contra 80 / 16,0 / 16,0 / 48,0 predichos |
| `ButtonWidgetClass` | **lo honra** | fuera de muestra: 13 y 38 px contra 12,8 y 38,4 |
| `MultilineTextWidgetClass` | **lo IGNORA** | una celda con 0,20 midio los mismos 80 px que una sin nada |

Modelo: **glifo = 0,74 x valor x alto de caja**, por defecto 1,0. Eso explica ENTERO el nulo del
20/08: aquel barrido corria sobre `MultilineTextWidgetClass Message`. El nulo era real; la
conclusion que saque de el, no — y estaba publicada.

**Dos candidatos muertos en la pantalla**: `"exact text" 0` NO es requisito (82,6% de
co-ocurrencia contra 3,1% de base en 821 layouts, 27x — y las dos celdas midieron 16 px), y la
POSICION del atributo da igual (16 px tras el `name`, 16 px al final).

★ **ABIERTO-UI-01: el PBO SI monta.** Un `.layout` que existe solo dentro de el, cargado por
prefijo de addon, pinta. Su propia clave sale cruda **sin la `#`**: el motor la reconocio y la
busco. Descartados ademas `prefix`, `dependencies[]` y el `CfgMods` completo, un addon por
candidato, 5 claves en un fotograma, con vanilla resolviendo como control positivo y cada PBO
re-extraido tras desplegar para confirmar que el csv iba dentro. Siguiente candidato: `class defs`
con modulos de script.

**HUECO MIO DE LA VISPERA, cerrado hoy**: el 20/08 corregi `dayz-pbo-build` de XML a CSV "en cinco
sitios" y `VALIDATION_CHECKLIST.txt` se quedo fuera — tres casillas mandando escribir
`stringtable.xml` y una mandando correr el validador roto. Es el fichero que alguien sigue paso a
paso.

**SEÑALADO, NO TOCADO**: `skills/dayz-pbo-build/SKILL.md.bak_pre_F3_section` esta trackeado y
marcado `distribution_role=payload` — el pack PUBLICA un backup obsoleto con ~10 referencias a
`stringtable.xml`. Retirar un artefacto distribuido no lo decido yo: queda como tarea
`task_ad07c3f3`.

**LO QUE EL VUELO CERRO** (6 vuelos + 4 sondas, DayZ 1.29.163709, 1280x720 y 1920x1080, todo con
captura detras):
- **Las claves `#STR_` SI resuelven en un preview `$profile:`.** Un fotograma con control
  positivo: clave de otro MOD (`#STR_LFPG_ACTION_ADD_WAYPOINT` -> "Add waypoint"), clave de
  VANILLA (`#STR_CfgWheel0` -> "Rueda"), prosa literal, y una clave sin definir -> cruda. O sea
  que crudo = no definida, no "el preview no traduce".
- **`GetText()` devuelve la clave CRUDA aunque la pantalla pinte la traducida.** En ese mismo
  fotograma `BtnNo.text == "#STR_CfgWheel0"` con "Rueda" en pantalla. Cualquier chequeo que lea
  el texto del widget y busque un `#` inicial reporta un fallo que no existe. Solo juzga el frame.
- **El glifo sigue al ALTO DE LA CAJA**, probado por falsacion: el MISMO `form_row.layout` con
  root `1 1` (RowLabel 435x720 px) pinta una letra que llena la pantalla, y con root `0.30 0.05`
  (130x36 px) sale legible. Sin tocar `text_proportion` en ninguno de los dos.
- **SUPERADO 2026-08-21**: aquel barrido de seis valores de `text_proportion` (0,03 a 0,36) que
  dio 104 px en los seis corria sobre `MultilineTextWidgetClass`, que **ignora el atributo**. El
  nulo era real; la lectura "puede que sea inerte" no. Ver el bloque de la noche del 21 arriba.
- **El preview se dibuja ENCIMA del inventario y aun asi no recibe click.** Cero pixeles rojizos
  en la banda de botones en 4 fotogramas, con el mismo detector encontrando 1.767 en el resto del
  frame. Foco, hover y rueda **no son observables** en este bucle: ni por agente ni por humano.
- `wrap` parte por espacios: una clave sin resolver es un token unico y por eso salia cortada por
  los dos lados. Con prosa real reparte en dos lineas.
- Mecanicas ya verdes en las dos resoluciones: el spacer CRECE (10,67 -> 528,00 px con viewport
  397,44), `iconDrops` se DIBUJA (glifo real, aspecto 1,1175) y los paneles PINTAN.

**PUBLICADO**: `d12e91d` + `501c116` (dayz-ui-development) y `34efa9a` + `27b3643`
(dayz-pbo-build). `packctl validate` **PASS, 0 hallazgos**, con el `source_commit` del informe
comprobado contra el HEAD real — ese cruce es lo que evita apuntarse la mejora de otra lane.
**Sin empujar**: encima hay **3** commits de la lane vecina sin subir. Paquetes `.skill`
reempaquetados y verificados por bytes en la carpeta de review.

**TRAMPA DE HERRAMIENTA, apuntada porque muerde a cualquiera**: `python -m packctl gate` salio con
**exit code 0 y `verdict: FAIL`**. Quien encadene ese gate mirando solo el codigo de salida
publica en rojo creyendo que esta en verde.

**2. EL VUELO DE PIXELES (historico del conductor y sus 4 paradas)**

`ObsidianVault\AI\10_Projects\DayZ_MCP\reviews\2026-08-19-ui-reload-layout\fly_templates.py`
(36.994 B, sha `376d5323c631c1af`, compila). Dos lanzamientos en una sentada — **1920x1080 y luego
1280x720** —, mismo esqueleto que `probe_tex.py` (FIFO 600 s, `run_is_alive` a 60/120 s, heartbeat del
lease cada 40 s, lease liberado ANTES de `dayz_test_stop`), capturas nativas a
`template_shots\<res>\` e informe `fly_templates_report.json`.

**Mide solo lo mecanico** —rects contra las predicciones, flags de `ignore_pointer`, aspecto del icono,
crecimiento del spacer— y **para en 4 puntos de operador**: el click real en `BtnYes` (foco), mirar el
aim con el HUD puesto, la rueda sobre la lista, y el ojo sobre pintura/glifos/claves.

**TRAMPA NUEVA Y GORDA, verificada abriendo el fichero**: `ui_click("BtnYes")` **resuelve el widget
EQUIVOCADO**. `FindAnyWidget` (`MCPClientBridge.c:1695`) engancha con el `BtnYes` de
`mcp_dialog.layout:322-324` —que tiene handler y esta `visible 0`— en vez del de la plantilla. **Un
preview cuyos widgets se llamen como los del dialogo se los come el dialogo.** Aplica a cualquiera que
use el bucle en caliente con nombres normales, no solo a este vuelo.

**Tres verbos pedidos** (`fb-20260820-194257-b685`), con `ui_focus` como el que importa: sin el, "el
boton enfocado sale rojo y no blanco" —el defecto entero de BUG-100— **no se puede medir sin manos**,
porque `SetFocus`/`SetActiveWindow` existen en el motor (`EnWidgets.c:695-698`) y no los expone nadie, y
`ui_click` llama al handler sin dar foco. Los otros dos: `parent=` en `ui_reload_layout` (hoy crea root,
nunca hijo, asi que no se puede inyectar una fila en un arbol vivo) y `ui_scroll` (`VScrollToPos`,
`:499`, sin exponer).

**AVISO DE ESTADO DEL PACK**: mi commit `96073a7` esta empujado, pero el repo tiene encima
`4add26e` (*docs(ui): answer what the offline preview does with text...*) **SIN empujar**, y no es
mio: es de la lane vecina, que lleva toda la noche trabajando la misma skill. No lo he subido —
empujar el commit de otro no me toca. Si alguien hace `git push` ahi, va incluido.

**2. LOS DOS FRENTES EN VERDE TRAS REVISION CIEGA. Lo que queda es de PIXELES.**

**Playbooks (LUZ VERDE)**: 3 playbooks, 16 fixtures (13 RED), **45 tests OK**. El revisor ciego dijo NO
en la primera pasada con 5 hallazgos y los cerro todos por mutacion en la segunda. Lo que gano el
sistema: el fail-open de `box.foreign` tapado con `scan_known` (fail-CLOSED probado con **11 formas**
del campo; solo el booleano `True` abre), un paso `bridge_status` que SI observa el fencing D-55, la
banda sin acotar del umbral reducida de **12.500x a 150x**, y `n_sites_required` con lector — era el
proximo `requires_bridge`. La lane del fencing intacta: **283 tests en 6 modulos adyacentes**.

**Plantillas de UI (LUZ VERDE)**: 4 plantillas + 8 ficheros de rects + README, commit **`96073a7`**
empujado al pack publico. Tres rondas de revision ciega. Lo que cazo, en orden de lo que dolio:
- **Un numero inventado**: `rover_sim_colorable` "616x" cuando el propio pack dice **577** dos veces; el
  616 era el conteo de `wrap` de otra tabla. Cuatro numeros mal y una etiqueta de corpus equivocada.
- **El `CLEAN` del lint era vacuo en 2 de las 4**: R5/R6/R7 solo miran widgets clicables. Probado
  rompiendo: badge fuera de pantalla, panel a tamano cero y **borrar el GridSpacer** sobrevivian los
  tres. Con `--expect-visible` mueren. El README lo dice con esas palabras.
- **`scroll_list` da ROJO en su copia correcta** (R4+R5 sobre `ScrollContent`): la altura 0 del spacer
  es el contrato. Documentado con la consecuencia — darle altura reintroduce la trampa que evita.
- **Y el bloqueante final no se veia mirando ficheros**: los arreglos estaban en el worktree y el
  INDICE seguia con la version anterior. Un commit habria publicado el 616 y la regla refutada. Lo cazo
  con `git show :<ruta>`.

**PENDIENTE DE UN FRAME RENDERIZADO** (nada de esto lo cierra ningun parser; lista del revisor):
1. Que los tres paneles pinten con esos alfa sin quedar lavados.
2. **El boton en `Focus`: rojo y no blanco** — hay que ENFOCARLO con teclado/mando, no basta abrir el dialogo.
3. Que el `GridSpacer` CREZCA al inyectar filas y que la rueda mueva. El `h=0` offline es correcto por
   diseno; que el motor lo suba no lo prueba ningun fichero.
4. Que `wrap 1` reparta lineas y un titulo largo no desborde: caben ~2 lineas y hay 18 px de hueco.
5. Que `iconDrops` se DIBUJE. El nombre resuelve (`dayz_gui.imageset:1052`), pero una textura que falta
   pinta BLANCO PLANO y no registra nada: resolver el nombre y verse son cosas distintas.
6. Que el HUD no robe el aim (`ignorepointer` es runtime).
7. **Las dos resoluciones en la misma sesion**: a 720p los botones quedan en 184,8x40,3 px con glifo de
   20 px. Ahi se ve si algo es ilegible.
8. Que las 9 claves `#STR_UI_*` resuelvan en vez de imprimirse crudas.

**AL EMPAQUETAR LA SKILL, OJO**: `references/dabs-framework.md` y `vanilla-menus-map.md` son **mas
nuevos en el arbol instalado que en el repo** (19-ago 01:46 vs 17-ago). Instalar el `.skill` completo
los pisa con version vieja. Y siguen sin estar en el repo: **estan a una promocion de desaparecer**.

**2. PLAYBOOKS: de 1 a 3, y la regla del sistema ya la impone una maquina.** Segunda lane Grok,
verificada por mi. `box_is_mine` (lease + caja sin diag ajeno + el run es mio) y `run_really_started`
(el run existe, esta `RUNNING`, los peers responden), ambos **DRAFT** y sin umbrales inventados. Corri
yo los tres juegos de fixtures: **exit 0 los tres**. Suite re-corrida por mi: **1736 tests, 2 failures,
4 skipped** — identico a lo que declaro; la baseline sube de 1711 y **no hay tercer rojo** (los 2 son el
centinela de `MCPBridge.c` de la lane vecina).

**Lo que mas vale de esa entrega no es un playbook**: `test_every_toml_loads_and_has_red_per_on_fail`
(`tools/tests/test_playbook_runner.py:86`) recorre TODOS los `.toml`, valida esquema, corre sus
fixtures y exige que **cada `on_fail` declarado aparezca como FAIL/WARN observado**. La convencion
escrita del README pasa a estar impuesta por maquina: un playbook futuro sin sus RED tumba la suite.

**Me corrigio una premisa mia**: le dije que un campo renombrado haria pasar el playbook siempre. Falso
en ESTE runner — un campo ausente hace que `eq`/`finite` devuelvan False, o sea FAIL ruidoso
(`playbooks/runner.py:434`, comprobado). Para un checklist que no pueda fallar harian falta
`expect = []`. La auditoria de campos de `place_safely` dio **6/6 verde** con productor citado.

**`requires_bridge` CERRADO**: se borro de los tres TOML (el gate real ya es `--require-version`; el campo no lo leia nadie y ademas mentia, `"7"` contra `"8"`), y `test_requires_bridge_is_not_a_gate` se pone rojo si alguien lo reinserta — mutacion ejecutada y restaurada, por el implementador y por el revisor.

**Y descarto `dialog_reachable` con razon comprobada**: necesita direccionar widgets por NOMBRE y
`get_field` (`runner.py:290`) solo recorre listas por INDICE, asi que `ui.nodes.0` no es `BtnOk`.
Portarlo habria dado fixtures verdes con comportamiento vivo dependiente del orden del walk.

**LISTA DE LA COMPRA PARA UN SOLO VUELO** (saca `place_safely` de DRAFT): `canopy_dy` pide 3 sitios y
tiene 1. Faltan **(7500, 7500)** y **(7612, 7602)**. Por sitio: `surface_query(x,z)` -> apuntar `y`;
`scene_raycast(from=[x, y+30, z], to=[x, y-5, z], intersect="geom")` -> apuntar `raycast.pos[1]`; el
numero es `abs(pos[1] - y)`. Con 3 filas, el umbral pasa a calibrado — y eso lo promociona el usuario.
Fichas de contribucion archivadas: `fb-20260819-220300-470c` y `fb-20260819-220314-db33`.

**2. ENTREGA DE GROK RECOGIDA Y VERIFICADA.** Los cinco puntos, cerrados salvo un paso operativo.

**El veredicto del punto 1 fue "de los dos"**: el motivo del fallo existia dentro del lifecycle pero
NO llegaba al cliente. El worker solo trata como rechazo pre-admision los payloads cuyo unico campo es
`error` (`dayz_test_worker.py:340-346`) y `instance_config_missing` no esta en `WORKER_ERROR_CODES`
(`:27-41`), asi que todo colapsaba a `worker_failed`. La otra mitad era mia: mi conductor esperaba
mirando solo `run_id`.

**Lo que cambio** (sha256[:16] antes -> despues, comprobados contra disco por mi, 5/5):
`process_lifecycle.py` `b27e0ce5` -> `12325e49` (ranura `last_start_error`; `stop_run` acepta el
fantasma EXITED sin ack y sin owner) · `dayz_test_tool.py` `484b1b42` -> `f756bc4b` (el compacto
sustituye `worker_failed` por el motivo real) · `server.py` `9876ca9f` -> `3e4597f9` (el tope de
`wait_for_box_s` en la descripcion, interpolado de la constante, asi no puede desincronizarse) ·
`dayz-mcp-architecture.md` (recuento 51 + §4.1 familia UI) · **`loopback.py` intacto**, sin tocar el
fencing.

**Verificacion propia, no su palabra**: 4 citas abiertas a mano, correctas; suite re-corrida por mi
**1711 tests, 2 failures, 4 skipped**, identico a lo que declaro; y **cerre el agujero que el mismo
confeso** —no habia medido en rojo el gate de `stop_run` porque el bloque se le evaporo a mitad de
corrida (la lane vecina escribe el mismo fichero)—: mutado el guard `never_started`, los **2 tests se
ponen rojos**, fichero restaurado a `12325e49d6aa602a`.

**LO QUE FALTA, Y ES OPERATIVO**: `last_start_error` es **una sola ranura en RAM del demonio**
(`process_lifecycle.py:815`, se pone en `:1107`, se limpia en `:1423`, se expone en `:2361`). **No
sirve de nada hasta reiniciar el demonio**, y reporta el ULTIMO arranque fallido, no el de un run
concreto. Y ojo al cambio de contrato: el compacto ya no devuelve `error_code=worker_failed` en ese
camino, sino el motivo real — quien hiciera match con `worker_failed` deja de verlo.

**Los 2 rojos de la suite no son de nadie de esta lane**: pinean el sha de `MCPBridge.c`
(`test_task9_spawn_phase_markers`). El fichero en disco es `2C4B198B...` con mtime 18:27:52 y ya
contiene `instance`; el pin sigue en `F1B49714...`, el centinela viejo. Es el fencing entrando sin
actualizar su centinela — la D-60 de la lane vecina dice que ese rojo es deliberado.

**2. El bucle de UI en caliente esta CERRADO Y MEDIDO.** `ui_reload_layout` verificado in-game
(run `08343f0c`, PBO **`7F19456CF4F1B08E`**, 4/4): el guard rechaza un path inexistente sin matar al
cliente, un `.layout` escrito desde fuera carga con peor delta **4,15e-05 px** contra el predictor, el
fichero **se relee en cada llamada** (mismo path, bytes distintos, rect distinto, 5,37e-05 px) y
`mode="close"` no deja nada. Ficha `fb-20260819-143308-291d` resuelta. Iterar UI ya no cuesta un vuelo.

**El frente de UI en caliente queda CERRADO por completo.** La ultima pregunta abierta —si `$profile:`
vale tambien para texturas— esta medida y la respuesta es **si** (run `fdf07db7`): un `ImageWidgetClass`
apuntando al perfil dibuja lo mismo que el mismo `.paa` dentro de un PBO, con el control positivo y el
negativo en el mismo frame. La UI caliente puede llevar imagenes propias. Trampa que se lleva por
delante media hora si no se sabe: **una textura de UI que falta pinta un cuadro BLANCO plano y el RPT no
dice nada**.

**Precision sobre lo anterior, porque la diferencia importa**: lo medido es el **atributo de layout**
`image0 "$profile:x.paa"`. La **llamada de script** `LoadImageFile` (`EnWidgets.c:257`) sigue **sin
medir** — es otro camino de codigo. Ya no hace falta un vuelo dedicado para saberlo: con
`ui_reload_layout` cargado, basta una llamada a `LoadImageFile` sobre un widget del preview y mirar. Va
de paquete en el proximo vuelo, no merece uno propio.

**3. BUG-099 CERRADO — no era defecto nuestro.** Probado con click fisico (run `98fc44af`): con la
ventana del juego ya enfocada, un solo click basta; lo que se comia el primero era el **foco de ventana
de Windows**. Sin arreglo que hacer. El canal mecanico que monte para que el dialogo se auto-reportase
**no trajo la respuesta** (`state=timed_out` a los 240 s): el veredicto es del usuario, por chat.
Queda de fase 3 la pasada 2 a 1280x720 con clicks fisicos.

## ⚠️ AVISO A LA LANE DEL FENCING + OJO CON EL PBO DESPLEGADO

**Pedisteis aviso si alguien despliega, asi que: si, he desplegado.** `DA8534EBF3126A00` a las 13:10,
reconstruido desde el arbol (no reutilice ninguno vuestro). Eso deja obsoleto vuestro `A9C0089998054E66`
otra vez, porque el mio anade la cadena de sondas a `MCPDialogController.c` ademas de los arreglos de
layout que ya llevabais. Cuando volvais a construir, cogera tambien eso. Si os estorba, el
`.bak_pre_probe_20260819` es el PBO limpio sin sonda (`4B897684`) y volver es una copia.

### El PBO desplegado lleva codigo EXPERIMENTAL

`P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` = **`DA8534EBF3126A00`** (201.276 B). Respaldos:
`.bak_pre_probe_20260819` (= `4B897684`, el bueno sin sonda), `.bak_pre_uifix2_20260819` (`4155136D`),
`.bak_pre_uifix_20260819` (`F070A6C8`, original).

`MCPDialogController` ya NO carga una ruta fija: prueba tres candidatos guardados por `FileExist` y se
queda con el primero que exista. **Ahora mismo hay un `mcp_hot.layout` en
`P:\LFPowerGrid_dev\_client\profiles\`, asi que el dialogo se sirve de AHI, no del PBO.** Si alguien
ve el dialogo comportarse raro, es eso. Para volver al comportamiento normal: borrar ese fichero (cae
al PBO por fallback) o desplegar el `.bak_pre_probe`. El linter da 2 FAIL sobre esas rutas y son
**deliberados** — ficha `fb-20260819-110949-73dd` pide que la regla aprenda el patron guardado.

## RESUELTO HOY — el frente de UI en caliente, en positivo

**El ciclo existe y va por `$profile:`.** El motor carga un `.layout` desde el directorio de perfil,
escribible desde fuera del juego, sin repack. Doble canal: `ui_tree` da `user_id 777` y el log del
cliente da `probe WINNER=$profile:mcp_hot.layout` (run `3b63168d`).

**Las otras tres rutas son UNA sola regla: el prefijo del addon lo sirve el PBO y solo el PBO.**
Work drive (SP-078), suelto en ruta empaquetada (gana el PBO), y suelto en ruta NO empaquetada — este
ultimo con control positivo dentro de la propia medida: `FileExist` da 1 para un fichero que solo
existe DENTRO del PBO, luego su 0 en la ruta suelta es ausencia real (run `4320d1d6`).
**Corolario**: anadir assets en caliente por prefijo de addon queda cerrado por el mismo mecanismo.

**BUG-097, BUG-098 y BUG-100 cerrados y verificados EN PANTALLA.** El panel pinta con
`rover_sim_colorable` (`ColorablePanel` era un no-op: existe y su estado Normal no pinta nada), el
titulo de 29 caracteres se lee entero, y los botones son legibles con `color 1 0 0 1` +
`inheritalpha 0` — el bloque blanco era el estado **Focus** de `EmptyHighlight` sin tintar.

**Skill `council-orchestration` escrita, instalada y empaquetada** tras el diagnostico del usuario
sobre como orquesto. 15 reglas comprobables, manifiesto antes del primer PID, brief unico con SHA.

## OTRO HILO VIVO, de la sesion de al lado: el fencing por `instance`: CERRADO OFFLINE

Escrito por la sesion que trabajo el fencing (18/08 23:00 -> 19/08 04:30). **No pisa nada de lo
de arriba**: son dos frentes distintos del mismo proyecto y el tuyo (UI) va por su lado. Tu
propia evidencia de esta noche —los `world_spawn` ajenos cayendo en tu mundo— es exactamente
lo que este trabajo cerca.

**Estado: implementado, revisado a ciegas en 4 pasadas, y CERRADO en todo lo que no necesita
juego. NADA promocionado al arbol.** Todo vive en `DayZ_MCP_dev\reviews\2026-08-19-lane-fence\`:

- `delta.diff` — el parche (34 ficheros; 3 nuevos, 8 de produccion, 23 de test).
  ⚠ **`gui\layouts\mcp_dialog.layout` esta FUERA del parche a proposito**: lo estas tocando tu
  (7670 -> 7696 -> 7722 -> 7922 B en una noche) y promocionar la copia lo revertiria.
- `DayZ_MCP_fence_DCC8730F.pbo` — 205.008 B, sha256 `DCC8730FEB98FF2A` (**el `A9C00899` se retiró el 19/08 a las 18:00**: su `MCPClientBridge.c` era anterior al tool `ui_reload_layout` y desplegarlo lo habría borrado; ahora el delta va MERGEADO, no sustituyendo). Historia del anterior: **Reconstruido a las
  04:24 sobre TU arbol**, o sea lleva tus arreglos de layout (BUG-097/BUG-100) mas los tres
  `.c` del fencing. El anterior (`6E1059DC`, 03:41) quedo obsoleto en cuanto desplegaste el
  tuyo y ya se ha borrado. Verificado: 13 entradas byte-identicas a fuente, `&inst=` x4,
  `MCP_BRIDGE_VERSION` sigue en `"8"` (sin bump, D-57).
- `TANDA-INGAME.md` — el guion de la sesion de juego, con TODOS los pendientes agrupados.
- `canario\canary_fence.py` — el canario en 3 fases, capturando por `cmdline_match`.

**Suite: 1678 tests, unico rojo el centinela** de `MCPBridge.c` (rojo POR DISENO hasta que el
PBO pase el gate in-game; se re-congela DESPUES del canario, nunca antes).

**Lo que midio la revision ciega, ronda a ronda:** el fencing empezo siendo decorativo (el
intruso recibia **100 de 100** mutaciones por una cache indexada por instancia); tras el
arreglo, **0 de 100**. Luego la optimizacion que lo sustituyo dejo al peer rezagado sin un
solo comando (**40/40** `unattributed`) y podia esconder al intruso del detector; ambas a cero
ahora. La ultima ronda cerro una invariante propagada a **2 sitios de 6**: cuatro caminos
ponian `EXITED` sin retirar bindings, y eso daba `ready:true` con servidor de un run y cliente
de otro, con las dos mutaciones aceptadas.

**Buzon: 84 -> 75 abiertas** (cierre en lote del 19/08). Propuesta de una lane Grok sobre las
84, revisada por subagente Opus: 22 `RESOLVER` propuestas, **12 aplicadas**, 7 rechazadas por
cierre en falso y 3 retenidas por dudosas. Evidencia en
`10_Projects\DayZ_MCP\reviews\2026-08-19-triaje-nocturno\` (`resoluciones.json`,
`revision-opus.md`, `a-aplicar.json`).
⚠ **Una correccion no se cierra sola**: tres de los rechazos cerraban la correccion de una ficha
dejando abierta la ficha corregida — la correccion desapareceria y sobreviviria el texto
equivocado. Van al mismo estado que su padre (LL-324).

**Si vas a desplegar un PBO tuyo**: reconstruye desde el arbol, no reutilices ninguno de los
mios, y avisa aqui — el PBO es uno por maquina.

### Ademas: **AGUJERO 1 DEL COORDINADOR — cerrado offline, SIN promocionar** (19/08 tarde)

Entrega completa en `DayZ_MCP_dev\reviews\2026-08-19-lane-a1\` (`MANIFEST.md` primero).
Es el primer trabajo de este proyecto en dias que **no necesita juego para cerrarse**: es
logica del daemon con tests unitarios, no toca puente ni `.c` ni `MCP_BRIDGE_VERSION`, y
no hace falta PBO ni canario.

**Que arregla**: con 128 tombstones acuñados por extraños que abandonaron su espera, el
**dueño legitimo de la caja** cobraba `operation_tombstones_saturated` (503) y se quedaba
sin lease ni `start_run` durante los 600 s de su propio claim. El chequeo de saturacion
(`session_coordination.py:295` y `:719`) corre antes del `operation_conflict` de `:301` y
mucho antes del `_active.client == client` de `:346`, y `session_acquire_wait` manda
siempre un `operation_id` nuevo — asi que el dueño entraba siempre por la puerta cerrada.

**Medido**: suite `Ran 1629 / OK` (base efectiva 1622 + 7 tests nuevos); los 4 tests de la
ronda 1 en ROJO contra el codigo sin tocar antes de existir el arreglo; **15 mutaciones**
(12 de la revision ciega + 3 de la ronda 2), cada una tumbando el test que dice proteger y
ningun otro; tests **puramente aditivos** (241 lineas añadidas, **0 borradas**); y foto de
541 ficheros del arbol real antes y despues, **0 cambiados**.

**Los dos MAYORES que encontro la revision eran de COBERTURA, no de conducta**: el codigo
era correcto pero dos de sus cuatro ramas — el privilegio en `cancel_operation` (`:801`) y
la mitad "lease activo" de la invariante (`:2753-2754`) — podian borrarse dejando la suite
**entera en verde**. Solo la mutacion las encontro; leer el diff no bastaba. Cerradas con
un test cada una en la ronda 2.

**Un MEDIO real, arreglado**: el `if error.hint` de `server.py:709` se evaluaba ANTES de la
allowlist de codigos, asi que cualquier no-2xx con `hint` devolvia su codigo crudo saltandose
el saneado. No habia regresion viva; era la puerta abierta para el siguiente que añadiera un
`hint`. Ahora la rama exige codigo conocido.

⚠ **Conducta que conviene saber antes de promocionar**: el holder del lease con un
`operation_id` distinto del suyo ahora recibe **409 `operation_conflict`**, no un 2xx. Eso
ES el arreglo — antes ni llegaba a ese 409 porque el 503 disparaba primero.

⚠ **Correccion al spec del coordinador**: su seccion B afirma que el fencing no toca
`server.py`. **Si lo toca**, en 4 hunks (lineas 86-221). No hay solape con esto —
`public_error_code` esta ~500 lineas mas abajo — pero la premisa era falsa.

**PROMOCIONADO** (17:21): los 4 ficheros estan en el arbol, con respaldos
`*.bak_pre_agujero1_20260819` al lado. **El daemon NO se reinicio** — el disco lleva el codigo
nuevo y el proceso vivo sigue con el viejo hasta su proximo arranque, para no pisar el vuelo de
la sesion vecina. Suite en el arbol: **`Ran 1639 / OK`**.

⚠ **Trampa medida al promocionar, se va a repetir**: la PRIMERA pasada dio 1 rojo
(`test_registered_tool_dispatches_and_does_not_hold_lock`) que **no era del parche**. Ese test
hace `inspect.getsource`, que coge los numeros de linea del codigo ya compilado y lee el TEXTO
del disco; la sesion vecina reescribio `server.py` a las 17:23 **con la suite en vuelo**
(`E437FD85` -> `4DD8B1CB`, tool `ui_reload_layout` nuevo) y la lectura aterrizo 38 lineas mas
arriba. Descartado por medida, no por corazonada: pasa en solitario con el parche, el control
pre-parche solo tumba los 7 nuevos, y la segunda pasada dio 1639 verdes. **La suite se corre con
el arbol quieto** (`fb-20260819-153716-8491`).

**Agujeros 2 y 3 siguen BLOQUEADOS** por el fencing sin promocionar: el 3 choca de verdad en
`_reap_run_locked`. El 1 no chocaba y por eso se hizo primero.

**BUG-104 ya tiene MECANISMO, no candidato**: el run fantasma no es latencia ni fallo del
reaper, es que **el reaper no llega a mirar**. `reap_dead_runs`
(`process_lifecycle.py:2219-2229`) hace `if self._quarantined(): return []` antes de
inspeccionar nada, y `_quarantined()` (`:849-861`) es una sonda VIVA que da True siempre que
haya un DayZ **retail** corriendo. La reja es mas ancha de lo necesario: el reaper no mata
nada. Discriminador barato si vuelve: `bridge_status` ya publica `retail_quarantine`.
Sin verificar: que hubiera retail vivo aquellos 17 min.

**Trampa nueva medida**: `DaemonStartupElectionProcessTest` **flakea bajo carga** — el
watchdog de idle del demonio de prueba (`timeout=0s`, `poll=5s`) suelta el puerto antes de
que conecte el cliente. En la suite entera daba 2 rojos; ese modulo solo, `Ran 7 / OK`. Si
lo ves rojo, reejecuta antes de acusar a nadie (`fb-20260819-140207-ec0d`).

### ESTADO 18:15 — tanda in-game ARMADA, esperando a que se libere la caja

**El agujero 1 YA ESTA VIVO.** El daemon arranco a las **17:37**, despues de la promocion de las
17:21, asi que cargó el codigo nuevo (`daemon_modules.stale` vacio). No hace falta reiniciar nada.

**Bloqueo**: la caja esta ocupada por una pareja AJENA viva — `@LFHeliCore + @LFHeli_OH1`, puerto
2502, servidor y cliente. Sin runs gestionados ni dueño, pero el juego esta arriba, asi que el
canario no puede correr. Decision del usuario: **esperar**, y hacer la tanda entera de una vez.

⚠ **No promocionar el fencing "de mientras".** A diferencia del agujero 1, dejarlo en el arbol sin
reiniciar NO es neutro: arma una trampa. El daemon ya se reinicio solo una vez hoy (17:37, por otra
sesion); si vuelve a pasar con el fencing puesto, entra en servicio de golpe y **cualquier juego
lanzado a mano queda LEGACY_UNBOUND = solo lectura** para todas las sesiones. La promocion va junta
con el reinicio y el despliegue, o no va.

**PBO listo y VERIFICADO**: `DayZ_MCP_fence_DCC8730F.pbo`, 205.008 B, sha `DCC8730FEB98FF2A`.
El `A9C00899` se retiro y borro — llevaba dentro un `MCPClientBridge.c` anterior al tool
`ui_reload_layout` y desplegarlo lo habria borrado en silencio (`fb-20260819-161133-ac1f`).
El delta del fencing va ahora **MERGEADO** sobre el arbol de hoy, no sustituyendo: **si el arbol
se vuelve a mover, rehacer el merge, no sustituir el fichero**. El comprobador ya lo detecta
(`enforce-base.json` + `pbo_still_valid.py`, probado con mutacion).

**Orden cuando la caja quede libre**: promocionar fencing → suite en el arbol → reiniciar daemon
→ desplegar `DCC8730F` → canario (`wrong_target_canary_count == 0`). Guion completo en
`reviews\2026-08-19-lane-fence\TANDA-INGAME.md`.

## LO QUE MANDA DE HOY

**Un negativo acotado a una variante no es un negativo general.** Cerre el frente de UI en caliente en
negativo y lo escribi en tres documentos durables. Cada medida individual era honesta; lo que estaba
mal era el **alcance de la frase**. Hicieron falta dos lanes ciegas con acceso al source para preguntar
por la variante que nadie habia cubierto — y era la buena. Al escribir un negativo, enumera las
variantes y di cual mediste.

**Y el brief es el tratamiento experimental, no el modelo.** El council de 5 lanes no es comparable
porque les mande prompts distintos y luego los compare. Cualquier scorecard que no registre el hash del
brief esta midiendo el prompt del orquestador. Esta en la skill nueva como regla con su check.

## Pendiente

1. **Fencing por `instance`** (D-57): cerrado offline por la lane vecina, pendiente de tanda in-game.
2. Buzon: **7 fichas nuevas hoy**. Sin triar.
3. Port del predictor a `DayZ_Tooling` — prompt en manos del usuario, con una linea mal
   (`text_proportion` SI vale en `TextWidget`: 679 usos).
4. Codex sin cuota hasta el 20-ago 11:16. Si aporta algo sobre metodo que las otras lanes no vieron,
   se mete en la skill antes de darla por cerrada.

## Trampas nuevas

- **`CreateWidgets` sobre un fichero inexistente revienta el cliente dentro del native.** Guard
  obligatorio con `FileExist` (`ensystem.c:397`), que SI resuelve rutas del VFS.
- **No existe `PanelWidgetTypeID`** (`EnWidgets.c:12-49`), ni `SetStyle`, ni `SetFont`. Vanilla llama
  a `CreateWidget` 2 veces frente a 149 a `CreateWidgets`. Construir UI en runtime da cajas, no
  interfaces.
- **`GetTextSize` (`:210`) existe** y es el oraculo parcial para el texto que `ui_tree` no lee.
- **Un cliente puede arrancar y morir sin conectar**, dejando un RPT de 723 B solo cabecera, sin
  minidump y con Steam vivo — descarta la causa de SP-095. Los conductores relanzan hasta 3 veces.
- **Tres almacenes de skills con drift invisible**: `codex-handoff-template` tiene 1208 / 1321 / 1282
  lineas segun donde mires. Una cita `path:line` a ese arbol tiene que decir de que almacen habla.
- **Y el arbol del plugin BORRA lo que no gestiona.** La skill `council-orchestration` se escribio y se
  verifico en la raiz activa, y al volver a mirar **habia desaparecido de los tres almacenes**; solo
  sobrevivio el `.skill` empaquetado. No es drift entre copias: es reconciliacion que se lleva el
  directorio entero. **La copia de verdad vive fuera del arbol gestionado**:
  `10_Projects\DayZ_MCP\reviews\2026-08-19-council-hotui\council-orchestration-src\`, y el `.skill`
  es lo que la restaura.

## Centinela + version
`MCP_BRIDGE_VERSION` = **8**, sin tocar. Centinela `MCPBridge.c` verificado antes del ultimo repack:
`F1B49714E2CC9660`.

**Gate de arranque:** `Retomo DayZ_MCP desde: el frente de UI en caliente RESUELTO EN POSITIVO — el
motor carga .layout desde $profile: sin repack (run 3b63168d, doble canal), y las otras tres rutas son
una sola regla: el prefijo del addon lo sirve el PBO y solo el PBO · BUG-097/098/100 cerrados y
verificados en pantalla · skill council-orchestration instalada · proxima accion: escribir el verbo de
recarga (Unlink + CreateWidgets sobre $profile:), que convierte la iteracion en segundos; el spec es el
.layout, NO un JSON de widgets (D-59: no existe PanelWidgetTypeID, ni SetStyle, ni SetFont) · OJO: el
PBO desplegado DA8534EB lleva la cadena de sondas y el dialogo se sirve de
_client\profiles\mcp_hot.layout, no del PBO · la leccion: un negativo acotado a una variante no es un
negativo general, y el brief es el tratamiento experimental, no el modelo`

</details>
<!-- LIVE-STATE:END -->

---

## Log histórico

## Cierre 21 (19/08 00:00 → 03:45, LIVE-STATE anterior) — conservado integro
# DayZ_MCP — Estado vivo · snapshot 2026-08-19 01:4x

**Objetivo vivo sigue siendo el mismo que el 18: implementar el fencing por `instance`** (spec
`plans\2026-08-18-fencing-instance-spec.md`, opcion A sin bump de version, D-57). Hoy NO se toco.
Lo de hoy fue el frente de UI, que estaba pendiente desde el cierre 20.

## 🎯 PROXIMA ACCION (una sola): un vuelo, en cuanto la caja quede libre

Encadena cuatro cosas que hoy no se pudieron hacer porque el PBO estaba **bloqueado por un run
ajeno**:

1. **Sonda del ciclo en caliente, corregida el 19-ago.** La version anterior de este paso
   ("arrancar sin reempaquetar y ver si el panel pinta") **no media nada** y habria quemado el
   vuelo: `P:\Mods\@DayZ_MCP\` contiene SOLO `Addons\*.pbo` — no hay arbol de prefijo
   desempaquetado, asi que el motor solo puede leer del PBO. Verificado: el PBO desplegado
   (18-ago 14:18) es anterior a la edicion del layout (19-ago 01:27) y contiene `TextWidgetClass
   Title` sin `ColorablePanel`; y ningun mod de `P:\Mods\@*` trae prefijo suelto. SP-078 tampoco
   cubre este caso: midio la variante *work drive* (`P:\<prefijo>\...`), no la carpeta del mod.
   **Sonda correcta**: copiar el layout arreglado a
   `P:\Mods\@DayZ_MCP\DayZ_MCP\gui\layouts\mcp_dialog.layout` y arrancar SIN reempaquetar.
   Si pinta → existe ciclo de UI en caliente (el frente "desaparcar UI en caliente" queda ganado).
   Si no pinta → gana el PBO tambien para layouts, y se reempaqueta **a mano**: `dayz_test_run`
   con `build:true` esta ROTO (no escribe el PBO, D-33), AddonBuilder a mano si funciona (~3,4 s).
   **No escribir en `P:\Mods\@DayZ_MCP\` con la caja tomada por un run ajeno**: el PBO esta
   mapeado por su juego y un layout suelto le cambiaria la UI en caliente (D-55).
2. **Verificar los dos arreglos** (panel con fondo, titulo sin recortar).
3. **Discriminador del doble click**: ventana YA enfocada y raton dentro, abrir un dialogo y contar
   clicks. Distingue foco de ventana de Windows (no es defecto nuestro) de foco de widget (si lo es).
4. **Pasada 2 de fase 3 a 1280x720** (`dayz_test_stop` + `dayz_test_run` con `width/height`; ojo con
   la ventana de reconexion, `fb-20260818-224553-f81d`). Y si el run lleva `@LFPowerGrid`, un
   `ui_tree(BTCAtmRoot)` de propina calibra `left_ref`/`top_ref`, que el predictor no modela.

## ✅ Cerrado 2026-08-19 — detalle en `30_Sessions/2026-08-19-DayZ_MCP-ui-fase3-linter-y-skill.md`

- **Fase 3 pasada 1: 11/11 casos correctos con clicks FISICOS de raton.** Verificado por valor, no
  por etiqueta (orden de `values`, unicode, `cancelled` sin `choice`, error de campo obligatorio con
  la llamada pendiente). El P1 historico `BtnNo` ∩ `BtnCancel` **cerrado bajo raton**: 639.0 → 828.7,
  865.2 → 1054.9, 1091.3.
- **Y el humano encontro 3 defectos que el gate no puede ver**, con el gate en verde: panel sin
  pintar, titulo recortado, doble click. Fichas `fb-20260818-225827-29bc`, `…-230058-88d0`,
  `…-231944-93ec`.
- **Dos arreglados en fuente, SIN construir**: `style Colorable` → `ColorablePanel`; `Title` a
  `MultilineTextWidgetClass` + `wrap 1` + `text_proportion 0.36`. **Los tres atributos sin verificar
  in-game.**
- **Detector de layouts dentro del linter que ya existia** (D-58): `DayZ_Tooling\scripts\
  detectors\layout_addressability.py` + `shared\layout_ast.py`, cableado en `script_validator.py`.
  Calibrado sobre 819 layouts publicados y falsificado con 7 sondas. Probado que no altera hallazgos
  preexistentes.
- **Skill `dayz-ui-development` parcheada y entregada empaquetada** (4 ficheros, `.skill` verificado
  abriendolo).
- Research de Grok sobre plantillas de layout: $0,25, 20 turnos, **120/120 citas resuelven**.

## 🔥 LO QUE MANDA DE HOY

**Un gate automatico en verde no dice NADA de como se ve la UI, y es estructural.** `ui_tree`
devuelve el color correcto aunque no se pinte; el texto de `TextWidget`/`MultilineTextWidget`/
`RichTextWidget` es ilegible por contrato (`MCPClientBridge.c:1677-1705`); y `ui_click` llama
`OnClick(target, 0, 0, mouseButton)` con las coordenadas **cableadas a cero**
(`MCPClientBridge.c:1759`), saltandose hit-testing, orden Z e `IGNOREPOINTER`. Para render e
interaccion hace falta un humano o una captura mirada. `capture_screenshot` deberia ser evidencia
obligatoria del protocolo de fase 3, no opcional.

## ⏸ Pendiente (ademas del vuelo)

1. **Fencing por `instance`** (D-57 / D-55): sin tocar hoy. Sigue siendo el objetivo del proyecto.
2. Port del predictor de rects a `DayZ_Tooling` — prompt entregado al usuario para la sesion del
   Knowledge Pack. **En ese prompt hay una linea mal**: dice que `text_proportion` no vale en
   `TextWidget` y que `wrap` solo va en Multiline/Html; medido despues, `text_proportion` sale 679
   veces en `TextWidgetClass` y `wrap` tambien aparece en RichText (236) y Text (4).
3. Ideas baratas del research sin implementar: `userID` en nuestros layouts (59 ficheros del corpus
   lo usan, los nuestros no), orquestar los 4 verbos como flujo, y `parent` en `MCPUiNode` como
   primer PBO que lo vale.
4. Tres agujeros de Opus antes de cualquier cola de caja (tombstones globales, BUG-046, reaper sordo).
5. Buzon: **5 fichas nuevas hoy**, sin triar.

## ⚠️ Trampas nuevas

- **Los estilos son por TIPO de widget.** `Colorable` existe para `WindowWidget` y no para
  `PanelWidget` (ahi es `ColorablePanel`). Usar el de otro tipo **no da error**: el widget se queda
  sin ImageSet y no pinta. Y `dayzwidgets.styles` es XML: grepearlo como formato de llaves da un
  falso negativo que suena a respuesta.
- **`MultilineTextWidget extends TextWidget`** (`EnWidgets.c:219`): cambiar la clase en el layout no
  rompe un `Class.CastTo(TextWidget, …)` en Enforce.
- **El empaquetador de skills esta roto en este host**: `quick_validate` hace `read_text()` sin
  encoding y cp1252 revienta con el `SKILL.md` original. Workaround: `PYTHONUTF8=1`.
- **Hay TRES parsers de `.layout` en circulacion** (el del renderizador HTML de `DayZ_UI_Research`,
  el del predictor y `layout_ast.py`). El que debe quedar es `layout_ast.py`.
- **`ui_tree` sobre un cliente que no esta dentro de la partida** devuelve el nodo ausente, no un
  error claro. El conductor de fase 3 ahora espera hasta 10 min en vez de abortar.
- Sigue vigente todo lo del cierre 20: `SetHandler` ≠ `SetUserData`, el log de script del CLIENTE no
  se vuelca en caliente, `dayz_test_stop` no admite lease propio, el bump de version es GLOBAL, y el
  click fisico ajeno existe.

## 🔒 Centinela + version
`MCP_BRIDGE_VERSION` = `EXPECTED_BRIDGE_VERSION` = **8**. Bridge desplegado `F070A6C8` sin cambios
hoy. Centinela de `MCPBridge.c` intacto: `F1B49714E2CC9660…` / base `136A6056C6439B2D…`.

**Gate de arranque:** `Retomo DayZ_MCP desde: fase 3 pasada 1 CERRADA (11/11 con clicks fisicos) pero
la fase 3 NO esta completa — falta pasada 2 a 1280x720 y hay 3 defectos vistos por el humano, 2 ya
arreglados EN FUENTE sin construir (style ColorablePanel; Title a MultilineText + wrap +
text_proportion 0.36) y 1 sin instrumentar (doble click) · proxima accion: UN vuelo cuando la caja
este libre — sonda de UI en caliente = layout suelto en P:\Mods\@DayZ_MCP\DayZ_MCP\gui\layouts\ y arrancar sin repack (la sonda vieja del handoff era voida) → verificar panel y
titulo → discriminador del doble click → pasada 2 · el objetivo del PROYECTO sigue siendo el fencing
por instance (D-57), hoy sin tocar · OJO: un gate en verde no dice nada del render (ui_tree da el
color correcto aunque no pinte, el texto de Text/Multiline/RichText es ilegible por contrato, y
ui_click llama OnClick con 0,0); los estilos son por TIPO de widget y fallan en silencio`


## Cierre 20 (18/08 02:20 → 13:00, LIVE-STATE anterior) — conservado integro

# DayZ_MCP — Estado vivo · snapshot 2026-08-18 13:00 (cierre 20 — **ciclos 6 y 7 IMPLEMENTADOS, revisados a ciegas, promocionados y GATEADOS in-game 11/13**; `ui_dialog` funciona de punta a punta con UI real; suite real **1590 OK**, clon **1427/0/0**, repo publico **`992899e`**; pendiente: caso «busy» intermitente, P1-02 en vivo, sonda lookback, bump v8)

ciclos_en_este_objetivo: 0 — la hoja de ruta de 8 ciclos esta CERRADA EN CODIGO (0-7). Objetivo vivo: **cerrar los cuatro
pendientes in-game** (caso 11 «busy», P1-02 con URL no-loopback, `wait_for(lookback)` por la cadena ATM = regresion del
ciclo 1, `dayz_test_stop` con PID muerto) y con ellos el **PBO siguiente con el bump de version 7→8** (D-52).

## 🎯 PROXIMA ACCION (una sola): con caja PROPIA, re-medir el caso 11 «busy» x3 y, si es bug, arreglarlo + PBO con bump v8 + smoke; en la misma sesion, P1-02 en vivo y la cadena ATM

Antes de tocar nada: `session_status` + `bridge_status.coordination.active` + `Get-CimInstance Win32_Process -Filter
"Name='DayZDiag_x64.exe'"` mirando `-mod=` y `-profiles=`. Hoy la caja la tomo otra sesion (`SUB_BRZ s69`) y **dejo su
juego vivo tras soltar el lease** (perfiles `navprobe_run`, `@SUB_BRZ`): no se tocan procesos ajenos (D-49), asi que los
in-game que faltan se quedaron sin hacer. Guiones listos en el scratchpad de la sesion `fba807d2…`:
`gate_ui_dialog.py` (matriz de 13 casos) y `gate_regress.py` (busy x3 + cadena ATM + lookback + control).

## ✅ Cerrado en el cierre 20 (18/08 02:20 → 13:00) — detalle en `30_Sessions/2026-08-18-DayZ_MCP-ciclos-6-7-gate-ui-dialog.md`

- **Ciclo 6 (lane F Grok, $1,81, 2 rondas; revision CIEGA estatica SOUND-WITH-NITS: 1 P1 + 3 P2, cerrados)**: P1-02 en
  `MCPBridge.c:172-177` (mismo texto y sitio que el cliente `:259-264`); `ui_dialog` fase 2 completa — DTOs
  (`MCPDialogField/Value/Result`, `MCPArgs +kind/message/timeout_s/fields`, `MCPResult +dialog`), `MCPDialogController.c`
  (host oculto pre-creado desde el TICK de mision, `TryFinish` IDLE→OPEN→TERMINAL→IDLE, 6 filas pre-creadas, bloqueo de
  input, sink), `gui/layouts/mcp_dialog.layout` (root `MCPDialogRoot`), dispatch + job diferido, y `CountExcluding` en el
  runner para que el dialogo no bloquee `camera_set`/`drive_probe_client`/`vehicle_get_in_client`. El P1 de la revision
  era un solape de rectangulos `BtnNo`/`BtnCancel` en confirm (un click en la zona comun daba `cancelled` en vez de `no`).
- **Ciclo 7 + remates (lane G Grok, $1,29, 2 rondas; revision CIEGA UNSOUND → cerrada)**: `playbook_run(name, params)`
  sobre las tools ya registradas (misma sesion/lease, sin `tool_lock` de cuerpo entero, lista de denegacion, tope de 32
  pasos, `certified` siempre false con `certified_reason`); **BUG-090** cerrado (`orphan_guard` decodifica consola con
  `errors="replace"`); cable v1.1 (`default_text`, normalizacion de vacios de Enforce). El P1 era un `is_certified` que
  exigia un punto fijo SHA-256 imposible, con test tautologico.
- **Gate in-game (2 PBOs)**: `B7F9BB6DCF677D8C` abria la UI pero **todo click daba `no_handler`** → causa medida:
  `ui_click` recorre `GetScript`/`GetUserData` (`MCPClientBridge.c:1747-1783`) y el controlador solo estaba en
  `SetHandler`. Fix del orquestador (6 lineas): `SetUserData(this)` en root y botones. PBO `CD22655F472C6503`
  (198.557 B) desplegado (backup `DayZ_MCP.pbo.bak_pre_ui_dialog_20260818`). **11/13**: root oculto en reposo; ack+OK
  → `completed/ok`; confirm `yes`/`no`; cerrar confirm → `cancelled`; form 1 campo con `default` legible y unicode de
  vuelta; form 3 campos EN ORDEN + `values_by_id`; 7 campos → `bad_args` en 0,0 s sin abrir UI; obligatorio vacio →
  `Error` visible y llamada pendiente; formulario siguiente limpio; timeout 5 s → `timed_out` a 5,03 s.
- **Post-gate**: `result_prune` alineado con el DTO real (`dialog` en `PRUNABLE_FIELDS`, `("ui_dialog","dialog")` en
  `SEMANTIC_EMPTY_FIELDS`, shim borrado, `null` = no rellenado); **centinela RE-CONGELADO** sobre el bridge gateado:
  `F1B49714E2CC9660…` / base `136A6056C6439B2D…`.
- **Suites y publicacion**: arbol real **1590 OK (skipped=4)**; boundary **211 ficheros / 4,11 MB / 0 hits privados**;
  clon **1427/0/0/29**; checkout **1427/0/0/29**; commit `992899e` (20 ficheros, +2832/−46) y push `2bbb4b4..992899e`.

## ✅ Pendientes IN-GAME CERRADOS (18/08 13:1x-13:2x, run `0716b585`, PBO `CD22655F`) — 4/4 + stop con PID muerto

- **Caso 11 «busy» x3: PASA 3/3** — con un dialogo abierto (comprobado vivo a los 3 s), el segundo devuelve
  `rejected/reason=busy` en **0,68 / 0,6x s** y el primero sigue abierto hasta su `ui_click(BtnOk)` → `completed/ok`.
  La anomalia del cierre 20 (el primero cerrandose solo con `dismissed_by=ok`) **NO se reproduce**: 4 corridas correctas
  contra 1 anomala → se declara flake del arnes, no del controlador. Si vuelve a aparecer: instrumentar `OnClick` con un
  `Print` de widget + estado antes de tocar la logica.
- **BUG-086 `wait_for(lookback)` CONFIRMADO in-game**: el bridge servidor loguea `[MCP-POC] result posted id=<id>` al
  responder, o sea ANTES de que arranque la espera. `wait_for(pattern="result posted id=20", lookback_lines=200)` →
  **satisfied en probe 1, 0,15 s**; el control con `lookback_lines=0` sobre el MISMO patron vence sin verlo (6 sondeos).
- **Regresion del ciclo 1 (verbos UI)**: `ui_tree(BTCAtmRoot)` 1 nodo, `ui_set_text(EditBtcAmount)` ok=1, y
  `ui_click(BtnBuyBtc)` **encuentra handler** (`not_handled` = el handler corrio y declino porque el ATM spawneado no
  esta instalado/alimentado — dominio LFPowerGrid, no MCP). El `action_use` sobre un `LFPG_BTCAtm` recien spawneado da
  `condition_failed` por lo mismo.
- **El dialogo NO bloquea los verbos diferidos** (`CountExcluding`): con un `ui_dialog` abierto, `camera_get` responde en
  **0,30 s** y el dialogo termina normal.
- **`dayz_test_stop` con un PID muerto a proposito**: matado el cliente del run (54268), `dayz_test_stop` cierra el run en
  **2,88 s**, `cleanup_degraded=false` y sin procesos huerfanos.

## ⏸ Pendiente (una sola cosa) — el PBO con el bump de version 7→8 (D-52)

Intentado hoy y **abortado a proposito**: al parar mi run, la otra sesion (`SUB_BRZ`/`navprobe_run`) relanzo el suyo
**con `@DayZ_MCP` cargado**, que (a) bloquea el fichero `P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo` y (b) corre el bridge v7.
El bump ya estaba escrito en el arbol (`MCPMessages.c` + `core.py`) y se **revirtio a 7 en el acto** para no dejar
`version_blocked` a su juego; comprobado despues: sus dos peers re-engancharon `7~1.29.163709` `version_state=ok`,
`ready=true`. El PBO v8 esta construido y verificado en `%TEMP%\dayz_mcp_pbo_20260818_v8\DayZ_MCP.pbo`
(`60EE217EFC370C2B`, 198.557 B, con `MCP_BRIDGE_VERSION = "8"` dentro). Receta para cerrarlo con caja libre:
`scratchpad\bump_v8.py` → desplegar ese PBO (backup primero) → reiniciar daemon → `wait_ready.py` → smoke.
**Tambien queda P1-02 en vivo** (URL no-loopback en `$mission:dayz_mcp.json` del servidor → `config url not loopback` y
sin sondeo; restaurar despues): necesita un arranque propio y, por tanto, la misma ventana.

## 🔒 Centinela + version gate
`test_task9_spawn_phase_markers` congela `MCPBridge.c`: **`F1B49714E2CC9660…`** / base **`136A6056C6439B2D…`**
(re-congelado 18/08 sobre el estado gateado). `MCP_BRIDGE_VERSION` = `EXPECTED_BRIDGE_VERSION` = **7** (el bump a 8 viaja
con el proximo PBO).

## ⚠️ Trampas nuevas del cierre 20
- **`SetHandler` ≠ `SetUserData`**: `ui_click` solo ve handlers por `GetScript`/`GetUserData`. Un mod cuya UI quiera ser
  clicable por el verbo debe exponerse por user data (D-53).
- El log de **script del cliente no se vuelca en caliente**: para diagnosticar UI in-game no cuentes con sus `Print`
  hasta que el cliente cierre.
- `wait_for` solo tail-ea los logs del **perfil del servidor** del run gestionado.
- La caja se reocupa sola **y el juego ajeno sobrevive al lease**: mirar procesos `DayZDiag_x64` con su `-mod=` antes de
  planear cualquier in-game.
- `AddonBuilder -packonly` sobre `P:\DayZ_MCP` a un staging de `%TEMP%` tarda ~1 s y SI empaqueta `gui\layouts\*.layout`
  (verificado por strings dentro del PBO).
- **`cpu=0s` y un JSON de salida a 0 bytes NO significan que la lane de Grok este colgada** (correccion de una
  nota anterior de HOY, que era falsa y costo matar dos lanes buenas): el proceso `grok.exe` no acumula CPU
  medible (el trabajo va por I/O y por procesos hijo) y **el JSON solo se escribe al terminar**. La lane del
  addendum del vault marcaba `cpu=0s` / 0 bytes y ya habia escrito D-54, LL-317, el addendum de la nota de sesion
  y las filas de la matriz. Señal de vida correcta: mirar los EFECTOS (mtime de los ficheros que deberia tocar,
  o el `.err`), o simplemente esperar por el pid.

**Gate de arranque:** `Retomo DayZ_MCP desde: hoja de ruta CERRADA EN CODIGO (ciclos 0-7); ui_dialog fase 2 gateado in-game 11/13 con PBO CD22655F (fix SetUserData); suite real 1590 OK, clon 1427, repo publico 992899e; centinela F1B49714/136A6056; version 7 (bump a 8 aplazado, D-52) · proxima accion: con caja PROPIA, re-medir el caso 11 busy x3 (gate_regress.py), P1-02 con URL no-loopback, cadena ATM para lookback, dayz_test_stop con PID muerto, y con el arreglo (si lo hay) el PBO del bump v8 · OJO: SetHandler != SetUserData para ui_click; el log de script del CLIENTE no vuelca en caliente; wait_for solo lee logs del SERVIDOR; el juego de otra sesion sobrevive a su lease (no tocar)`

## Cierre 19 (18/08 00:30 → 02:20, LIVE-STATE anterior) — conservado integro

# DayZ_MCP — Estado vivo · snapshot 2026-08-18 02:2x (cierre 19 — ciclos 4 y 5 IMPLEMENTADOS por Grok en dos lanes (D/E), revisados a CIEGAS por subagente (0 P1 cada una), corregidos en r2 y PROMOCIONADOS; suite real **1566 OK**; pin local de CLIs HECHO en esta maquina; export del clon regenerado sin los JSON de seguridad; **repo publico PUBLICADO `c5bf220..2bbb4b4` (02:1x, con OK del usuario)**; daemon `stale: [loopback.py, server.py]` sin reiniciar (caja ocupada por navprobe ajeno))

ciclos_en_este_objetivo: 0 — objetivo vivo: **ciclo 6** (PBO siguiente: P1-02 loopback en `MCPBridge.c:160-172`, `ui_dialog` fase 2 con cable `dialog` anidado, regresion del ciclo 1, sondas host/`array<ref T>` in-game). Ciclos 0-5 CERRADOS en Python (5 = fase 1; fase 2 = ciclo 6). Ciclo 7 = `playbook_run`.

## 🎯 PROXIMA ACCION (una sola): ciclo 6 — el PBO siguiente (P1-02 loopback en `MCPBridge.c:160-172` + `ui_dialog` fase 2 con cable `dialog` anidado + regresion del ciclo 1 + sondas host/`array<ref T>`), una candidata, una sesion in-game; antes, si la caja esta libre, reiniciar el daemon stale y cerrar las dos verificaciones in-game pendientes

Antes de tocar el daemon o la caja: `bridge_status.coordination.active` + `server_peer.last_poll_age_s` + `Get-Process DayZDiag_x64` (a las 00:34 seguian vivos el servidor 28192 y el cliente 18900 de `ForzaDayZ\work\navprobe_run`, sin lease). Verificaciones in-game pendientes que caben en cualquier sesion con caja: `action_use → wait_for(lookback_lines=200)`; `dayz_test_stop` con PID muerto; y ahora tambien: reiniciar el daemon para que cargue `loopback.py`/`server.py` nuevos (whitelist `ui_dialog` + `_read_json` endurecido) — hasta entonces el daemon `8de8046e` sirve el codigo viejo (`warnings: daemon_module_stale`).

## ✅ Cerrado en el cierre 19 (18/08 00:30 → 02:2x) — detalle en `30_Sessions/2026-08-18-DayZ_MCP-ciclos-4-5-lanes-D-E.md`

- **Reparto por FICHERO, no por ciclo**: `ui_dialog` (ciclo 5) y P1-03 (ciclo 4) tocaban los dos `loopback.py` → lane E =
  `loopback.py` + `server.py` + `ui_dialog.py` (P1-03 + `ui_dialog` fase 0+1); lane D = instalador + publicacion + docs
  (P1-01 + P2-04). Workspaces `%TEMP%\mcp-lane{D,E}-20260818` (1047 ficheros, baseline 1507 con errors=3 = los tres tests
  de `test_install_mcp` que leian el manifiesto REAL de `reports\security\` — la evidencia de P1-01).
- **Lane D (Grok, $0,73 + r2 $0,94; revision ciega SOUND-WITH-NITS 0 P1 / 2 P2)**: P1-01 rediseñado — manifiesto de CLIs y
  fixture not-found pasan a `%LOCALAPPDATA%\DayZ_MCP\security\` (`DAYZ_MCP_SECURITY_DIR` override; sin fallback al arbol;
  `installer_cli_manifest_missing` con receta `run: python install_mcp.py --pin-clis`); `--pin-clis` (excluyente con
  `--register`; `--claude-exe/--codex-exe` o `shutil.which`; solo `.exe` nativo x64; escritura atomica; graba la sonda
  `mcp get p0s-absent-fixture-do-not-create` [+`--json` en CODEX] identica a la del proveedor; TOFU, drift nombra
  `--pin-clis`; `TimeoutExpired` tipado `installer_cli_probe_timeout`; sin fixture huerfano si falla la sonda);
  `INSTALLER_CLI_MANIFEST` → `installer_cli_manifest_path()`/`installer_not_found_fixtures_path()` a tiempo de uso; `p0s_gate`
  por el mismo resolvedor (create-only intacto); `main()` JSON lleva `detail` con la receta; boundary/included ya no publican
  los dos JSON; README **49 tools (+`exec_enforce`)** con catalogo medido por instanciacion + paso de pin ligado a
  `python install_mcp.py --register` (NO a `install-mcp.ps1`, que registra con `claude mcp add`/`codex.cmd mcp add` y no
  necesita pin); `README-mcp.md` con el pin. Suite ws **1522 OK (errors 0)**. Promocionado 01:4x
  (`_backups\20260818-laneD-promo`, 8 ficheros).
- **Lane E (Grok, $0,85 + r2 $1,07; revision ciega SOUND-WITH-NITS 0 P1 / 2 P2)**: P1-03 `_read_json` (`Content-Length`
  negativo/no numerico → 400 `bad_content_length`; > `MAX_BODY_BYTES` 1 MiB → 413 `body_too_large` SIN leer el cuerpo
  (handler HTTP/1.0, sin keep-alive); cuerpo corto → 400 `bad_body_length`; UTF-8 invalido → 400 `bad_json`; 7 tests de
  socket real). **`ui_dialog` fase 0+1**: contrato v1 en `plans/2026-08-18-ui-dialog-contrato-v1.md` (3 `kind`, `title`
  1..80, `message` 0..600 obligatorio en ack/confirm, `fields` 1..6 con claves exactas `{id,label[,required,default]}`,
  `id` `^[a-z][a-z0-9_]{0,31}$` unico, `timeout_s` 5..240, presupuesto +10 s ≤ 250 < `MAX_TIMEOUT_S`; **cable ANIDADO
  `{ok, dialog:{state,…}}`** porque `MCPResult.state` YA es `ref MCPPlayerState` (`MCPMessages.c:371`) — hallazgo de la
  revision ciega, decision **D-50**; Python aplana para el agente y pasa `id`/`_server`); modulo `tools/dayz_mcp/ui_dialog.py`
  (unica fuente de verdad: la tool y `validate_command_args` del daemon lo importan; el daemon devuelve el token fijo
  `bad_args`, sin texto del llamante en la auditoria; `isinstance` + `except TypeError` → nada de `TypeError` con entrada
  no hashable); `enqueue_bridge`/`probe_bridge_result`/`abandon_bridge` ADITIVOS en `Runtime` y `ClientRuntime` (linea a
  linea con `call_bridge`: identity, lease_token, `_STALE_LEASE_ERRORS`, `_public_enqueue_error`); espera con lock por
  sondeo + `sleep` fuera (tests miden: contendiente < 1 s con dialogo abierto; `tool_lock.locked() is False` en cada sleep;
  N+1 no encola; transporte ≠ `timed_out`; cancelled ≠ no); docstring/comentario «the only/Unique» → regla + ambas tools;
  `ui_dialog` en `CLIENT_COMMANDS`; catalogo 7.748 → 7.898 chars. Sondas a nivel de fuente en el contrato: (1) sin punto
  unico de creacion del host (ctor `MCPClientBridge.c:135-155` sin workspace; `ResolveUiRoot :1356-1370` si); (2) **si**:
  `MCPCommandBatch.commands` es `ref array<ref MCPCommand>` deserializado por `JsonSerializer.ReadFromString`
  (`MCPMessages.c:116-123`, `MCPClientBridge.c:326-329`), aunque `MCPArgs` aun no tiene ninguno anidado. Suite ws 1551
  (2 flakes de carga que pasan aislados: `candidate_wave` 12,3 s OK, `stale_heartbeat` OK). Promocionado 01:5x
  (`_backups\20260818-laneE-promo`, 7 ficheros, 3 nuevos).
- **Suite real completa tras ambas promociones: `Ran 1566 tests — OK (skipped=4)`** (1507 + 15 + 44).
- **Pin local HECHO en esta maquina** (`install_mcp.py --pin-clis --claude-exe … --codex-exe …` con las rutas del manifiesto
  antiguo; exit 0; sondas reales de `claude.exe`/`codex.exe`; `%LOCALAPPDATA%\DayZ_MCP\security\` con los dos JSON; los
  loaders promocionados los leen: roles CLAUDE/CODEX). Los JSON antiguos de `reports\security\` del arbol privado quedan
  sin leer (no borrados).
- **Export del clon regenerado**: `boundary.py` 207 ficheros / 4,04 MB / 0 hits privados (205 − 2 JSON + `ui_dialog.py` +
  `test_ui_dialog.py`; los 16 avisos de artifact-read son los conocidos); `export_public_repo.py` 207 copiados, re-scan 0
  hits; venv fresco; **suite del clon: 1403 / 0 / 0 / 29** (2ª corrida, 220 s; la 1ª dio 1403/0/1: el test nuevo
  `test_publish_boundary_excludes_installer_cli_pins` leia `tools/publish/included.json`, que no viaja al export → `skipTest`
  si no existe; real 45 OK, clon 45 OK skipped=1). `clone_cycle.ps1:45` abortaba en PS 5.1 con la primera linea de
  stderr del runner (aviso `pydantic_settings` del venv fresco + `$ErrorActionPreference='Stop'` = NativeCommandError):
  arreglado (EAP `Continue` alrededor del runner).
- Grok total de la sesion: **$3,59** (D 1,67 + E 1,92); revisiones ciegas: 2 subagentes Fable (~10 y ~16 min).

## ⏸ Pendiente por ciclo
- Publicacion: HECHA — overlay del export (207) al checkout + `git rm` de los dos JSON + suite del checkout **1403 / 0 / 0 / 29** (191 s) + commit `2bbb4b4` (14 ficheros, +2173/−104) + push `c5bf220..2bbb4b4` (OK del usuario). `diff_vs_repo.py`: solo `.gitattributes` en el repo, `ui_dialog.py`/`test_ui_dialog.py` nuevos.
- Ciclo 2/3b in-game: `action_use → wait_for(lookback)`, `dayz_test_stop` con PID muerto; reinicio del daemon (stale).
- Ciclo 6 (PBO): P1-02 + `ui_dialog` fase 2 (cable `dialog` anidado; `fields` como `array<ref T>` de ENTRADA en
  `MCPArgs` — sonda 2 dice que el serializador lo admite en `MCPCommandBatch`; si falla, aplanar en `ui_dialog.bridge_args`)
  + regresion del ciclo 1 + sonda 1 (donde crear el host oculto una vez).
- Ciclo 7: `playbook_run`.
- Buzon abierto (~13): sin cambios en este cierre (`-2b00`, `-833f`, `-4979`, `-c486`, `-959f`, `-dd53`, `-7d3e`, `-e8e9`,
  `-1a2b`, `-ecba`, `-01d0`, `-6dfb`/`-d6e4`, `-bb31`).
- Backlog nuevo: `orphan_guard.listener_pid_for_port` decodifica netstat como UTF-8 → con `PYTHONUTF8=1` en el entorno 3
  tests e2e (`test_port_reclaim` ×2, `test_client_credential_rotation_e2e`) fallan (byte 0xa2 OEM); mismo patron que el
  `_decode_console_bytes` de `doctor.py` (lane C). Real, no de las lanes.

## 🔒 Centinela + version gate
`test_task9_spawn_phase_markers` congela `MCPBridge.c` (`0ED14AF5076A2672…` / base `B86E79D357C8551…`), intacto en este
cierre (cero Enforce tocado). `MCP_BRIDGE_VERSION` = `EXPECTED_BRIDGE_VERSION` = 7. Solo se toca tras gate in-game.

## ⚠️ Trampas nuevas del cierre 19
- Reparto de lanes por conjunto de FICHEROS (interseccion de anclajes por grep ANTES de escribir los prompts): el
  hash-gate de `promote.ps1` solo avisa al final.
- Grok corre con `PYTHONUTF8=1`: los 3 tests de netstat OEM le salen rojos; repetirlos en shell propio antes de creerselos.
- `run_export_suite.py` bajo `clone_cycle.ps1` en PS 5.1: la primera linea de stderr mataba el script (arreglado).
- El JSON de salida de Grok via `1>` de PS 5.1 es UTF-16LE (BOM FF FE): leerlo con `utf-16`.
- `Wait-Process` en el tool de PowerShell no mata a los hijos desacoplados; `Start-Process powershell -Command '… *> …'`
  con comillas anidadas NO crea el fichero de salida: usar un wrapper `.ps1` con `-File`.

**Gate de arranque:** `Retomo DayZ_MCP desde: ciclos 0-5 CERRADOS en Python (4 = P1-01/P1-03/P2-04, 5 = ui_dialog fase 1 con cable dialog anidado D-50; ambos por Grok en lanes D/E + revision ciega + r2 + promocion); suite real 1566 OK; pin local hecho; export regenerado (207, sin JSON de seguridad) · repo publico 2bbb4b4 · proxima accion: ciclo 6 (PBO: P1-02 + ui_dialog fase 2 con cable dialog anidado + regresion ciclo 1) · in-game pendiente: reiniciar daemon stale, wait_for lookback tras action_use, dayz_test_stop con PID muerto · OJO: mirar coordination.active + peers + procesos DayZDiag ajenos antes de tocar caja o daemon`

## Cierre 18 (17/08 21:45 → 18/08 00:15, LIVE-STATE anterior) — conservado integro


# DayZ_MCP — Estado vivo · snapshot 2026-08-17 23:55 (cierre 18 — hoja de ruta RATIFICADA (D-48); ciclo 1 GATEADO in-game; ciclos 2 y 3b IMPLEMENTADOS por Grok, revisados a ciegas y PROMOCIONADOS; suite real 1507 OK; clon 1344/0/0; repo publico al dia; daemon reiniciado solo 23:54 con stale:[]; **sonda qwen3.5 sin recorte PASS 00:04 = ciclo 2 CERRADO**; siguiente = ciclo 4/5)

ciclos_en_este_objetivo: 0 — objetivo vivo: **ciclo 4** (Python sin `server.py`: P1-03 `loopback.py` Content-Length; P1-01 instalador con diseno; docs P2-04) y **ciclo 5** (`ui_dialog` fase 1, `server.py`, espera FUERA de `tool_lock`). Ciclos 0-3 CERRADOS. Ciclo 6 = PBO siguiente (P1-02 + `ui_dialog` fase 2 + regresion del ciclo 1); ciclo 7 = `playbook_run`.

## 🎯 PROXIMA ACCION (una sola): abrir la lane del ciclo 4 (Grok, `%TEMP%`, ficheros disjuntos de `server.py`) y, en paralelo o despues, la del ciclo 5 sobre `server.py`; ambas offline, sin caja

Antes de arrancar: releer el ciclo entero en `plans/2026-08-17-hoja-de-ruta.md` (:415-458 y :461-517) y comprobar sus citas
contra el arbol (`server.py` se movio: lane B lo reescribio el 17/08 23:44). Reparto probado esta noche: workspace copia +
finder parcheado + baseline (`make_ws.ps1`), Grok postura B desacoplado, revision CIEGA por subagente, `-r` para los nits,
`promote.ps1` con hash-gate. Verificaciones in-game pendientes que caben en cualquier sesion con caja: `action_use →
wait_for(lookback_lines=200)` sobre el ATM; `dayz_test_stop` con un PID muerto a proposito (lane C).

## ✅ Gate de producto del ciclo 2 — sonda qwen3.5:27b sin recorte: PASS (18/08 00:04, 548 s, catalogo 49 tools)

Secuencia del junior: `list_tools` → describe `session_status`/`bridge_status` → las llama y LEE `ready=false
reason=client_not_polling` → describe `player_teleport` (dice lease) → describe `session_acquire_wait` → **`session_acquire_wait`
(no `session_acquire`, ningun nombre inventado)** → `player_teleport` → `session_release` → 2 `pipeline_feedback` bien formados
(`-220212-74ea` "server vivo pero cliente sin sondear: ready=false y teleport ok=1" — matiz correcto: `ready` es pila
completa; `-220212-da2c` `cleanup_degraded:[audit_failed]`, preexistente). Los tres criterios del plan (:145-147) cumplidos.
⚠ **Efecto colateral archivado (`fb-20260817-220523-5a2a`)**: la caja estaba libre a las 23:54, pero a las 23:55:36 arranco un
run **navprobe** (`ForzaDayZ\work\navprobe_run`, fuera del lifecycle y SIN lease) y el `player_teleport` de la sonda movio a
SU jugador a [7150,0,7650] ~00:01 → la celda navprobe de esa hora es sospechosa. Refuerza `-7d3e` (listar DayZDiag ajenos).

## ✅ Cerrado en el cierre 18 (17/08 21:45 → 23:55) — detalle en `30_Sessions/2026-08-17-DayZ_MCP-ciclo1-gate-y-lanes-B-C.md`

- **Ciclo 0**: las 4 decisiones de la hoja de ruta ratificadas por el usuario (**D-48**). El chequeo del simbolo
  `DabsFramework` ya estaba hecho (cabecera de la hoja) y el gate lo confirmo: la rama dispara.
- **Ciclo 1 — gate in-game PASADO** (run `28f2e26f`, LFPowerGrid + @DayZ_MCP, PBO `BCA758A161B95058` 11/11 byte-identico
  a fuente, verificado entrada a entrada): `action_use(LFPG_ActionOpenBTCAtm)` → `started:1` + `[BTCOpenResponse]` +
  `[BTCAtmView] Opened`; `ui_tree(BTCAtmRoot)` (⚠ sin path da `no_menu` en hosts pre-creados); `ui_set_text(EditBtcAmount)`;
  **`ui_click(BtnBuyBtc)` → `clicked:1 handler=LFPG_BTCAtmView`** (cubo «rama Dabs dispara») → `[BTCTxResult] type=1 err=6`;
  `ui_set_text` TextWidget ok; `pos_real`: teleport a pie y=0 → 294.73, ATM/sedan flags=0 y=0 → 294.69/294.68, **teleport
  SENTADO = posicion nueva del sedan** (`[7150, 294.01, 7720]`, sedan a 0,20 m), infectado flags 3108 y=0 → 294.06;
  `declared_slots` del sedan 16 reales (BUG-066(c) cerrado); logs sin errores del bridge. **Centinela re-congelado**
  `0ED14AF5…` / `B86E79D3…` (7/7). Buzon `-5505`, `-f33e`, `-db17`, `-db76` resueltos.
- **Ciclo 3a**: flake create-vs-write arreglado (`test_bug046_startup_deadlock.py:1060-1085`, escritura atomica + espera
  de contenido); clon 3 corridas (ver abajo). `-283e` resuelto.
- **Ciclo 3b (lane C Grok, $1,10, 2 rondas, revision CIEGA por subagente: SOUND-WITH-NITS 0 P1)**: `process_lifecycle.py`
  clasifica PID owned/gone/foreign/unknown; `stop_run` suelta con PID muerto o ajeno y **nunca termina un foreign**;
  reaper retira all-gone-or-foreign; unknown sigue fail-closed (SC-009/F-07 restaurados); doctor
  `PROCESS_SCAN_DECODE_FAILED`. **D-49**. Promocionado 23:05 (`_backups\20260817-laneC-promo`). `-1d6e`, `-8711`, `-6ac8`
  resueltos; `-833f` sigue abierto (predicado en el worker SELLADO: `dayz_test_worker.py:647`, `dayz_test_readiness.py:153`).
- **Ciclo 2 (lane B Grok, $5,61, 2 rondas, revision CIEGA: SOUND-WITH-NITS 0 P1, 3 P2 cerrados en r2)**: verbos low-level
  de lease con `LOW-LEVEL: ` + `session_acquire_wait` «Preferred: » + alias `lease_acquire`; `wait_for` ok=satisfied,
  validacion `bad_args` por campo, timeout nombra `wait_for`, **`lookback_lines=200`** (0..2000); `lease_required` con
  receta; `bridge_status.ready {ready, reason ∈ ready|no_run|server_poll_stale|client_not_polling|client_legacy_blocked|
  version_mismatch}` (legacy/mismatch solo si ese peer ha sondeado); `game_not_ready` por peer OBJETIVO en embedded y
  cliente; linea de lease en toda mutacion; instructions con el flujo junior; `logs_since` empareja rpt+log por nombre;
  autospawn sin crash y una vez por racha (D-14 intacto); WebP MIME; `bad_args` con campo/limite. **Catalogo 8.973 →
  7.748 chars.** Promocionado 23:44 (`_backups\20260817-laneB-promo`). 15 ids del buzon resueltos.
- **Suite real completa: `Ran 1507 tests — OK (skipped=4)`** (tras ambas promociones). **Clon aprovisionado**: 1344 /
  0 / 0 / 28 (corrida 1; corridas 2-3 y suite del checkout: ver `## 🚀 Publicacion`).
- **Lane R (Grok solo-lectura, $0,20)**: audit de Codex re-verificado contra el arbol → 12 VALID / P2-05 OBSOLETE / P1-02
  fuera (Enforce → ciclo 6); arbitraje en `10_Projects/DayZ_MCP/reviews/codex-review-inbox.md`. Los VALID del ciclo 4
  sin `server.py`: **P1-03** (`loopback.py:1512-1524`) y **P1-01** (instalador, requiere diseno); P1-04/P2-06 ya en la lane B.

## 🚀 Publicacion — HECHA: `https://github.com/willy92wins/dayz-mcp` `31040de..c5bf220` (17/08 23:50)

Secuencia medida: `clone_cycle.ps1` (boundary 207 ficheros / 3,96 MB / 0 hits privados; export; venv como
`install-mcp.ps1`; suite en el clon **1344 / 0 / 0 / 28**) → dos corridas mas de la suite en el mismo clon (**1344/0/0 las tres**:
criterio 3a de la hoja cumplido — 3 corridas completas sin el flake) → overlay `robocopy /E` al working tree del repo (40 entradas:
+13 ficheros nuevos = playbooks, `inbox.py`, 5 tests) → suite sobre el checkout **1344 OK (skipped=28)** → commit
`c5bf220` → push. `diff_vs_repo.py`: repo 195 → 207 (+ `.gitattributes` solo en el repo, correcto). Los 16 avisos de
`artifact-read` de `boundary.py` son los conocidos (native-launchers y evidencias fase1-3 excluidos por diseno).

## ⏸ Pendiente por ciclo
- Ciclo 2: sonda qwen3.5 (arriba). Verificar in-game `action_use → wait_for(lookback)`.
- Ciclo 3b: verificar in-game `dayz_test_stop` con un PID muerto a proposito; el daemon debe haber cargado el modulo
  (el de las 23:23 ya lo tiene: `stale` no lo lista).
- Ciclo 4: P1-03 + P1-01; docs P2-04 (README dice 39 tools, hay 48 + `exec_enforce`).
- Ciclo 5: `ui_dialog` fase 1 (Python; espera FUERA de `tool_lock`, patron de `wait_for`).
- Ciclo 6: PBO con P1-02 (loopback en `MCPBridge.c:160-172`) + `ui_dialog` fase 2 + regresion del ciclo 1.
- Ciclo 7: `playbook_run` (solo tras qwen PASS).
- Buzon abierto (~13): `-2b00` TTL (= D-15, decision), `-833f`, `-4979` .git roto, `-c486` exclusion de arbol, `-959f`,
  `-dd53`, `-7d3e`, `-e8e9`, `-1a2b`, `-ecba`, `-01d0`, `-6dfb`/`-d6e4` (timeout con peer stale sondeado), `-bb31` aparcado.

## 🔒 Centinela + version gate
`test_task9_spawn_phase_markers` congela `MCPBridge.c` (`0ED14AF5076A2672…` / base `B86E79D357C8551…`), re-congelado sobre
el estado gateado el 17/08 22:2x. `MCP_BRIDGE_VERSION` = `EXPECTED_BRIDGE_VERSION` = 7. Solo se toca tras gate in-game.

## ⚠️ Trampas nuevas del cierre 18
- La caja se reocupa sola: a las 23:23 otra sesion reinicio el daemon y tomo lease con un servidor sondeando mientras yo
  iba a reiniciarlo. Antes de tocar el daemon: `bridge_status.coordination.active` + `server_peer.last_poll_age_s`.
- `Start-Process -ArgumentList` no entrecomilla rutas con espacio: `--cwd "…\DayZ Projects\…"` se parte y grok aborta
  («--prompt-file cannot be used with [PROMPT]»). Entrecomillar el elemento a mano.
- Lanzar Grok DESACOPLADO del tool (10 min de tope); esperar por pid; leer su JSON solo cuando el pid ha muerto (el
  fichero queda bloqueado mientras corre).
- Un `python - <<EOF` desde el Bash tool cae al REPL y llena el contexto de tracebacks: script a fichero.
- Workspace delegado = copia con jerarquia (`<ws>\DayZ Projects\{DayZ_MCP_dev,DayZ_MCP}`) + finder editable parcheado
  (`String.Replace`, no `-replace`) + baseline de hashes; receta en `scratchpad\make_ws.ps1` de esta sesion (rehacer si hace
  falta: 40 lineas). Promocion por `promote.ps1` (hash-gate destino == baseline, backup sha, escritura unica, verify).

**Gate de arranque:** `Retomo DayZ_MCP desde: hoja de ruta ratificada (D-48); ciclos 0-3 CERRADOS (ciclo 1 gateado in-game run 28f2e26f, centinela 0ED14AF5/B86E79D3; ciclo 2 promocionado + qwen3.5 PASS; ciclo 3 promocionado, D-49); suite real 1507 OK; repo publico c5bf220; daemon con stale:[] · proxima accion: ciclo 4 (P1-03 loopback.py, P1-01 instalador, docs P2-04) y ciclo 5 (ui_dialog fase 1) con Grok en %TEMP% + revision ciega + promote.ps1 · in-game pendiente: wait_for lookback tras action_use, dayz_test_stop con PID muerto · OJO: la caja se reocupa sola (navprobe sin lease): mirar coordination.active + peers antes de mutar`


## Cierre 17 (17/08 17:40 → 21:15, LIVE-STATE anterior) — conservado integro

# DayZ_MCP — Estado vivo · snapshot 2026-08-17 17:40 (cierre 16 + **cierre 17** encima — gate de publicacion verde salvo centinela, PBO CON `pos_real` YA DESPLEGADO, plan `ui_dialog`; HANDOFF COMPLETO — 3 ENTRADAS POR TRIAR: audit Codex + plan weak-agent Grok + Gemini; `action_use` y fix `pos_real` en arbol SIN gate)

ciclos_en_este_objetivo: 0 — objetivo vivo: **sesion de decision** sobre las 3 entradas de hoy (audit Codex, plan weak-agent de Grok, Gemini) + buzon (36): separar grano/paja → decidir → plan. En paralelo, pendiente operativo: gate CONJUNTO `action_use` + `pos_real` (`fb-20260817-110222-5505`) — **ya NO bloqueado: el PBO esta construido y desplegado, y la caja esta libre** (ver cierre 17 mas abajo).

## 🎯 PROXIMA ACCION (una sola): sesion de revision — triar las 3 entradas + buzon, separar grano de paja, decidir, plan

El usuario reunio «a los vengadores» (Grok, Codex, Gemini) y probo con consumidores reales (clientes IA). Todo esta
**procesado y archivado, NADA decidido** (asi lo pidio: revisar en la siguiente sesion). Entradas, con lectura preliminar
de Claude marcada como tal:

1. **Plan weak-agent (Grok TUI, «decidido con el usuario, pendiente de implementacion»)** —
   `plans/2026-08-17-weak-agent-consumer-ux.md`. Medido con gemma4/qwen3.5/qwen3.8 en dos rondas (catalogo 48 tools =
   10.318 chars; recorte a 6k deja 21 visibles). Paquete: **P0** sin tools nuevas (errores-receta: `lease_required` →
   «call session_acquire_wait(purpose=…)», `version_blocked`/peer stale → `game_not_ready` + motivo, `wait_for` en timeout
   `ok:false` y que nombre `wait_for`; un solo verbo de lease visible — esconder o marcar LOW-LEVEL `session_acquire`/
   `session_wait`/`session_cancel`/`session_heartbeat`; `bridge_status.ready {ready, reason∈enum}`); **P1** alias
   `lease_acquire`→`session_acquire_wait`; **P2** tool `playbook_run(name, params)` sobre el runner. Fuera: `search_tools`,
   lease implicito, cookbook en descriptions, orquestador delante. Gate de producto: qwen3.5 sin recorte en PASS. Contrato
   hacia adelante con nombres exactos + tests minimos + rollback (todo additive). Reparto sugerido: Codex (G7) sobre
   `server.py` — hoy caliente (sesion UI verbs). Ids del buzon enlazados dentro.
2. **Auditoria estatica del repo publico (Codex, presupuesto extraordinario)** —
   `reviews/2026-08-17-codex-technical-handoff-repo-audit.md` (+ `.docx`). Snapshot `31040de` (16-08): **el arbol ya se
   movio** (playbooks, `uid`, `entities_query`, `action_use`, cookbook; 39→48 tools) → re-verificar cada [C-xx] contra el
   arbol actual. Registro: P1×4 (instalador no portable + fixtures con rutas/hashes/inventario de la maquina; bridge
   SERVIDOR sin validar URL loopback; `Content-Length` sin acotar; WebP con MIME PNG), P2×7 (CI/releases/lock/docs;
   `GetFirstHuman`; `wait_for` pattern vacio/value negativo/timeout<=0; `save_dir` arbitrario), P3×3. §7-8: `dayz_ready`,
   simple/expert, **auto-lease por mutacion**, envelope con `remediation`, Tasks/Resources/Prompts, `scene_apply`/
   `scenario_run`/refs opacas, tools de vehiculo, perfil admin, observabilidad; roadmap Fases 0-5; primer PR «hardening».
   Lectura preliminar: grano probable P1-02/P1-03/P2-06 (baratos; **P1-02 es Enforce → agruparlo con el rebuild+gate
   pendiente**), P1-01 (privacidad; re-verificar contra lo que la sesion de export ya arreglo hoy), P1-04; desfasado P2-05
   (batch 6 ya puso `uid`); **choques con decisiones tomadas**: auto-lease (§7.4) vs «no lease implicito» del plan
   weak-agent; catalogo de tools de intencion (§8) vs council 36 «guardas = datos» y D-39 «por demanda».
3. **Gemini Flash 4.6 (debut, ciego)** — `reviews/2026-08-17-gemini-flash46-ai-native-pack.md`. No leyo el repo: inventa
   un MCP de busqueda de API Enforce con 4 tools inexistentes → paja, salvo 3 ideas: `llms.txt` en la raiz, schemas
   cerrados para modelos pequenos (misma linea que el plan weak-agent), `system_prompt.md` de consumidor.

**Convergencias a la vista** (para la sesion de decision, no decididas): `dayz_ready` (Codex) ≡ `bridge_status.ready` (Grok)
≡ `fb-…-013638-1b14`/`-104756-aa92`; envelope con `remediation` (Codex) ≡ errores-receta (Grok); `wait_for` (Codex P2-06 +
Grok P0) ≡ `fb-…-104756-5640`/`-111108-0c77`. **Divergencias**: auto-lease si/no; playbooks (datos) vs `scenario_run`/`scene_apply`
(tools); tamano de la superficie (Codex: dos superficies; Grok: recortar). Buzon: 36 sin resolver, la mitad son de estas
mismas sondas. Vault: `10_Projects/DayZ_MCP/reviews/codex-review-inbox.md` (entrada del 17-08, sin arbitrar; scorecard NO
volcado hasta arbitrar hallazgo a hallazgo).

## ✅ Cerrado por cierre 17 (tarde) — gate de publicacion + PBO listo + plan `ui_dialog`

**El PBO ya esta reconstruido y desplegado**, con TODO dentro. ⚠ **SUPERADO a las 19:39:34 por la sesion de LFPowerGrid**, que parcheo `InvokeUiClick` para Dabs y reconstruyo: **PBO vigente 167.554 B, sha `BCA758A161B95058`, 11/11 entradas byte-identicas a fuente** (verificado por esta sesion), con `MCPClientBridge.c` = `A6C80927F854…`. **Sigue SIN compilar**: empaquetar no es compilar. El ciclo 1 (build + gate) lo conduce ESA sesion; no tocar `MCPClientBridge.c` ni el PBO desde aqui. El PBO anterior era `167.195 B`, sha
`9EFC11DEA8852CEF`, en `P:\Mods\@DayZ_MCP\addons\` (junction al `!Workshop`, ambas vistas ya
lo sirven). Verificado por las **11 entradas byte-identicas a su fuente en disco**, no por un
"Build Successful": `MCPBridge.c` empaquetado hashea `0ED14AF5076A`, que es el valor exacto que
el centinela reportara al re-congelarse. Lleva `pos_real`, `action_use`, el fix de
`clicked`/`not_handled`, y ademas `ui_set_text` sobre `TextWidget` (nuevo, ver abajo).
Backup del anterior en `_backups\20260817-pbo-posreal\`.

**La caja esta libre**: cero procesos DayZ, puerto 8765 sin listener. El servidor suelto
`30bb95a2` que mencionaba cierre 16 ya no existe. El gate in-game se puede lanzar en frio.

**Gate de publicacion: de rojo a "solo el centinela".** Clon del export **1307 / 2 fallos**,
arbol dev **1470 / 2 fallos**, frontera **206 ficheros / 0 private hits**, export 206/0/0. Los
2 que quedan son las dos mitades del centinela. Seis arreglos, todos verificados DENTRO del
clon aprovisionado y no solo en el arbol:

1. `playbooks/` entra en la frontera (7 ficheros). Requirio arreglar un
   `Path(rel).relative_to("tools")` en `boundary.py` que revienta con cualquier `.py` incluido
   fuera de `tools/`. Cierra `fb-20260817-003625-587f` y `fb-20260817-094010-415e`.
2. BUG-076: el `Literal` de `camera_set` tenia **tapiada su propia rama `bad_args`** — la
   validacion correcta existia y pydantic rechazaba antes de que el cuerpo corriera.
3. `test_batch6.py` resolvia el addon a mano; ahora usa `tests._addon_paths.addon_root()`.
4. `test_dayz_test_tool.py:593` leia la policy del host y afirmaba la lista de proyectos del
   AUTOR; ahora alimenta un fixture. Era el unico ERROR del clon.
5. `playbooks/runner.py` llevaba 3 rutas absolutas de esta maquina (disparaban el guard
   fail-closed del export); derivadas de `Path(__file__).parents[1]/"tools"`.
6. `entities` entra en `PRUNABLE_FIELDS` (era el unico de los 13 miembros `ref` de `MCPResult`
   que faltaba) y `ui_set_text` acepta `TextWidget`. **Los dos declarados en el changelog de
   `product-spec.md`**, que viaja dentro de la frontera. Cierra `fb-20260817-094001-4267`.

**NO se ha publicado, por decision del usuario.** El `.gitattributes` del repo existe
precisamente para que los tests de bytes pasen en un clon: ahi se espera verde, no rojo
documentado. Push unico tras el gate. Repo limpio en `31040de`; el delta seria 195 -> 206.

**Dos trampas nuevas encontradas y archivadas** (las dos valen mas que el bug):
- `clone_cycle.ps1` — el gate mismo — resolvia sus scripts desde el scratchpad de una sesion
  de agosto que **sigue en disco**. No fallaba: corria un `boundary.py` viejo y daba verde
  sobre un arbol que no habia medido. Arreglado con `$PSScriptRoot`. `fb-20260817-133417-f82b`.
- El test que fija `PRUNABLE_FIELDS` decia estar *"anchored to MCPMessages.c"* y comparaba dos
  constantes Python. Por eso `entities` falto semanas en verde. Ahora deriva del `class
  MCPResult` real y se falsifico con 4 sondas. `fb-20260817-134413-dd8e`.
- Flake bajo carga sin arreglar: `test_runtime_root_lock_...` lee vacio un fichero de senal.
  Medido **1/3** con suite completa, **0/6** aislado. Viaja al repo publico.
  `fb-20260817-152308-283e`.

**HOJA DE RUTA de todo lo pendiente**: `plans/2026-08-17-hoja-de-ruta.md` (council con Grok, $1,42, cabecera de arbitraje del orquestador). Ocho ciclos; **solo dos consumen sesion in-game**. Techo de **dos lanes de escritura** con conjuntos de ficheros disjuntos: todo lo que toca `server.py` es UNA lane y un orden. Cierra cuatro decisiones, entre ellas que **el auto-lease de Codex pierde** frente al lease explicito.

**Bug que bloquea la caja** (`fb-…-174116-8711`): un run con todos los pids muertos no lo barre el reaper y devuelve `active_run_exists` a cualquier lanzamiento. `reap_dead_run` existe para ese caso exacto y **no esta expuesta como tool**.

**Plan nuevo, aprobado y sin implementar**: `plans/2026-08-17-ui-dialog-plan-fusionado.md` —
interfaces tipo (`acknowledge` / `confirm` / `form`) en un solo verbo `ui_dialog`. Council de
tres lanes ciegas (Grok, Fable, ChatGPT) con **ocho coincidencias independientes**. Lo que hay
que saber sin abrirlo:
- **El agente** consume la respuesta por MCP. Nada de RPC cliente-servidor ahora.
- **Host pre-creado y oculto, NO `UIScriptedMenu`**: el contrato de esa clase es cargar el
  layout en cada instanciacion (`bookmenu.c:13`, `cameratoolsmenu.c:141`, `chatinputmenu.c:16`).
- **Cero protocolo nuevo en Enforce** para la espera: se reutiliza `postNow=false` + job.
- ⚠ **El hallazgo caro**: todos los verbos hacen `async with runtime.tool_lock:
  call_bridge(...)`. Un dialogo de 60 s escrito asi **congelaria el daemon multi-sesion**. La
  unica excepcion existente lo dice en su docstring (`server.py:1123-1128`). El cambio es solo
  Python. Aplica a CUALQUIER verbo futuro que espere a un humano.
- La UI en caliente (`ui_open`/`ui_close` + linter de layout por rects) queda **aparcada** a
  proposito: `fb-20260817-151939-bb31`.

Detalle: `30_Sessions/2026-08-17-DayZ_MCP-gate-de-publicacion-y-council-ui-dialog.md`.

## ⏸ Pendiente operativo (bloqueado por rebuild; lo conduce quien tenga el PBO): gate in-game de `action_use` + `pos_real`

Verbo nuevo **`action_use`** aterrizado y empaquetado hoy 12:11 (⚠ **SUPERADO por cierre 17: el PBO vigente es el de las 16:04, 167.195 B, sha `9EFC11DEA8852CEF`, y ya lleva `pos_real` dentro**; el de 12:11 era 166.318 B y contenia
`action_use`, `DispatchActionUse`, `setup_failed`). Hace que el jugador ejecute una accion de usuario sin
teclado — es lo que faltaba para ABRIR una UI de mod (los verbos `ui_*` solo sirven con la UI ya abierta).
**NO esta verificado in-game**: el gate real de Enforce es arrancar, y no se ha arrancado con esto dentro.
Ese mismo PBO lleva ademas el fix de `clicked`/`not_handled` de `ui_click` de las 11:34, que el PBO de las
11:21 no tenia.

Dos condiciones de entorno (no de codigo) para poder gatearlo:
1. **Sesion MCP nueva**: el verbo no aparece en la lista de tools de una sesion abierta antes del reinicio
   del daemon (reinicio 12:11, generacion nueva, `modules stale: []`).
2. **Los DOS mods a la vez**: ningun perfil los carga juntos (`DayZ_MCP` tiene `default_base_mods: []`,
   `LFPowerGrid` no incluye `@DayZ_MCP`). Unica combinacion dentro de la policy sellada:
   `dayz_test_run(project="LFPowerGrid", extra_mods=["@DayZ_MCP"])` — ese perfil ya tiene `P:\Mods` y los
   dos roots de CF.

Cadena que cierra el escenario (el `wait_for` **no es opcional**: `started:true` = despachado, no ejecutado;
el RPC del ATM nace del evento de animacion, `animatedactionbase.c:199`):
`action_use(action="LFPG_ActionOpenBTCAtm", classname="LFPG_BTCAtmAdmin", radius=5)` →
`wait_for(log_matches, "[BTCOpenResponse]")` → `ui_tree` → `ui_set_text("EditBtcAmount")` →
`ui_click("BtnBuyBtc")` → `wait_for(log_matches, "[BTCTxResult]")` y leer `err=`.

⚠ ~~Hay un servidor suelto corriendo~~ **RESUELTO en cierre 17: cero procesos DayZ y puerto 8765 libre.** (Era `30bb95a2`, @DayZ_MCP **sin** LFPowerGrid, efecto colateral de
`pack_only=true`): **no sirve** para este test, hay que pararlo y relanzar con la combinacion de arriba.
⚠ Riesgo abierto: si `Type().ToString()` devolviera otro formato en runtime, el lookup por nombre
fallaria siempre con `action_not_found`. Solo lo cierra el arranque.

Detalle: `30_Sessions/2026-08-17-DayZ_MCP-action-use.md`. Buzon: `fb-20260817-103305-db76`.

**En el MISMO gate va el fix de `pos_real`** (`fb-…-094207-f33e`, aplicado 13:05 a `MCPBridge.c`, sha `0ED14AF5076A2672`,
75.184 B, backup `_backups\20260817-posreal\`): `DispatchPlayerTeleport` reporta `veh.GetPosition()` tras `SetTransform` (la del
ocupante va un frame por detras) y `ValidateSpawnArgs` resuelve `y==0` → `SurfaceY` antes de `CreateObjectEx` (la colocacion de la
IA es diferida y el readiness la leia a y=0). Contratos offline verdes (`test_player_teleport` +1, `test_world_spawn_ground_contract`
nuevo); **el centinela `test_task9_spawn_phase_markers` esta ROJO A PROPOSITO** (2 fallos = protocolo, nota en el test) hasta gatearlo.
**~~El PBO de las 12:11 NO lleva este fix → rebuild `-packonly` antes del gate~~ HECHO en cierre 17: reconstruido y desplegado 16:04, anclas verificadas dentro del PBO** (anclas: `applied = veh.GetPosition()`,
`y = GetGame().SurfaceY(x, z)`, `DispatchActionUse`) y anadir al arranque: (a) teleport SENTADO en CivilianSedan → `pos_real` =
posicion NUEVA (vs `query_all_players`); (b) `world_spawn ZmbM_CitizenASkinny_Blue` flags=3108 y=0 → `pos_real.y` ≈ superficie;
(c) regresion sedan flags=0 y=0 y teleport a pie y=0. Al pasar todo: re-congelar las DOS mitades del centinela sobre el
`MCPBridge.c` gateado. Peticion archivada: **`fb-20260817-110222-5505`**.

## ✅ GATE AGRUPADO DEL BATCH 6: PASADO (17/08 11:30-11:40, run `dd0a01cc`, servidor+cliente diag 1.29.163709)

Sobre el PBO `9BEA4C6D1D67AA33` (160.592 B, desplegado 11:21 por OTRA sesion — la de los *UI verbs* — con los
10 ficheros del arbol byte-identicos dentro, bridge **v7**, cero `PluginDeveloper`; daemon reiniciado 11:20:50 tras
el edit de `loopback.py`/`server.py`/`session_coordination.py` de las 11:19:44). Nada de esto lo hice yo: lo verifique.

| Item | Resultado |
|---|---|
| teleport sin uid, y=0 | `pos_real` y=294.73; `query_all_players` confirmo [7100.06, 294.75, 7699.81] |
| teleport con uid / uid falso | ok / `player_not_found` |
| teleport SENTADO en CivilianSedan | coche+jugador movidos (sedan en destino por `entities_query`, `in_vehicle` sigue 1) — **`pos_real` VIEJO** (posicion previa) |
| inventory_give inventory / hands / hands otra vez | ok / ok / **`hands_occupied`** |
| notify_players uid / broadcast / uid falso | `sent:1` + popup en captura del cliente / idem / `player_not_found` → vanilla `SendNotificationToPlayerIdentityExtended` **SI notifica desde server** (conflicto con la medida de ControlPlane resuelto: lo que no notificaba era el NotificationSystem local) |
| entities_query | `count_total` 38/7/19/18, ascendente por distancia, **read-only sin lease** (READ_ONLY_COMMANDS lo lleva desde 11:19) |
| infectado `ZmbM_CitizenASkinny_Blue` flags 3108 | recorrio 12,7 m en ~16 s hacia el jugador y le pego (health 0.695→0.483): **camina y ataca** — `pos_real` del spawn devolvio y=0 aunque asento en y=291.97 |
| wait_for | `log_matches 'OnStoreLoad SUCCESS'` 56,6 s / 28 sondeos; `players_at_least 1` al primer sondeo |
| script log servidor | cero errores del bridge (los 3 `ok=0` son los negativos intencionados) |

Centinela **re-congelado** (`tests/test_task9_spawn_phase_markers.py`, las DOS mitades: `1066AF81…`/`573D1D78…`) → 7/7.
Suite completa: `Ran 1467 · 3 fallos · 4 skipped` — **los 3 fallos son AJENOS**, del trabajo en curso de la otra sesion
(UI verbs: `test_constants_and_read_only_set_are_exact` por `ui_tree` en READ_ONLY, `test_prunable_fields_match_the_bridge_result_class`
por `ui`/`clicked`/`handler`/`user_id` en MCPResult, `test_camera_set_look_at_alias…` por el `Literal` nuevo de `cam_mode`). NO los toques:
tienen dueno. Cierre limpio: `session_release` con `cleanup_degraded:["audit_failed"]` (preexistente, release correcto), `dayz_test_stop`
ok, cero procesos DayZ, `session_status` sin owner ni cola.

## 📚 Docs cookbook aterrizadas (17/08 13:15, Grok escritor $0,06 + receptor; backups `_backups\20260817-docs-cookbook\`)

`tools/README-mcp.md`: doctor con `--daemon-policy normal` (firma real), listas *requires a lease / no lease*, `## Cookbook`
(Wait for a player / Spawn safely / Report friction or a bug), chat no expuesto (.ADM manual), `version_blocked` con juego
apagado = sin peers, `lease_required` → `session_acquire_wait`. `playbooks/README.md`: `--live` = cliente del daemon (puede
autospawnearlo), juego apagado → S1 `version_blocked` ~13-15 s exit 1, DRAFT no certifica dosel, S4 no ve solidos laterales.
`place_safely.toml`: solo `summary` + comentario. `test_playbook_runner` 22/22, fixtures 4/4 exit 0.
**Pendiente en `server.py`** (no lo toque: lo edita la sesion de UI verbs): lote de docstrings `fb-20260817-110444-be93`.

## 📥 Buzon: 69 entradas, **35 sin resolver** (15:55) — hoy 29 resoluciones con evidencia + 4 altas mias

Lo mio pendiente ahi: `fb-…-110222-5505` (gate conjunto), `fb-…-110444-be93` (docstrings server.py), `fb-…-094207-f33e`
(pos_real, cerrable con el gate), `fb-…-135446-9fd0` (aguja `SurvivorM_` falla con personaje femenino: usar `'Survivor`). El resto
es casi todo `server.py`/`doctor.py` (weak-probe: `ready`, `lease_required` con pista, `wait_for ok:true` en timeout, catalogo de 48
tools, `session_acquire` vs `_wait`, autospawn, doctor encoding/detail, `bad_args` sin campo ni limite — me mordio 3 veces con
titulos >120) + lifecycle (`dayz_test_stop` imposible con pid muerto `fb-…-121035-1d6e`; TTL 120 s en celdas largas de LFHeli;
clave stale invisible `fb-…-114601-dd53`) + export (`playbooks/` en la frontera). **No tocar `server.py` mientras la sesion de UI
verbs este viva** (edit suyo a las 15:30; colision real el 17-08 04:02): re-tomar hashes justo antes de escribir — hoy ese gate
aborto dos veces a tiempo (HANDOFF reescrito por la otra sesion a las 12:38 y mergeado, no pisado).

## 🧪 Suite y arbol al cierre 14 (15:55)

- Suite completa del arbol dev: **Ran 1470 · 2 fallos (SOLO el centinela, a proposito) · 4 skipped (15:58)**. Los 2 fallos del centinela son MIOS y a proposito (fix pos_real sin gate).
- Otras sesiones esta tarde (buzon): `test_batch6` pasado a `addon_root()` (`fb-…-134413-dd8e`, portable al clon), `camera_set`
  de vuelta a `str` con su rama `bad_args` viva (`fb-…-133417-5f6d`), gate de publicacion `clone_cycle.ps1` atado a `$PSScriptRoot`
  (`fb-…-133417-f82b`). No son mios: los cito para que el que retome no los rehaga.
- `MCPBridge.c` `0ED14AF5076A2672` (13:00) intacto; PBO desplegado `F5A313436D8F7052` (12:11, sin el fix); `server.py` mtime 15:30
  (otra sesion); cero procesos DayZ; mi lease liberado; `session_status` sin owner ni cola.

**Gate de arranque:** `Retomo DayZ_MCP desde: gate del batch 6 PASADO (run dd0a01cc); fix de pos_real y action_use EN ARBOL sin gate in-game (MCPBridge.c 0ED14AF5, centinela ROJO a proposito, PBO desplegado F5A31343 sin el fix); 3 entradas de los vengadores PROCESADAS y SIN TRIAR (audit Codex reviews/2026-08-17-codex-technical-handoff-repo-audit.md, plan weak-agent plans/2026-08-17-weak-agent-consumer-ux.md, Gemini reviews/2026-08-17-gemini-flash46-ai-native-pack.md) · proxima accion: SESION DE DECISION — leer las 3 entradas + buzon (36), separar grano/paja, decidir los choques (auto-lease si/no; playbooks-datos vs tools de intencion; alcance del hardening P1) y escribir el plan; en paralelo queda el pendiente operativo del gate CONJUNTO action_use + pos_real (fb-20260817-110222-5505) para quien haga el rebuild · OJO: 3-4 sesiones comparten el arbol (hash-gate antes de escribir; server.py es de la sesion de UI verbs) · SP-287 pending en skill-patches-pending.md (invariantes Enforce del gate)`

## ✅ Aterrizado y certificado offline (17/08 madrugada) — suite `Ran 1463 · 2 fallos (centinela) · 4 skipped`

- **Batch 6** (Grok escritor, $0,42, 2 rondas, foto de hashes; receptor cazo 4 defectos):
  `player_teleport`/`inventory_give` **sin `PluginDeveloper`** (`PluginDeveloper` solo se registra bajo
  `DIAG_DEVELOPER`, `pluginmanager.c:65,:247-252` → muerto en release; patron sustituto = VPP
  `SetPosition` / `CreateInInventory`/`CreateInHands`) · **`uid` opcional** en teleport/give/notify
  (`FindHumanByUid` por `GetPlainId`; `player_not_found`) · verbo nuevo **`entities_query(pos, radius
  0<r<=200, limit<=128)`** sobre `GetObjectsAtPosition3D`, nearest-first + `count_total` · notify por
  `uid` (vanilla, `notificationsystem.c:141-151`; null = broadcast) · `IsAllowedSpawnFlags` admite
  `ECE_CREATEPHYSICS` (VPP spawnea IA con `INITAI|CREATEPHYSICS`) · **bridge 6→7** en `MCPMessages.c:1`
  y `core.py:17`. Backups: `_backups\20260817-batch6\`.
- **Buzon del pipeline** (Grok, $0,20): `dayz_mcp/inbox.py` + tools `pipeline_feedback` /
  `pipeline_inbox` / `pipeline_resolve`. Append atomico de una syscall a
  `%LOCALAPPDATA%\DayZ_MCP\inbox\feedback.jsonl`, ids `fb-<fecha>-<hora>-<hex4>` sin contador
  compartido (12 entradas en el mismo segundo sin colision), historial append-only (resolver = otra
  linea). **Funciona sin daemon y sin juego.** El hook `session-state-injector.ps1` lleva ahora la
  linea 7 de conducta + contador de pendientes al arrancar (mudo si vacio). Backup `_backups\20260817-buzon\`.
- **Diccionario de playbooks** (Grok, $0,23): `DayZ_MCP_dev\playbooks\` — `README.md` (indice +
  ciclo DRAFT→PROBED→CALIBRATED→FROZEN), `place_safely.toml` (formato corrida 36: paso de 5 campos,
  ops en whitelist, calibracion con procedencia; **uncalibrated nunca gatea, degrada a WARN**),
  `runner.py` (stdlib; `--fixtures`/`--live`; exit 0/1/2), 4 fixtures rojo/verde,
  `tests/test_playbook_runner.py` (22). Va al **repo publico** (decision usuario) — la peticion de
  frontera del export esta en el buzon (`-003625-587f`).
- **Fixes de receptor** (mios): `test_vehicle_trace_contract` pin v6→v7; `test_player_teleport` al
  contrato nuevo (+ prohibe `PluginDeveloper`); `test_inventory_give` reescrito por Grok en ronda 2.

## 📥 Buzon: 40 entradas, 38 sin resolver — triaje pendiente (todo esta AHI, no aqui)

Cuatro escritores distintos en una noche (yo, sonda A read-only, sonda B con shell que archivo sus
17 con sus manos, y **otra sesion tuya que nadie aviso** con 7 entradas de uso real). Las que mas
muerden, confirmadas ×2: `entities_query` responde **`lease_required`** siendo lectura (falta en
`READ_ONLY_COMMANDS`; rompe `place_safely` S4) · docstring de `wait_for` **sin enum** de `condition`
(un agente frio se come `bad_args`) · doctor: el README pide `--json` sin `--daemon-policy` (falla) y
`PROCESS_SCAN_FAILED` es encoding, no proceso · el autospawn del cliente cobra 5-12 s al primer toque
· 4 altas de documentacion (chat no mencionado en ninguna tool; `world_spawn` no enlaza
`place_safely`; README-mcp no es cookbook). Veredicto de la sonda fria: **T1/T2 no cerrables por un
agente frio hoy**. `pipeline_inbox(limit=40)` para verlas.

## 🧭 Decisiones de la noche (corrida 36 del council + usuario)

- **El multitool ES el MCP**: primitivas + playbooks encima; GameMaster/verify/sesiones = consumidores.
- **ControlPlane NO se pliega**: se congela como producto durmiente (unico artefacto retail-verificado
  de footprint cero, topologia outbound); reabre solo con demanda de terceros.
- Guardas de seguridad = **playbooks, no tools** (`product-spec.md:107-108`).
- **Congelar TEMPRANO**: el playbook nace estructurado y ejecutable; la prosa fue el PASS falso.
- Grants por agente: no ahora, pero **precondicion dura** antes de que un bucle desatendido tenga la
  superficie mutante con una key.
- Medidas 16/08 noche: el motor SI entrega chat al script del server dedicado (via CF; uid vacio);
  `-adminlog` SI produce `.ADM` con id estable — observacion cero-Enforce (verified-apis.md).

## 🔒 Centinela + version gate (leer antes de tocar el bridge)

`test_task9_spawn_phase_markers` congela el sha del bridge y esta **rojo a proposito** desde el batch 6.
Re-congelar SOLO tras el gate in-game, las dos mitades. `MCP_BRIDGE_VERSION` y `EXPECTED_BRIDGE_VERSION`
suben juntas o no suben (test `test_bridge_versions_move_together_to_v7`).

---
## Cierre 9 (16/08 noche, otra sesion) — PUBLICADO; conservado integro

(cierre 9 llevaba ciclos=3 sobre «publicar el repo» — objetivo CUMPLIDO el 16/08 noche)

## ⚠️ 1. Comprobar que `P:` existe (10 s, antes de nada)

El `subst` **se cayó solo** el 16/08 entre las 17:44 y las 18:45, sin reinicio, y tumbó el gate de
acreditación: sin `P:\Mods` falla el sellado de la raíz y `dayz_test_run` devuelve `bad_mod_authority`
como si el fix estuviera roto. **No lo está.** `Test-Path P:\Mods`; si es `False`:

```
subst P: "C:\Users\guill\OneDrive\Documentos\DayZ Projects"
```

## ✅ 2. El árbol está VERDE y es atribuible

**Suite del árbol 22:32: `Ran 1416 · OK · skipped=4`** — cero fallos, cero errores. Se acabó la
ambigüedad de las dos sesiones: los 4 rojos del cierre 7 eran BUG-077 y BUG-078, y **la otra línea
los cerró** (el hardcode `utopia_pc` ya no está en `dayz_test_worker.py`; la regla vive ahora en
`request-policy.json` / `worker-runtime.json`, que es donde se quería). Composición: 1401 del cierre 7
+ 7 tests míos + 8 suyos = 1416.

## 🐞 Cerrado en esta tanda (16/08 noche)

| Bug | Qué era | Gate |
|---|---|---|
| **BUG-076** | `camera_set` rechazaba `cam_mode:"look_at"`. El valor de cable es `lookat` **sin guión bajo**, y el parámetro vecino de la misma firma se llama `look_at` | Alias **normalizado**, no sólo aceptado. 3 mutaciones en rojo; la decisiva: aceptar sin normalizar da `'look_at' != 'lookat'` |
| **BUG-080** | El daemon no arrancaba con ninguna sesión abierta: la quiescencia se exigía **antes** del fast-path del recibo | `_settled_receipt()` read-only salta el gate sólo donde no se escribe nada. 3 mutaciones en rojo; perímetro 150/150 |
| **BUG-075** | `restore_gameplay` devolvía `ok:1` con la cámara puesta | **Gate in-game pasado**: `player_camera_active` → cámara → **`player_camera_active` otra vez**. Probado también en la variante `orient`→`free` |
| **BUG-081** (nuevo) | Saltar a la free-cam abandonaba la `staticcamera` en el mundo y borraba su única referencia | Encontrado **aplicando DZ-R7** sobre la invariante de BUG-075. Contrato de source + no-regresión in-game |
| **BUG-071** | La API key se leía una sola vez; tras rotarla, backoff ciego hasta 30 s | **Gate funcional pasado** (ver abajo) |
| BUG-077 / 078 | Hardcode de proyecto y test acoplado a la máquina | Cerrados por la otra sesión |

### BUG-071 — el gate que vale, con sus tres piezas

No basta con «compila». El camino nuevo es el de **fallo** de poll, y se ejercitó así: key caducada
plantada **antes** de arrancar el servidor → el bridge nace con credencial muerta →
`poll error=5` con `backoff_s` **4 → 8 → 16 → 30 → 30**, clavado en el tope (el síntoma exacto de la
ficha) → key buena restaurada **en caliente** → `poll key reloaded path=$mission:dayz_mcp.json
keylen=43` → **poll reanudado de verdad** (`bridge_status.server_peer.last_poll_age_s = 0.216`), sin
reiniciar la misión.

El gate confirmó por su cuenta el hallazgo que descartó el diseño obvio: el error llegó como
**`error=5`**, que es el valor que `EREST_ERROR` y `EREST_ERROR_CLIENTERROR` **comparten**
(`restapi.c:16-17`). Desde Enforce **no se puede distinguir un 401 de una conexión rechazada**, así
que el disparador tuvo que ser el fallo sostenido, no una clasificación de auth.

## 🔒 3. El centinela de `MCPBridge.c` — leer antes de tocar ese fichero

`tests/test_task9_spawn_phase_markers.py` congela el sha del bridge, que **no está en git**: es el
único control de cambios no intencionados sobre ese fuente. Su cabecera manda re-congelar **sólo
sobre estado gateado in-game**, nunca sobre un edit source-only, y **las dos mitades juntas**
(`BRIDGE_SHA256` y `BASE_BRIDGE_SHA256`).

Esta tanda lo disparó al tocar el bridge y **se dejó en rojo hasta pasar el gate**, que es lo que el
test existe para forzar. Re-congelado después sobre el estado gateado (`F716A88D…` / `E87539E5…`) y
**verificado que sigue mordiendo**: una mutación de 1 byte pone ambas mitades en rojo. Si vuelves a
tocar `MCPBridge.c`, el camino es ese y no actualizar el hash para «arreglar» la suite.

## 🚀 PUBLICADO — `https://github.com/willy92wins/dayz-mcp` (MIT)

Repo local **`C:\Users\guill\Repos\dayz-mcp`** (fuera de OneDrive; D-44). Commits: `8e2ad98` inicial
(gate del export 1242/0/0/28 ×2), `dafac69` generabilidad + `.gitattributes`, `a74280f` README de dos
loops, **`31040de` = la tanda nocturna de este cierre 8** (BUG-071/075/076/080/081, 9 ficheros, +426;
suite del repo 1253/0/0/28 con la máquina quieta). `origin/main` = `31040de`. Nada del árbol queda sin
publicar salvo lo que la frontera excluye a propósito. ⚠️ La suite tiene un test de elección de daemon
sensible a CARGA (`test_run_daemon_candidate_wave…`): si da rojo con otros gates corriendo, re-medir a
solas antes de atribuirlo.

**Cómo se publica a partir de ahora** (tubería ya con sitio duradero:
`DayZ_MCP_dev\tools\publish\` + README): `boundary.py` → `export_public_repo.py` → venv del export
con los tres pasos de `install-mcp.ps1` → `run_export_suite.py` → overlay `robocopy /E` sobre el repo
(excluyendo `.venv-mcp`, `egg-info`, `__pycache__`, `.git`) → **suite otra vez sobre el working tree
del repo** (es donde git ya ha hecho lo suyo con los bytes) → commit → push.

⚠️ **Dos trampas pagadas hoy**: (1) `core.autocrlf` rompió un pin de bytes en el primer commit
(LICENSE psutil 1548≠1577) y convertía a CRLF las fuentes congeladas por sha; el `.gitattributes`
del repo (`* text=auto eol=lf`, `tools/vendor/** binary`) lo cierra, pero **el gate se corre sobre el
clon checked-out, no sobre la copia exportada** (LL-290, BUG-082). (2) Los `.snap`/`.post` que deja
el mecanismo de escritura de otra sesión los excluye la frontera (`WRITE_ARTIFACTS`); si aparecen
otros sufijos, ahí se añaden.

**Un clon ajeno genera lo excluido**: `python relock_toolchain.py` (re-fija el lock al MSVC/SDK
local; no-op en esta máquina), `build_native_launcher.py --verify-reproducible`,
`python -m dayz_mcp.launcher_registry_update bootstrap` → `install-dayz-test-v1 --expected-sha256 <sha>`.
Secuencia completa en el README público.

## 🐞 Bugs abiertos — ninguno bloquea publicar

- **BUG-067** — fondo P1 (adoptar/reconciliar un run ajeno en vez de sólo rechazarlo). La mitigación
  fail-closed ya está aterrizada. Adjudicado a Fase 2 **con `rigorous-data-audit` (DZ-R9) previo**.
- **BUG-069** — arreglado sin gate: exige un vehículo con fixture de conducción. **Agrupar** con el
  próximo test de vehículos, no gastar un ciclo propio.
- Backlog P2/P3 sin urgencia: 031, 035, 036, 038, 061, 065.

## ❌ Hipótesis muertas — no reintentar

- **«Editar `dayz_mcp.json` en caliente recarga la key»**: **falso y medido**. La key vive en memoria,
  los polls siguen OK y no hay fallo que dispare la recarga. Para gatear rotación hay que plantar la
  key y **reiniciar el proceso**.
- **«Se puede detectar el 401 desde Enforce»**: falso. `OnError` entrega un `ERestResultState` y
  `EREST_ERROR` comparte valor con `EREST_ERROR_CLIENTERROR`.
- **Casar la procedencia del registro por identidad NTFS**: `ReplaceFileW` **no conserva el file-id**.
- **«Todo mod de Workshop es un junction»**: falso, **17 de 390 (4%)**.
- **«El reclaim embedded alcanza procesos ajenos»**: falso; exige argv **idéntico**. Mataba hermanos sanos.
- **Excluir el builder en vez de externalizar**: la frontera ya lo excluía; la fuga no era esa.

## 🩹 Deuda localizada

- **Flakes de `test_client_mode`**, los dos en el mismo fichero. (a) `:706` afirma el timeout con un
  margen de **0,3 s** y bajo carga el fallo llega por conexión; (b) `:830` es el error que quedaba en
  el export. **No arreglar a ciegas**: (b) necesita el traceback real desde el export.
- **`DayZ_MCP_dev` no está en git** (`.git` vacío). Backups por SHA en `_backups\`.
- **`ProcessVehicleGetInClientPrep` NO suelta la cámara**, y es deliberado: ahí la simulación se
  restaura como precondición del comando de vehículo, no como salida del control de cámara.
- **La fuga de BUG-081 no es observable desde las tools** (una `staticcamera` no tiene geometría que
  raycastear ni id que inspeccionar): la fija el contrato de source, no una medición in-game.
- `camera_set free` **no aterriza en la posición pedida**. Medido con control: no lo causa BUG-081
  (en la primera llamada el `DeleteOwnedCamera` insertado era un no-op y tampoco coincidió).
  Comportamiento previo de `FreeDebugCamera`, **sin ficha propia todavía**.

**Gate de arranque (cierre 9, HISTORICO — superado por el de arriba):** declarar `Retomo DayZ_MCP desde: PUBLICADO en willy92wins/dayz-mcp, árbol verde 1416/0/0/4 · próxima acción: <lo que toque>; si toco el árbol, republicar por tools\publish\` y verificar `Test-Path P:\Mods` antes de tocar el juego.

### 2026-07-29 — gate agrupado `query_all_players`

**Veredicto:** el verbo `query_all_players` está **verificado in-game** y el PBO nuevo está desplegado. Los tres verbos viejos pasaron regresión con ese mismo PBO. **GameMaster IG-1 queda DESBLOQUEADO.** BUG-066 (c) no está arreglado pero sí **diagnosticado con causa raíz citada**. Dos frentes del lote quedaron sin cerrar por un bloqueo declarado, no por olvido: ver «Lo que NO se cerró».

ciclos_en_este_objetivo: 2 (BUG-066 (c) fix + los dos gates in-game que quedaron)

Handoff de la jornada: `AI/30_Sessions/2026-07-29-DayZ_MCP-gate-query-all-players.md`.

## El PBO — artefacto nuevo y su restore point

| | SHA-256 | Tamaño | Origen |
|---|---|---|---|
| **DESPLEGADO (nuevo)** | `51C2F5A2E2078C6B8006BDAA4D0FB9D8B4D6C3850880D2CE3823433C458AE932` | **132.786 B** | 2026-07-29 16:47:50 |
| Restore point (anterior) | `92F73D388C0A2FED408CA99D36E2941391642F47EE85DB72F5AACFF74302E479` | 131.843 B | 2026-07-28 21:43:43 |

Restore point en `DayZ_MCP_dev\_restore\` (copia + `MANIFEST-92F73D38.md` con el comando de reversión) y segunda copia en el scratchpad de la sesión. **Ambas verificadas por SHA-256 tras copiar, no por mtime.** Revertir es correcto sólo si el hash vuelve a ser `92F73D38…E479`.

## Gate de `query_all_players` — lo que se midió, con el dato

Todo con el PBO nuevo, server+cliente Diag, `mode=all`, chernarus.

| Escenario | Resultado | Evidencia |
|---|---|---|
| 0 jugadores | **PASA** | `ok:1`, `players:[]` — array vacío es ÉXITO. Contraste medido en el MISMO estado: `query_player_state` devuelve **error `no_players`** y `query_all_players` devuelve **éxito con `[]`**. |
| 1 jugador con identidad | **PASA** | `uid:"76561198141021937"` (SteamID64 real), `pos:[13529.36328125, 2.097942590713501, 6168.49951171875]`, `health:1`, `in_vehicle:0` |
| `pos` coherente | **PASA** | idéntica, dígito a dígito, a la que devuelve `query_player_state` en la misma lectura |
| `uid` estable | **PASA** | mismo valor en 4 lecturas separadas, incluida una tras cambiar de estado |
| `health` en 0..1 | **PASA** | `1` (no 100) |
| `in_vehicle` | **PASA** | ciclo completo **0 → 1 → 0**: a pie `0`; tras `vehicle_enter` en CivilianSedan (`seated:1, seat:"driver"`) → `1` y la `pos` saltó al asiento; tras borrar el coche → `0`. No es un valor congelado. |
| `Man` sin identidad | **PASA como observación, NO como prueba del guard** | Con un `SurvivorM_Mirek` spawneado, `players` siguió con **una sola entrada**. Pero eso NO discrimina «el guard `!ident` lo omitió» de «`GetPlayers()` no lo devuelve»: ambas producen el mismo observable. **El guard sigue sin ejercitarse de forma probada.** |

**2 clientes simultáneos: NO EJECUTABLE en esta caja.** 1 cuenta Steam = 1 cliente; el segundo DayZDiag recibe kick **179**. Requiere segunda cuenta o segundo PC. Consecuencia honesta: **la multiplicidad de SALIDA (≥2 entradas a la vez) no está probada in-game**; lo que sí está probado es que el verbo no es un `Get(0)` disfrazado en su forma (devuelve array, y el array responde al estado real).

## Trampas NUEVAS verificadas esta sesión — las tres cuestan una sesión si no se saben

- **`dayz_test_run` con `build:true` está ROTO.** Devuelve el genérico `dayz_test_failed` (el `except Exception` de `server.py:1101`, que tapa la causa) y **no escribe el PBO** (hash y mtime intactos). Reproducido 2 veces, siempre a los ~16 s. **Sin `build` funciona** (`mode=server` 5,1 s; `mode=all` 28,6 s) y **`preflight:true` pasa en 1,5 s**, así que launcher, PE, bundle, request y lifecycle están SANOS. **AddonBuilder tampoco es el culpable**: a mano da `Build Successful`, exit 0, en ~3,4 s. Es trabajo de PLATAFORMA → congelado por D-33, no lo abras.
  **Workaround verificado y repetible**: `AddonBuilder.exe P:\DayZ_MCP <staging> -prefix=DayZ_MCP -temp=P:\temp\DayZ_MCP -clear -packonly` → contar anclas dentro del PBO → publicar a `P:\Mods\@DayZ_MCP\Addons\` → verificar por SHA-256 → `dayz_test_run` **sin** `build`.
- **El daemon cachea `loopback.py` EN MEMORIA.** Un verbo añadido a `SERVER_COMMANDS` responde **`not_whitelisted`** hasta que el daemon se reinicia, aunque el fuente en disco sea correcto y aunque el tool ya aparezca registrado en el cliente MCP. Evidencia dura: `loopback.py` mtime **15:57:54**, daemon PID 4372 arrancado **14:18:42** → 1 h 39 min sirviendo el módulo viejo. El síntoma engaña: parece «el verbo no está» y es «el daemon no lo ha leído». Reinicio = `Stop-Process` del listener del 8765; el cliente lo re-spawnea lazy (`daemon_generation` 4f0dc590… → 3b381110…). Tras el respawn hay una ventana corta de `version_blocked` / `peer_reconnect_flush` mientras rehace el handshake: **no es un fallo, reintenta**.
- **`players` null se serializa como `[]`, NO como `null`.** Comprobado en `query_player_state`, `world_spawn`, `object_delete` y `telemetry_read`: todos emiten `"players":[]` (igual que `raycast:{}`/`telemetry:{}`). **Consecuencia para el consumidor (GameMaster): `players:[]` NO distingue «cero jugadores» de «este verbo no rellena players».** Si IG-1 necesita esa distinción, tiene que mirar el verbo que pidió, no el campo.

## BUG-066 (c) — reproducido y con CAUSA RAÍZ, sin arreglar

Reproducido con el PBO nuevo sobre un CivilianSedan: `"declared_slots":[""]` (un elemento, cadena vacía).

**No es «faltan slots»: es que se pregunta por los slots equivocados.** `PopulateTelemetryInventory` (`MCPBridge.c:1739,1742`) usa la API INVERSA:

| Usa hoy | Qué es de verdad (vanilla) | Lo que hace falta |
|---|---|---|
| `GetSlotIdCount()` | `inventory.c:172-175` — "number of slots **this item can belong to**" | `GetAttachmentSlotsCount()` — `inventory.c:181-184`, "number of slots **for attachments**" |
| `GetSlotId(i)` | `inventory.c:167-171` — "slot where **this item belongs**" | `GetAttachmentSlotId(i)` — `inventory.c:176-180`, "slot **for attachment**" |

Un `CivilianSedan` no se attacha a ningún padre → cuenta 1 con un id inválido → `InventorySlots.GetSlotName(id)` (`inventoryslots.c:48`) devuelve `""`. **Fix = dos líneas en `MCPBridge.c:1739` y `:1742`**, pero obliga a otro rebuild + gate: agrúpalo con los dos gates que quedan (abajo), no lo hagas suelto.

## Lo que NO se cerró, y por qué

- **BUG-061 cadence** (`19.96890272042031 Hz < 20`): **bloqueado por el propio encargo.** Medirlo exige `vehicle_trace`, que el prompt de la sesión listó como PROHIBIDO. El frente lo pedía y la prohibición lo impide: hay que adjudicar cuál manda antes de volver a intentarlo.
- **`OnContact` owner-client**: no ejecutado. Necesita cliente owner conduciendo y la lectura va por la misma vía `vehicle_trace`. Mismo conflicto.
- **Fix de BUG-066 (c)**: diagnosticado, no aplicado (otro rebuild + gate).

## Cierres degradados de esta sesión — reconócelos, no son fallos nuevos

- **`session_release` devolvió `cleanup_degraded:["audit_failed"]` las 3 veces**, con `released:true` y `terminal_safe:true` siempre. Los `dayz_test_run`/`dayz_test_stop` cerraron con `cleanup_degraded:false`. `session_status` final quedó **limpio** (sin owner, cola vacía, `audit_fault:null`, 0 pendientes) y **cero procesos DayZ**. El audit del release falla pero el release es correcto; si reaparece, es preexistente a esta sesión.
- `storage_1` se renombró a `storage_1.bak_20260729_mcpgate` antes de arrancar (el run previo era con `@LFHeli_OH1` y éste iba sin él; hay dos precedentes de `storage_1_corrupt-modstorage-*` por exactamente eso).

## Los frentes abiertos, en el orden acordado

1. **[SIGUIENTE] Un rebuild + un gate que agrupe**: fix de BUG-066 (c) (dos líneas, ya citadas) + `OnContact` owner-client + BUG-061 cadence — **estos dos últimos sólo si se adjudica antes el conflicto con la prohibición de `vehicle_trace`**. Usa el workaround de build de arriba: `build:true` sigue roto.
2. **[CONGELADO por D-33]** Fase 2, fondo de BUG-067, y ahora también **el `build:true` del lifecycle** (es plataforma). BUG-067 se queda con la mitigación fail-closed. Lo que NO hay que volver a derivar: el nudo del TTL es *indecidible* — `touched_at` sólo se actualiza cuando el cliente consigue el lease, así que «120 s sin tocar» significa a la vez *muerto* y *esperando turno* (verificado con dos R21). Segundo bloqueante: `mode=all` hace DOS `lifecycle_start` y el ticket se consume en el primero. Plan v2 en `plans6-07-28-fase2-run-queue-orden-estable.md`.
3. **[MEDIA]** 9 mods sin medir en la tabla de arena → fail-closed en Fase 4. **[TRANSVERSAL]** `measure_mod_scripts.ps1` no sigue junctions en PS 5.1.
4. **[BACKLOG P3]** BUG-031, y BUG-006/007 marcados «deferred → fase 1» desde antes de que la Fase 1 existiera: revisar si siguen aplicando.

**NO ABRIR sin decisión explícita**: cualquier trabajo de plataforma (D-33), Fases 3-6 (tocan `runs.json` → DZ-R9), el frente del Knowledge Pack (la regresión vive en la rama `r21/phase01-foundation`; `ac21d13` sin mergear invirtió 4 tests de fail-closed), y `vehicle_trace` (P7).

## Invariantes CERRADAS — NO retocar sin evidencia nueva

- **`query_all_players` verificado in-game 2026-07-29.** Array vacío = éxito. `MCPPlayerState` sigue intacta a propósito. No re-litigues el diseño (clase nueva `MCPAllPlayer`, identity-less omitidos, health 0..1).
- **BUG-067**: el predicado es `observed - registered_pids`. **No lo cambies a `!=`**; hay un test que lo caza y existe por eso.
- **BUG-037**: el CÁLCULO del timeout va arriba (lo necesita el retorno temprano del camino interno), el RECHAZO va después de `authorize`. No lo subas.
- **`process_lifecycle.py` NO es módulo sellado**; `daemon_contract.py`, `host_config.py`, `server_cli.py` y `dayz_test_worker.py` SÍ. Tocar un sellado obliga a rebuild + rollout CAS.
- **`loopback.py` y `server.py` NO están sellados** en `app.pyz` — editarlos surte efecto sin rebuild del bundle, **pero exige reiniciar el daemon** (ver trampas).
- 7 fixes de Enforce verificados EN EL JUEGO. BUG-044 y BUG-064 cerrados. Q1/Q2/Q3 ratificadas → D-32.
- BUG-062(b): máximo dos `_send`, deadline sin ampliar. Reiniciar el daemon es seguro (confirmado otra vez hoy).
- Fase 1: lista cerrada `frozenset({"active_run_exists"})`; camino de adopción FUERA a propósito.
- BUG-064: el harness nunca selecciona procesos por substring. `orphan_guard.kill_pid` **no existe y no se debe crear**.
- `CONTROL_SAMPLE_HZ=20` · `OnContact` real insustituible · lifecycle sólo por tools y `run_id` exacto · no tocar `session_coordination.py` (BUG-046) · `--idle-timeout` 3600 sin `-Register`.

## Trampas del entorno ya conocidas

- **BOM**: `python open().write()` por defecto. `Set-Content -Encoding utf8` (PS 5.1) mete `EF BB BF` SIEMPRE; en Enforce impide compilar con un síntoma que engaña (`CParser: quoted string not closed on line 1`). **Esta sesión compiló limpio**: `Module: Mission; loaded 216x files; 486x classes`, cero errores, `defines:` con `DayZ_MCP`.
- **Las fichas del ledger anteriores al refactor de julio citan anclas MUERTAS.** Verifica el ancla ANTES de delegar.
- **`P:` puede caerse sola** (`subst`). Síntoma engañoso: `identity_open_failed:P:\Utopia_PC`. Remontar: `subst P: "C:\Users\guill\OneDrive\Documentos\DayZ Projects"`.
- **NUNCA encadenes el rollout detrás del build sin comprobar su exit code** (deja el registro VACÍO). Ciclo bueno: `build_native_launcher.py --offline --verify-reproducible` → exit 0 → `rollback-last` → verificar registro en baseline vacío `330B04E8` → `install-dayz-test-v1 --expected-sha256 330B04E8…` (sha del REGISTRO tras el rollback, no del PE).
- **El rebuild del bundle NO se delega**: clavado a rutas absolutas de OneDrive.
- **Un workspace delegado envejece**: diff fichero a fichero antes de aterrizar; aterriza sólo el alcance declarado.
- **Para vigilar a Codex usa el PID**, nunca marcadores de texto. `codex:codex-rescue` tiene tope duro de 10 min.
- **Cuatro módulos NO deterministas** bajo carga: `test_bug046_startup_deadlock`, `test_task7_review_regressions`, `test_bug046_audit_fault_recovery`, `test_client_mode`.
- Antes de tocar `dayz_mcp\*.py`: avisar (desarma las tools MCP de las sesiones vivas) **y contar con que el daemon hay que reiniciarlo para que el cambio tenga efecto**.

**Gate de arranque:** `Retomo DayZ_MCP desde: gate agrupado CERRADO — query_all_players VERIFICADO in-game con PBO nuevo desplegado (51C2F5A2…E932, 132.786 B), regresión de los 3 verbos viejos OK, gate de captura OK, BUG-066 (c) reproducido y con causa raíz citada (API inversa: GetSlotIdCount/GetSlotId en vez de GetAttachmentSlotsCount/GetAttachmentSlotId), y GameMaster IG-1 DESBLOQUEADO · próxima acción: un rebuild + un gate agrupado con el fix de dos líneas de BUG-066 (c), y adjudicar antes si OnContact/BUG-061 se hacen pese a la prohibición de vehicle_trace · OJO: dayz_test_run con build:true está ROTO (usa el workaround de AddonBuilder a staging + publicar por hash) y el daemon cachea loopback.py (reiniciarlo tras tocar la whitelist) · NO abrir Fase 2, fondo de BUG-067, Fases 3-6, Knowledge Pack ni vehicle_trace`
### 2026-06-28 — Captura cap-aware (MAX_MCP_OUTPUT_TOKENS) + WebP opt-in (ORTOGONAL a Fase 5)
- **Pregunta del usuario**: mejorar la parte visual del MCP. Diagnóstico: el pipeline ya estaba maduro (JPEG q82 default, crop, canal dual a disco `save_fullres`); el techo real = los ~25k tokens/respuesta de Claude Code. Research (agente claude-code-guide): `MAX_MCP_OUTPUT_TOKENS` SÍ sube ese techo (VERIFICADO docs/issues; default 25k; global a TODOS los MCP; `anthropic/maxResultSizeChars` NO aplica a imágenes); WebP aceptado pero con bugs 400 (#39146 mis-MIME, #15807).
- **Implementado** (Python puro, sin tocar Enforce/PBO): `mcp_capture.py` → `client_token_cap`/`default_max_tokens`/`resolve_request_budget` (margen `TOKEN_SAFETY_MARGIN=0.92`); `server.py capture_screenshot` clampa fail-closed antes de capturar + docstring con valores de referencia y semántica `scale`/`webp`; WebP en `encode_bytes`/`_mime_for` (opt-in); `SCALE_WIDTHS["full"]` 640→8192 (budget-limited).
- **Bug R8**: los unit-tests del budget pasaban pero `SCALE_WIDTHS["full"]=640` capaba el ancho ANTES del budget loop → el cap-raise no compraba resolución. Cazado por verificación funcional end-to-end (no solo unit), corregido + re-verificado.
- **Gates offline**: suite **143/143** (9 nuevos `tests/test_token_budget.py`); medición sobre frame real 1302×776 (jpeg q82, margen 0.92, `scale="full"`): 25k→581px · 50k→822px · 75k→1007px · 100k→1162px; `scale="small"`=512 hard-cap; clamp(200k@cap25k)=23k.
- **Config usuario**: `MAX_MCP_OUTPUT_TOKENS=75000` añadido al `settings.json` GLOBAL (env block; JSON validado, hooks/plugins preservados). Memoria `mcp-image-token-limit-claude-code` corregida (sin verificar → verificado + patrón server↔cliente + webp-brick + medición).
- **PENDIENTE = gate in-vivo** (no fabricable offline): sesión NUEVA → `scale="full"`/75k con el juego → confirmar imagen ~1010px sin rechazo del cliente; webp → vigilar 400-brick. Archivos: `tools/mcp_capture.py`, `tools/dayz_mcp/server.py`, `tools/tests/test_token_budget.py` (nuevo).

### 2026-06-14 — Fix captura visual fase-3 (grab stale) — root cause + fix + validación in-game
- **Síntoma** (observado 06-14 conduciendo el bridge por PowerShell): `camera_set` ok=1 pero los window-grabs salían BYTE-IDÉNTICOS entre launches distintos → la captura cogía un frame stale del escritorio (Steam/Cowork), no el render vivo.
- **ROOT CAUSE = el GRAB, no camera→render.** El grab viejo hacía `SetForegroundWindow()+CopyFromScreen(rect)`; desde un PowerShell de fondo el foreground-lock de Win32 ignora el request → CopyFromScreen leía lo compositado en esas coords. **camera→render SÍ funciona** (el bridge Enforce NO se tocó; SetActive(staticcamera) conduce el render).
- **Fix**: grab canónico único `tools/spike0/mcp-grab.ps1` con `PrintWindow(hwnd, PW_RENDERFULLCONTENT=2)` (superficie propia de la ventana, robusto a oclusión, sin robar foco) + fallback foreground-real (AttachThreadInput). Consumido por `mcp_capture.py` (tool MCP + fase3), `spike0-window-enum.ps1` y `A6 gate-mcp.ps1` (sin tocar el harness A6).
- **Content-aware gate (A2/A3)**: `mcp_client.py` `run_phase3_content` conduce lookat(player) vs lookat(cielo) y `phase3_capture_cases` añade `D2_capture_content` (delta_follow/delta_center/delta_live) → "D2 pasa" ahora significa que el visual funcionó de verdad. El check non-black viejo no distinguía live de stale.
- **Validación in-game** (4 launches): diagnóstico multi-método (`mcp-grab-diag.ps1`) + `run-fase3.ps1` GATE=PASS (`D2_capture_content` delta_follow=0.36, delta_live=0.0069, method=printwindow) + `gate-mcp.ps1` (player+arma encuadrado, `captures/mcp_probe_akm_i1.png`). Evidencia AKS74U: `tools/spike0/_grabdiag/subject_printwindow.png`.
- **Hallazgo readiness**: la captura puede coger un frame de carga/transición si se dispara antes de que el CLIENTE esté renderizando in-game (gate-i2 cogió la pantalla de carga; gate-i1 faithful cogió al player aún sin asentar). El content-aware lo caza (delta≈0). Requiere settle/readiness en el consumidor — ver fix `PHASE3_CONTENT_GRAB_SETTLE_S=2.0`.
- **Sin rebuild PBO, sin tocar Enforce, gates 0-2 intactos.** Suite venv 38/38. Ficheros: `mcp_capture.py`, `mcp_client.py`, `spike0/mcp-grab.ps1` (nuevo), `spike0/mcp-grab-diag.ps1`+`-analyze.py` (nuevos diag), `spike0/spike0-window-enum.ps1`.

### 2026-06-10 (noche, 3) — Fase 4B impl + GATE 4B in-game PASS → PROYECTO CERRADO
- Codex implementó 4B source-only (handoff propio): 3 handlers Enforce (`world_time_set`/`world_weather_set`/`exec_enforce`) + `ver=` en ambos pollers + `MCP_BRIDGE_VERSION="4"` + tools/gating Python. Receptor: scope limpio por mtime (8 handlers + MCPJobRunner intactos), 26/26.
- Gate 4B (conduce Claude): rebuild PBO 4B (AddonBuilder, verificado por strings) → regresión fase3 ×3 GATE=PASS → harness ingame4b **11/11 PASS**.
- **3 hallazgos del gate**: GATE4B-001 (bug producto, TODAS las tools: `ok is False` vs int 0/1 del bridge → errores de negocio como éxito; fix + fake alineado + test; LL-115), GATE4B-002 (allowlist BOM crashea arranque → utf-8-sig), GATE4B-LIM (limitación engine: `ExecuteEnforceScript` Developer-only devuelve false en server headless aun con wrapper vanilla EXACTO — probados 6 formatos; gating+audit verificados, ejecución documentada fuera de alcance, adjudicado con usuario).
- Fixes de harness del gate: sleeps fijos → polling con deadline (backoff del bridge); advisories (visible día/noche, exec engine) separados del gate; test_instance_lock bootstrap inline.
- Cierre: product-spec E1/E2 → ✓ (E3/E4 ya en gate 4A); Fuera-de-alcance + Changelog; **Fase 4 COMPLETA → proyecto DayZ-MCP cerrado** (todas las fases 0-4 PASS in-game).

### 2026-06-10 (noche, 2) — GATE 4A in-game PASS (conduce Claude)
- Sin juego: E3 install end-to-end (cazó **GATE4A-001**: Pillow ausente de requirements → pin 12.2.0); `mcp` desinstalado del user site → stdlib-only verificado de verdad (shim + 11 tests con python del sistema); registro real `claude mcp add` → `√ Connected`; smoke MCP 9/9 con cliente SDK (cazó **GATE4A-002**: `allow_reuse_address` de HTTPServer permite doble LISTEN en Windows → lock E4 roto → `ExclusiveThreadingHTTPServer` + `test_instance_lock.py`, suite 19/19; la suite de Codex no testeaba el lock).
- Con juego (`run-fase3.ps1 -NoStop` → swap del loopback por el server MCP con `fase3.key`): regresión FASE3_VERDICT PASS 6/6 → in-game 8/8 — bridge real → MCP tools end-to-end (pos real, captura PNG real ≈21k tokens, concurrencia, stale-key liveness, recovery 6.0s medido).
- 2 fixes del harness de gate (no producto): lock-test necesita 2º cliente MCP real (un server stdio pelado espera initialize); recovery-check por polling (3s fijos < backoff post-401).
- product-spec E3/E4 → ✓ in-game; E1 → 9/12; inbox actualizado. Pendiente: 4B.

### 2026-06-10 (noche) — Fase 4A implementada (Codex) + receptor PASS
- Codex implementó 4A desde el prompt scope-bounded (20 min): `dayz_mcp/` (loopback extraído stdlib-only con los 4 cambios §3.2; server FastMCP con 9 tools, mutex, 4-estados, lifespan start/stop), shim 5-endpoints, install `-Register`-gated, README, 11 unittests nuevos. Hallazgo C aplicado por Codex: FastMCP no acepta param `from` → alias público `"from"` + `from_pos` interno en scene_raycast (verificado server.py:260/269). Schemas citados contra MCPBridge.c/MCPClientBridge.c/mcp_client.py.
- **Receptor Claude**: 18/18 re-run independiente; scope por mtime limpio; constraints verificados en código (incl. mutex en `runtime.tool_lock` — grep inicial por `self.` dio falso negativo); `asyncio.to_thread` en captura (no bloquea el loop). ACEPTADO, 0 scope creep. Findings menores: mcp en user site (desinstalar tras crear `.venv-mcp`), `EXPECTED_BRIDGE_VERSION="4"` a respetar en 4B.
- Pendiente: **gate 4A in-game** (7 puntos del plan §8-4A + regresión). 4B sin empezar.

### 2026-06-10 (tarde) — Fase 4: grill + research + plan v1→R22→v2 + prompt impl 4A
- Grill Modo B en 2 lotes AskUserQuestion (7 adjudicaciones G-1..G-7): SDK `mcp`/FastMCP; 1 proceso 2 caras; E2 reformulado (hallazgo cláusula de desafío: el snapshot ERPCs protegía un riesgo que T-A eliminó); deuda fuera; `vehicle_drive`/`session_*` fuera + `bridge_status`; tramos 4A→4B.
- Research fase 0 SOLO de lo nuevo (excepción dual documentada): smoke install real `mcp==1.27.2` en Python 3.14.3 host + firmas inspeccionadas; SDK no serializa (`start_soon`) → mutex E4; `configure_logging`→stderr; hallazgo load-bearing: los `print()` del loopback a stdout corromperían el stdio MCP. Firmas Enforce 4B verificadas host-direct (`GetVersion game.c:944`, `SetDate world.c:51`/`GetDate :33`, `Weather weather.c:167/183-189`, `WeatherPhenomenon.Set :49`/`GetActual :38`, `GetWeather game.c:1349`).
- Plan v1 (12 tools E1, gates 4A/4B) → **R22 Codex**: approve with minor (R22-F4-001 FAIL handshake incompleto/fail-open + 4 WARN: lifecycle asyncio↔thread, negativos de gates, BUG-009..012 sin declarar, `/set_poll_delay` omitido de back-compat + 1 NIT). Receptor verificó host-direct los load-bearing → **v2** con los 6 aplicados (handshake `version_state` 4-estados + `EXPECTED_BRIDGE_VERSION` const + `--require-version`; `LoopbackServer` start/stop + `ServerState` encapsulado; fixtures negativos 4A.7/4B.7; BUGs declarados; `/set_poll_delay` preservado+test).
- Bajado al product-spec: filas E1/E2/E4 + Changelog 2026-06-10. Inbox: applied. Prompt impl 4A escrito (scope-bounded). NADA de código/PBO tocado.

### 2026-06-09 (noche) — Plan Fase 3 v2 + review adversarial (autónomo, usuario dormido)
- 4 adjudicaciones Grill Modo B vía AskUserQuestion (usuario despierto, pre-sueño): alcance = plan+review adversarial; CONFLICT-1 = presupuesto de tokens; CONFLICT-2 = síncrono+readiness; peer cliente = mínimo para Visual.
- 2 sub-agentes de recon host-direct (estado del bridge + patrón cliente RPC/cámara) → 2 supersesiones del research: **SUP-1** (indexado GAME_TEMPLATE-only → Camera entity), **SUP-2** (MissionGameplay sin OnRPC + bridge HTTP → poller cliente T-A).
- CONFLICT-1/2 aplicados al `product-spec.md` (D2 + Changelog). Plan v1 redactado → **review adversarial de 3 subagentes** (arq/traza · citas R2 · riesgos in-game) → **plan v2** integrando fixes. Review doc: `reviews/2026-06-09-adversarial-review-fase3-plan.md`.
- Review confirmó SUP-1/SUP-2 host-direct y T-A viable; cazó bugs (ventana de grab equivocada, freeze server-authoritative en peer equivocado, create_local, FOV getter, token budget ~320px, job machinery=port) + definió un **Spike 0 OFFLINE** (ping RestApi, selector ventana, calibración tokens) que de-risca sin rebuild.
- NADA de código/PBO/in-game tocado. Próximo: ratificar SUP-1/SUP-2 + 6 puntos → Spike 0 offline → (opcional) R22 Codex → implementación.

### 2026-06-09 — Fase 3 (Visual): research dual fase 0 cerrado + consolidado
- Research dual R24: Claude (5 sub-agentes Explore/general-purpose en paralelo, 1 por dimensión) + Codex CLI, independientes (anti-contaminación). Solape ~70% (clusters de hecho verificado) → dual justificado (<90% umbral de degradación).
- Convergencia (núcleo): cámara client-rendered (RPC a peer cliente nuevo), imagen host-side (no pasa por el engine), sin señal vanilla de frame-presentado (gate externo frame-diff). Familia cámara `enworld.c:53-65` + `world.c:19` + transporte `MCPBridge.c:190/1810` **spot-verificadas host-direct** por Claude (exactas).
- Deltas cruzados clave (cada mitad aportó load-bearing que la otra no tenía): **Codex** halló `CameraToolsMenu` (precedente vanilla get/set + `RPCSingleParam` + `IsInterpolationComplete`); **Claude** halló que el límite real es **~25k tokens** (Claude Code), no 1MB → **CONFLICT-1** corrige D2.
- Conflictos para el plan: CONFLICT-1 (D2 `<1MB`→tokens, adjudicar), C1 (¿cámara indexada mueve viewport? — 1er spike), C2 (contrato peer cliente nuevo).
- NADA de código/PBO/product-spec tocado. Docs: `research/2026-06-09-fase3-visual{,-claude,-codex}.md`. Handoff: `30_Sessions/2026-06-09-DayZ_MCP-fase3-research-fase0.md`.
- Próximo: plan Fase 3 (Grill Modo B), 1er gate = adjudicar CONFLICT-1.

### 2026-06-09 — Fase 2 cerrada: R21 procesada + X.5 (F2-001/F2-002) + re-run in-game PASS
- R21 fase-2 (Codex, sesión 27): needs-fix, 2 FAIL + 3 WARN. Claude verificó host-direct los 5 hallazgos + el contrato.
- Hallazgo de Claude que refinó la R21: el fix single-exact que Codex sugería para F2-001 rompería los negativos
  `fixture_not_found`/`parse_error` del harness (usan 3 paths bajo el prefijo, `mcp_client.py:21-23`/`963-970`) →
  ripple R7. Fix correcto = harden-prefix deny-by-default.
- X.5 correctiva (Codex, sesión 28): F2-001 (allowlist: prefijo + basename sin `/`,`\`,`..`) + F2-002 (fixture
  vacío → `parse_error`/`found=false`). Claude verificó host-direct ambas matrices + scope limpio (IsFiniteFloat/
  radius/harness/MCPMessages intactos — Codex resistió la tentación F2-004).
- Claude: re-build PBO (AddonBuilder `-packonly`) + deploy `P:\Mods\@DayZ_MCP` (verificado por contenido: PBO
  contiene `empty_fixture`) + re-run `run-fase2.ps1 -WaitInGameSeconds 300` → **GATE=PASS, overall_pass=true**
  (`_fase2/run_20260609_014239`). Ripple R7 cerrado in-game. 3 WARN → BUG-010/011/012 backlog.
- Próximo: research dual fase 0 de Fase 3 (Visual).

### 2026-06-07 — X.5 correctiva (sesión 9): harness arreglado, fase 0 certificada
- Codex aplicó los 4 fixes (2 P1 harness + 2 P2 bridge); Claude (receptor) verificó host-direct: A1 sin fallback
  (target=marker, distance 0.0313m, no 0.000), verdict-integrity (overall_pass exige A1-A5 + clientExit0), backoff
  tras parse-fail, Shutdown+destructor (`RestContext.reset()` restapi.c:133). Re-cert `run_160720` GATE=PASS legítimo.
- Adjudicado: el tell "~0.003m" era predicción errónea de Claude; el gate real (target=marker + <0.5m + ≠0.000)
  se cumple — 0.0313m es el asentamiento vertical real del player. fase 0 cerrada de verdad.
- Próximo: fase 1 (control).

### 2026-06-07 — A4+A5 closeout, R21 doble revisión, decisión X.5
- A4+A5 PASS (Codex sesión 7): fix contrato 403→401 (`mcp_server.py:77`, gap que cazó Claude pre-handoff);
  A4 401/401/401+400, A5 backoff+recovery en log real. POC "completo" provisional.
- R21 estructural (doble revisión independiente Claude + Codex sesión 8): transporte sano, pero 2 P1 del harness.
  Codex cazó el A1 tautológico que la mitad Claude subestimó; Claude (receptor) verificó host-direct que disparó
  en run_045213 y que la propiedad real de A1 igual se sostiene (0.0031 m). Consolidado + scope B (usuario).
- Próximo: X.5 sesión 9 (2 P1 + 2 P2 bridge + re-cert), luego fase 1.

### 2026-06-06/07 — POC fase 0: plan (R22) → Step 0 → A1+A2+A3 PASS
- Proyecto propio; product-spec (Modo A); plan POC (Modo B) + R22 Codex (approve w/ minor) → v2.
- Step 0 (smoke): FAIL deploy → PBO `-packonly`; FAIL compile `ERESTOPTION_*` → quitado; FAIL `config_url_empty`
  → config dual-path `$mission:`; **PASS** (transporte plumbing probado).
- Step 1 (POC real, A1+A2+A3): FAIL compile `ReadFromString` (3er param) + ternario `?:` (no existe en Enforce,
  error de Claude) → fix var string; FAIL `no_players` (mission bare no spawnea) → **fix mission chernarus completa
  (run-poc.ps1)**; **A1+A2+A3 PASS in-game 2026-06-07** (distance 0.0, ticks_in_flight 6420, ids 10/11).
- Lecciones: harness-apis §9 item 10 (proto en dump ≠ runtime); Enforce sin ternario; mission completa para spawn.
- Próximo: cierre A4+A5 + R21 del POC; luego fase 1 arquitectura.

## Backlog entrante (2026-08-16, de otra sesion Cowork)

Llegado por relevo del usuario, con el encargo explicito de **meterlo en backlog, no de
dejarlo todo**. Nada de esto se ha tocado todavia; la otra sesion **no modifico `DayZ_MCP`**,
solo lo leyo.

- **BUG-075 (bug-ledger)** — `restore_gameplay` deja la camara activa. Causa localizada y
  **las tres citas verificadas por mi** contra `MCPClientBridge.c`. Es el mas caro de los
  tres: deja al usuario sin poder jugar y hay que reconectar, y la tool contesta `ok:1`.
  Al arreglarlo, el gate in-game no es opcional (Enforce).
- **BUG-076 (bug-ledger)** — `camera_set` rechaza `cam_mode:"look_at"`. Sin verificar.
- **Re-avistamiento, NO bug nuevo**: `session_release` devolvio `cleanup_degraded:
  ["audit_failed"]` dos veces seguidas. Ya esta descrito en **BUG-046** (refresh/persist
  concurrente con el WAL de release). Se anota aqui para no re-archivarlo como hallazgo
  nuevo; van ya tres avistamientos distintos (jul-29, hoy x2), asi que la frecuencia deja de
  ser anecdotica.
- **Idea de tool (del usuario): visibilidad antes de teleportar.** Hoy `player_teleport` es a
  ciegas — cayo dentro de un matorral y las entidades quedaron bajo la copa de un arbol.
  **El discriminante ya esta validado in-game por la otra sesion**: rayo vertical desde ~30 m
  sobre el punto y comparar la Y del impacto con `surface_query` del mismo (x, z); si
  coinciden, no hay nada encima. Medido en (7512, 7502): raycast **315,5603** vs superficie
  **315,5601**. Dos formas posibles, de menos a mas util: `point_is_clear(x, z)` o el caso
  general `has_line_of_sight(from, to)`. La segunda responde ademas «¿ve el jugador este
  objeto desde donde esta?». No hace falta verbo nuevo del bridge si se compone sobre
  `scene_raycast` + `surface_query`, que ya existen — misma via barata que `wait_for`.
