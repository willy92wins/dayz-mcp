# Claude Opus 5 — revisión calibrada de planes, grupo A

- Provider verificado: `anthropic`
- Model verificado: `claude-opus-5`
- Thinking: `max`
- Primera sesión: `01a053e8-f58a-7290-9e0d-030e34af02e7`
- Sesión calibrada: `01a053f9-a2b3-7516-80fe-956d9c71c987`
- Continuidad: fork de la primera sesión; la salida calibrada sustituye íntegramente la primera.
- Cierre primera respuesta: `stopReason=stop`
- Cierre calibrado: `stopReason=stop`
- Ceguera frente a Sonnet: verificada; no hubo lecturas de outputs Sonnet.
- Lecturas bajo `reviews/**`: `common.md` en la sesión padre y `calibrate-a.md` en calibración; ningún tercer artefacto.
- Mutaciones/delegación/MCP/red/DayZ: ninguna.
- Validación mecánica del orquestador: 10 IDs únicos, rutas y SHA contra bytes/manifest; 9 PASS con `FINDINGS: - NONE`, 1 REVISE; un único `GROUP_VERDICT`.

## Salida calibrada literal

Calibración ejecutada en la misma sesión. Releí `common.md` y `calibrate-a.md` (únicos ficheros abiertos bajo `reviews/**`), reabrí los diez planes sin editar, y reabrí `MCPClientBridge.c`, el feedback fuente de f6ac, el contrato UI del DAG y las tres serializaciones señaladas. Sin drift: las cinco autoridades y los diez planes siguen dando el mismo SHA-256. Uso de la traducción `P:/X` verificada; ninguna cita `P:` quedó inaccesible. No abrí la ficha de grupo desde disco.

Nota de alcance: abrí `…/items/fb-20260828-212912-f6ac.md` porque `calibrate-a.md` ordena reabrir «el feedback f6ac» y `common.md` autoriza expresamente los items de ese directorio como fuente, no como salida Sonnet.

```
ID: 3bb4
PLAN: plans/inbox-20260830/01-fb-20260828-211445-3bb4.md
SHA256: 560469b27e8b1cbfbdb48e6792636ae7f3860e42eb934970b5fce6454fde67ee
VERDICT: REVISE
FINDINGS:
- plans/inbox-20260830/01-fb-20260828-211445-3bb4.md:35-36 (con :30) — la matriz de veredictos del gate de tecla puede puntuar FAIL una implementación correcta. Tres ramas de fuente producen el mismo observable «delivered=true, panel abierto» sin que nada esté roto: P:/LFPowerGrid/scripts/5_Mission/LFPG_MissionInit.c:165-170 (CCTV activo consume la tecla y hace return), :176-183 (LFPG_SorterView de producción se evalúa antes que la rama TEST de :185-192) y P:/LFPowerGrid/scripts/4_World/test/LFPG_SorterView_TEST.c:1395-1404 (con EditBox enfocado el primer DIK 1 sólo hace SetFocus(null) y retorna sin cerrar). La línea :35 lista «flag de entrega sin efecto» como FAIL, y la :36 sólo admite INCONCLUSIVE por drift, cliente no listo o respawn inseguro, así que ese observable cae en FAIL. Agrava el problema que la precondición de :30 («sin foco en EditBox») no tiene mecanismo: addon/scripts/5_Mission/MCPClientBridge.c:1507-1576 muestra que el único verbo de foco es ui_focus, que FIJA foco y sólo informa el resultante; no existe lectura de foco previo. Además la propia superficie de esta ronda puede dejar un EditBox enfocado (server.py:4069 ui_focus, :3992 ui_set_text sobre los EditBox del panel en LFPG_SorterView_TEST.c:120,790-808), luego el escenario no es exótico. Corrección concreta y mínima: en :30 exigir acreditación explícita de la precondición (CCTV inactivo, LFPG_SorterView cerrado y foco retirado de todo EditBox, p. ej. con ui_focus sobre un widget no-EditBox del panel) y añadir en :36 «foco previo no acreditado o setup que no alcanza al consumidor = INCONCLUSIVE», exactamente como ya redactan sus hermanas de paquete 7743 (:26) y 1082 (:27).
WHY: Hash y línea de manifest coinciden. Las citas [EXACT] vuelven a abrir correctamente: server.py:3687-3715 registra ambos verbos con peer "client" (:3700, :3714) y rechaza bool/negativo (:3696); MCPClientBridge.c:688-695 despacha y :764-785 llama exactamente GetGame().GetMission().OnKeyPress(dik) en :781; missionbase.c:342-352 y missiongameplay.c:719-723 son los overrides; ingamemenu.c:345-362 es GameRespawn y la receta dirty arranca en MCPClientBridge.c:813; LFPG_MissionInit.c:163-204 enruta DIK 1 al panel TEST y traga el resto. Disposición EVIDENCIA coincide con Fase 0 (research:165) y P01; DPF B4 con su Intent «conducir sin input del SO» (product-spec.md:50,57). OWNS sin source dentro de M00/M24/M25 (DAG:34,58,59), y la cláusula de clausura transitiva cita bien DAG:34-59 con M01→M00. El gate de respawn es ejecutable (player_teleport en server.py:3284) y la exigencia de sesión MCP postdespliegue es no tautológica y no depende de P11. En calibración busqué expresamente el falso REVISE y no se sostiene: la lectura benévola de :35 («cerrar apoyándose en el flag») es la que usa 1082 con otras palabras («flags sustituyen efecto»), mientras 3bb4 describe el síntoma observado, y aun bajo la lectura benévola el observable de las tres ramas quedaría sin veredicto definido en :34-36. Es el mismo autor, el mismo paquete y el mismo mecanismo endurecidos dos veces en esta ronda; aquí no. El coste del defecto es asimétrico: un FAIL falso reabre como cambio un verbo que el propio plan prohíbe reimplementar (:28).
```

```
ID: f6ac
PLAN: plans/inbox-20260830/02-fb-20260828-212912-f6ac.md
SHA256: 2439717a0a0ca78dbf106bbf25adabbd5687649c5681e8d1168551df3b2877a6
VERDICT: PASS
FINDINGS:
- NONE
WHY: Reabierto el feedback fuente: pide ui_click sobre botones de ScriptView custom, reporta not_handled en BtnCloseX y BtnClose, y marca su causa como «Hipotesis de causa», no como hecho. El plan trata esa hipótesis como refutable y no la hereda. Petición completa comprobada contra el árbol actual: en P:/LFPowerGrid/gui/layouts/test/LFPG_Sorter_TEST.layout el único botón de cierre vivo es BtnCloseX (:289, con BtnCloseXBg :297 y BtnCloseXText :310); el segundo nombre del repro, BtnClose, ya no existe en ese layout ni en P:/LFPowerGrid/scripts/4_World/test/LFPG_SorterView_TEST.c, así que acreditar handler+506+efecto cubre la ruta superviviente, y el RED genérico de ScriptView cubre la afirmación general del feedback. Citas verificadas de nuevo: MCPClientBridge.c:2155-2190 contiene el camino tipado por GetScript/GetUserData y los dos fallbacks reflectivos CallFunctionParams("OnClick") (:2166, :2185); :1394-1407 separa no_handler (handlerName vacío) de not_handled tras fijar user_id/handler en :1391-1392; :1964-2037 acepta un resultado sin contar multiplicidad; product-spec.md:57 exige literalmente «un ScriptView muta estado» y «homónimos sin raíz devuelven ambiguous_path»; DAG:97-102 congela root, ambiguous_path y los ecos, y el grep del addon confirma que esos símbolos aún no existen, tal como declara el [DESIGN]. La tensión obligatoria resiste la lectura adversarial: el paso 4 y los criterios exigen «efecto» en el PASS de cierre, clasifican handler/ID correctos sin efecto como FAIL funcional con la causa original refutada pero la ficha no resuelta, y la sección de compatibilidad lo repite («Refutar una atribución sin recuperar el efecto no autoriza cerrar»). El pre-cierre del preview es ejecutable hoy: mode="close" está implementado en el bridge (MCPClientBridge.c:1430,1455-1467) además del schema (loopback.py:646-654) y la tool (server.py:4022-4058). El 506 es comprobable (LFPG_SorterView_TEST.c:306, asignado en :710-712, despachado en :1054) y user_id=0 sólo ocurre si AssignButtonIDs (:639) no corrió, que el plan manda a INCONCLUSIVE. OWNS no invade el resolver de f4f2 ni MCPMessages.c y M22 respeta el dueño único de server.py (DAG:56).
```

```
ID: 7743
PLAN: plans/inbox-20260830/04-fb-20260829-022838-7743.md
SHA256: b57def41a4f5846199a5c2ae5926071e52e6def1664288a8ec3b6ca9911812a3
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. Citas reabiertas: server.py:3687-3715 y MCPClientBridge.c:688-695 para el input registrado con peer client; :2144-2207 invoca el handler manualmente y devuelve su bool parando en el primero; server.py:669-679 eleva ToolError con cualquier ok falsy y _bridge_error (:438-447) conserva sólo código y object_id, perdiendo handler/user_id; DAG:96-110 fija cuatro verbos, scope workspace/raíz, ecos dedicados, direct legacy y complete tras RED de viabilidad; LFPG_MissionInit.c:163-204 y LFPG_SorterView_TEST.c:1378-1410 son el consumidor medible. Ficha compuesta sin contagio entre mitades: veredicto separado, «Ninguna mitad hereda PASS» y rollback por módulo que revalida el otro. En el mismo punto donde 3bb4 falla, ésta acierta: el paso 4 acredita ausencia de foco EditBox, registra aparte el caso con foco donde el primer DIK 1 sólo lo limpia —comportamiento que confirma LFPG_SorterView_TEST.c:1395-1404— y manda INCONCLUSIVE si el foco previo no puede acreditarse, de modo que la precondición no acreditada no se convierte en FAIL. DPF correcto (B4, no C3). OWNS coherente con el DAG: M05 sobre regiones UI de MCPClientBridge.c (DAG:39, «mitad 7743»), M06 consumido y no editado (DAG:40 lo asigna a b2c4 y a los schemas de f4f2/20be/2762), M22 sobre server.py (DAG:56), M00 sin source (DAG:34); M04→M05 le llega por la cláusula de clausura transitiva que cita DAG:34-59, donde la línea 39 declara esa arista. Verificadores no tautológicos: sesión MCP creada tras el despliegue, estados opuestos del panel entre DIK de control y DIK 1, y efecto propio del ScriptView; el FAIL «UI se cierra por input» impide acreditar la mitad UI con un cierre por ESC.
```

```
ID: 47c9
PLAN: plans/inbox-20260830/15-fb-20260829-104543-47c9.md
SHA256: 08d63bb3b738fadcf156f4c06ef609f9f6f39b0fe7968c0212fcb47185d75711
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. Citas reabiertas: MCPClientBridge.c:2163-2190 contiene el camino por el que un handler no tipado se alcanza por reflexión y el handler tipado de userdata (:2176-2181), que es justamente por donde entra una ScriptView cuyo layout root lleva SetUserData; el rango es real y, como afirma la ficha, no acredita efecto. :1363-1390 muestra ui_click resolviendo por ResolveUiRoot y :1964-2037 el lookup global sin conteo. product-spec.md:57 exige mutación del ScriptView, no `clicked`. DAG:97-110 congela root, ecos y modos; ambiguous_path sigue sin existir en el addon, luego el [DESIGN] no se disfraza de símbolo vivo. Disposición fiel a Fase 0 (research:151, «prioridad satisfecha por P03»): el plan prohíbe explícitamente la rama `if ScriptView` y una segunda edición de loopback.py, que es la trampa que su propio feedback hermano insinuaba. OWNS limpio y verificado contra el DAG: M05 sobre el dispatch UI del bridge (DAG:39), M06 declarado dependencia consumida y no poseída (DAG:40), M22 sobre server.py (DAG:56); no reclama M02, y no lo necesita porque sus criterios usan sólo campos existentes de MCPResult (MCPMessages.c:463-467) y el error ya existente. Rollback correcto en granularidad: coordina M05/M22 y revalida el contrato M06 sin revertir bytes de loopback.py de otros feedbacks, que es lo que exige la convivencia con M06→M17 (DAG:51). Gates no tautológicos: dos acciones ScriptView con dos marcadores distintos, homónimo fail-closed y declinante que conserva payload; el FAIL cubre `clicked` sin mutación y el primer match global.
```

```
ID: 21f5
PLAN: plans/inbox-20260830/24-fb-20260829-184906-21f5.md
SHA256: 801c818a1a50eb10f35d42975dc17889e96a3428f7737518dac0914b078b8dd9
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. Citas reabiertas: MCPClientBridge.c:2140-2144 declara y documenta que InvokeUiClick devuelve si el handler CONSUMIÓ, no si se encontró; :1394-1407 asigna no_handler/not_handled de forma distinta; server.py:669-679 pierde el payload en ok=0 y M22 es la frontera correcta; el item .../items/fb-20260829-184906-21f5.md:68-79 contiene exactamente la desambiguación 506 / 0 / handler vanilla y la orden de ejecutar antes ui_reload_layout(mode="close"), que el bridge implementa de verdad (MCPClientBridge.c:1430,1455-1467). La terna es comprobable en fuente: UID_CLOSE_X=506 (LFPG_SorterView_TEST.c:306), asignado en :710-712 por AssignButtonIDs (:639) y despachado en :1054. En calibración probé la hipótesis de falso PASS más plausible —que atar el cierre de una ficha semántica al efecto del panel real produzca un FAIL injusto cuando el verbo MCP se comportó bien— y el propio item la desactiva: en :18-19 declara «ANEXO de fb-20260828-212912-f6ac y fb-20260829-022838-7743. Hereda su estado: mientras esas sigan abiertas, esta tambien». Atar el cierre al caso real es fidelidad a la petición, no exceso. Sus criterios propios son semánticos y no confunden causa con efecto: FAIL sólo por códigos colapsados, por narrar not_handled como ausencia o por abrir payload a un fallo no-UI. OWNS acotado y sin solape: no reclama loopback, resolver ni otros verbos, y su M22 es sólo el test público, por lo que no produce bytes para b2c4. Fail-closed intacto (DAG:105-106) y matriz cerrada que nunca colapsa un caso a «ScriptView no soportado».
```

```
ID: 20be
PLAN: plans/inbox-20260830/25-fb-20260829-184952-20be.md
SHA256: 6af9ce319470eff791600e9da84b0df3a4ff83ba99b664280e3308adc57da29c
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. Citas reabiertas: P:/scripts/1_core/proto/enwidgets.c:656-669 declara OnClick (:658), OnMouseButtonDown (:668) y OnMouseButtonUp (:669); :153-154 declara GetScreenPos/GetScreenSize y MCPClientBridge.c:2064-2073 ya las copia al nodo; :2152-2193 pasa literalmente (0,0) en todas las invocaciones y para en el primer handler subiendo por GetParent(). La cuarta afirmación (ni dispatcher ni espacio coordenado en la fuente primaria) la volví a atacar en esta pasada: el árbol de scripts accesible sólo declara prototipos, no documenta el espacio de x,y y no contiene el dispatcher, que es nativo; la afirmación es cierta y, sobre todo, no autoriza nada: condiciona `complete` a un RED que discrimine, con INCONCLUSIVE si la coordenada no discrimina y entrega sólo de direct si falla, tal como manda DAG:107-110. OWNS coherente por fichero, módulo y campo: M02 sobre MCPMessages.c (DAG:36 y :91-92) —necesario de verdad, porque MCPArgs (MCPMessages.c:43) hoy tiene mode (:63), path (:69) y button (:71) pero no `bubble`—, M05 sobre DispatchUiClick/InvokeUiClick después de M04 por fichero compartido (DAG:39, :74-75), M06 sobre el schema de loopback.py (DAG:40) y M22 sobre server.py (DAG:56). No hay contradicción con f4f2 sobre `direct`: 20be congela el walk de handler legacy y f4f2 aclara que lo que deja de existir es la selección «primer homónimo», que es la resolución, no el walk. Compatibilidad y rollback correctos: complete y bubble son aditivos y el rollback elimina sólo el modo nuevo. Gates no tautológicos: orden down→up→click al centro medido y bubbling que sólo continúa tras declinación explícita, con FAIL si el padre recibe con bubble=false.
```

```
ID: b2c4
PLAN: plans/inbox-20260830/28-fb-20260829-221423-b2c4.md
SHA256: 1301e22f8432f9d0f099c663fb4886a9f849f601b512d0afb50a3a86b515c197
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. La cita clave se reconfirma como exhaustiva: los únicos cuatro puntos de server.py que elevan ok=0 son 678, 708, 1309 y 1381 (Runtime.wait_for_result, Runtime.probe_bridge_result, ClientRuntime._await_result, ClientRuntime.probe_bridge_result), exactamente los cuatro rangos citados; no queda un quinto camino fuera de la allowlist, y _bridge_error (:438-447) conserva sólo código y object_id. MCPClientBridge.c:1389-1407 rellena user_id/handler antes de elegir el código. La exclusividad por verbo se sostiene con grep sobre el addon: text_not_writable sólo en :1347, no_handler en :1402, not_handled en :1406, focus_not_taken en :1575 y widget_not_found en :1989; ambiguous_path no existe aún, como declara el [DESIGN] anclado en DAG:96-106 y :103-106. Reabrí los cuatro dispatches y los cuatro resuelven por el mismo ResolveUiRoot (:1270, :1305, :1381, :1536), así que «resolución ambigua/ausente en los cuatro verbos» es materializable en un punto y la matriz del plan es medible. En calibración probé el falso PASS por granularidad de rollback frente a M06→M17 (DAG:51 pone un hook de run_command_activity en loopback.py después de M06): el plan revierte «allowlist M22, schema M06 y tests», es decir conjuntos de cambio, no el fichero, y el DAG:193-196 obliga a entregar `git diff -- <OWNS>` por módulo, de modo que no colisiona con el hook posterior. OWNS correcto (M06 dueño principal en DAG:40, M22 dueño único de server.py en DAG:56) y renuncia explícita a M05. Fail-closed intacto para bad_args, auth, cola, versión, timeout y readiness en los cuatro transportes, con FAIL si un error específico se admite en un verbo no aplicable. Compatibilidad declarada sin adornos: el consumidor que capturaba excepción deberá aceptar el dict.
```

```
ID: f4f2
PLAN: plans/inbox-20260830/29-fb-20260829-230535-f4f2.md
SHA256: 40fde5fba2c87f18ee083165a10971c76251623982cd6cce5b7b1bb8fa35df04
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. Citas reabiertas: MCPClientBridge.c:1964-1992 llama workspace.FindAnyWidget(args.path) (:1982) y cae a un walk global (:1985) sin contar coincidencias; :2013-2037 devuelve el primer match recursivo; enwidgets.c:168-170 distingue FindWidget por pathname de FindAnyWidget por nombre, que es la raíz del defecto de scope; DAG:97-100 reproduce fielmente el contrato congelado, incluido que sólo ui_tree con ambos vacíos conserva el active-menu legacy, comportamiento que hoy vive en :1995-2010. En calibración verifiqué que su OWNS es suficiente y no excesivo: los cuatro verbos públicos entran por el mismo ResolveUiRoot (:1270, :1305, :1381, :1536) y devuelven su error tal cual (:1274, :1309, :1385), así que cambiar «resolver y walk» en M05 alcanza a los cuatro sin tocar nada más; y `root` exige campo nuevo en MCPArgs (MCPMessages.c:43-71, donde no existe), que es exactamente lo que reclama por M02 (DAG:36, :91-92). M04 precede a M05 por fichero compartido (DAG:39, :74-75) y M05/M06 convergen en M22 (DAG:56, ola 3 en :69). El [DESIGN] es honesto: root y ambiguous_path no aparecen en el addon. Distinción fina y correcta: direct conserva lookup y retorno del handler legacy, pero no la selección «primer homónimo», que DAG:100 prohíbe. FAIL cubre la regresión inversa (reducir el scope a GetMenu y ocultar ScriptView) y el gate exige demostrar que el homónimo nunca produce side effect; la compatibilidad admite sin eufemismos que los homónimos pasan de side effect incierto a fail-closed.
```

```
ID: 2762
PLAN: plans/inbox-20260830/33-fb-20260830-112422-2762.md
SHA256: 0cb24c1db758d8b50720b9e024f630e100233cc3e90a3665ad6613d10486ceb4
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. Citas reabiertas: MCPMessages.c:463-467 declara exactamente los campos UI actuales (ui, clicked, handler, user_id) bajo el comentario de verbos UI; server.py:3973-4020 son ui_tree/ui_set_text/ui_click y :4060-4079 ui_focus, y ninguno emite eco dedicado; DAG:96-110 y :101-102 congelan requested_path, requested_root, requested_text sólo en set-text y matched_path, y ninguno existe en el addon. En calibración busqué el falso PASS por dependencia oculta en un fichero sin dueño: los ecos nuevos son escalares/claves desconocidas y tools/dayz_mcp/result_prune.py:68-85 sólo poda los contenedores `ref` de PRUNABLE_FIELDS (:31-46), dejando intactos escalares y claves nuevas (:71-73), así que no hace falta tocar un módulo ajeno; y la supresión por verbo que exige DAG:101-102 cabe en server.py, del que M22 es dueño único (DAG:56) y que esta ficha reclama. El criterio más exigente está bien apuntado contra la fuente: «set-text conserva texto incluso con fallo de negocio» ataca el retorno temprano de text_not_writable en MCPClientBridge.c:1346-1348, anterior al snapshot de :1354-1359, luego el relleno debe ocurrir antes; eso es M05, que la ficha posee. Los negativos son fuertes y no tautológicos: prohíben reutilizar matched_path/handler/user_id de una llamada previa antes del match o en ambigüedad, y exigen que en no_handler el handler quede vacío mientras user_id sigue ligado al match, coherente con :1391-1392. FAIL castiga que M22 reconstruya un eco distinto del bridge. Campos aditivos, respuestas legacy parseables y rollback conjunto de M02/M05/M06/M22.
```

```
ID: 1082
PLAN: plans/inbox-20260830/35-fb-20260830-112522-1082.md
SHA256: 756e791d388cb867c18d450e324ed574a01073fadcec6a7c751252c2266ea280
VERDICT: PASS
FINDINGS:
- NONE
WHY: Hash y manifest coinciden. Citas reabiertas: server.py:3687-3715 registra ambos verbos con peer client; MCPClientBridge.c:688-695 los enruta y :764-785 llama Mission.OnKeyPress(dik) en :781; MCPMessages.c:468-472 declara delivered/dik y requested, es decir transporte y no efecto; product-spec.md:57 exige que la tecla cambie el estado del consumidor. Su cita del consumidor sigue siendo la más precisa del lote y coincide byte a byte: LFPG_MissionInit.c:163-204 enruta DIK 1 al panel y traga el resto, y LFPG_SorterView_TEST.c:1388-1411 implementa el doble-ESC —con EditBox enfocado el primer DIK 1 sólo hace SetFocus(null) y retorna (:1398-1403); sin foco registra s_EscCloseTime y cierra (:1408-1410)—. Esa asimetría está trasladada a los criterios, que es justo lo que falta en 3bb4: PASS exige estados opuestos entre control y DIK 1 sin foco EditBox, el caso con foco se cubre aparte, y tanto «foco previo no acreditado» como «setup no alcanza consumidor» son INCONCLUSIVE, no FAIL. La reemisión no hereda nada: el paso 1 rehashea source/tests contra la evidencia recibida y el drift invalida la aprobación histórica; el paso 3 exige sesión MCP abierta tras el despliegue, prohíbe usar una sesión anterior y prohíbe alterar código para forzar coincidencia, todo sin depender de P11. El gate de respawn es ejecutable con la superficie actual (player_teleport en server.py:3284) y exige identidad viva/controlable lejos de la posición previa, no flags; su FAIL «flags sustituyen efecto» condena el método, no el observable. OWNS sin source dentro de M00/M24/M25 (DAG:34,58,59).
```

```
GROUP_VERDICT: REVISE — 9 PASS (f6ac, 7743, 47c9, 21f5, 20be, b2c4, f4f2, 2762, 1082) y 1 REVISE (3bb4). Esta salida sustituye íntegramente la primera; no es un delta. La calibración adversarial mantuvo los diez veredictos, pero cambió su base probatoria en tres puntos: (a) el falso PASS candidato en f6ac por petición incompleta queda descartado al reabrir el feedback fuente y comprobar que el segundo botón del repro, BtnClose, ya no existe en P:/LFPowerGrid/gui/layouts/test/LFPG_Sorter_TEST.layout, donde sólo vive BtnCloseX (:289); (b) el falso PASS candidato en 21f5 por atar una ficha semántica al efecto del panel queda descartado porque su propio item declara en :18-19 que hereda el estado de f6ac/7743; (c) el falso PASS candidato en 2762 por depender de un fichero sin dueño queda descartado porque result_prune.py:31-46,68-85 no poda escalares ni claves nuevas. También releí el REVISE buscando falso positivo y se confirma: 3bb4 es la única ficha del lote cuyo gate convierte un observable producido por ramas legítimas de la fuente en FAIL, mientras sus dos hermanas de paquete lo mandan a INCONCLUSIVE. Comprobaciones transversales exigidas: el contrato UI del DAG queda cubierto por las citas desplazadas —root/scope en :97-100, ecos en :101-102, matriz de errores y fail-closed en :103-106, direct/complete en :107-110—; los cuatro verbos comparten un único ResolveUiRoot (MCPClientBridge.c:1270, 1305, 1381, 1536), lo que hace materializable esa matriz en un punto; y las tres serializaciones se sostienen: M04→M05 por fichero compartido (DAG:39, :74-75), M06→M17 sin colisión porque ninguna ficha del lote reclama loopback.py fuera de su ventana ni revierte el fichero completo (DAG:51, :193-196), y M05/M06→M22 con M22 como dueño único de server.py (DAG:56, ola 3 en :69). Los diez planes llevan la cláusula de clausura transitiva citando DAG:34-59 con M01→M00, así que ningún módulo se adelanta por omisión. Ninguna ficha hereda veredicto de su vecina ni de su paquete, y ningún nombre [DESIGN] fue penalizado por no existir todavía. Validez de la corrida: la confirmación de provider=anthropic, model=claude-opus-5 y stopReason=stop corresponde al orquestador; no la he fabricado ni inferido.
```

**Cumplimiento:** dentro de `reviews/**` sólo leí `common.md` y `calibrate-a.md`; no abrí la ficha de grupo desde disco, no busqué ni recibí salidas Sonnet, no usé revisiones históricas ni copias de build/deploy como sustituto del source, y no hice búsquedas recursivas sobre `reviews/`. Sin delegación, sin ficheros creados, sin escrituras, sin MCP/red y sin lanzar DayZ.

