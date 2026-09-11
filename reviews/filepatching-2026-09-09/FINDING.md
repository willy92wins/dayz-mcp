# -filePatching: medio resuelto, y el resto ya esta armado

Todo lo de aqui sale de datos que YA estaban en disco. No hizo falta una corrida.

## La carpeta que el handoff daba por vacia no lo estaba

`@DayZ_MCP\DayZ_MCP\` existe y tiene **un** fichero:

    @DayZ_MCP\DayZ_MCP\gui\layouts\hot\mcp_hot.layout   8324 bytes, mtime 2026-08-19

No esta en el arbol fuente (`addon/`) y por tanto no se empaqueta. Alguien lo dejo ahi
el 19 de agosto justo para probar si el motor lo servia.

## El experimento ya volo, y nadie miro el resultado

`MCPDialogController.c:34-43` lleva una lista de sondeo con un comentario que pide
exactamente una corrida:

    // Hot-layout probe (2026-08-19). [...] Untested, and worth one flight:
    // whether it resolves a path that is NOT in the PBO at all.

Volo en la corrida `25dc3c98` de esta madrugada. Del log del cliente
(`_client/profiles/script_2026-09-09_00-37-27.log`):

    probe idx=0 path=$profile:mcp_hot.layout                     file_exist=0
    probe idx=1 path=DayZ_MCP/gui/layouts/hot/mcp_hot.layout     file_exist=0
    probe idx=2 path=DayZ_MCP/gui/layouts/mcp_dialog.layout      file_exist=1
    probe WINNER=DayZ_MCP/gui/layouts/mcp_dialog.layout

`file_exist` es `FileExist(candidate)`, la comprobacion del propio motor
(`MCPDialogController.c:169`).

Premisas comprobadas, no supuestas:

| premisa | como se comprobo |
|---|---|
| el fichero estaba ahi durante la corrida | mtime 2026-08-19, tres semanas antes |
| `-filePatching` estaba activo | `_client/profiles/DayZDiag_x64_2026-09-09_00-37-25.RPT`: `-mod=P:\Mods\@DayZ_MCP ... -filePatching` |
| el .RPT es el de ESA corrida | 00-37-25 contra el script log 00-37-27 |

**Resultado: el motor NO resuelve un fichero suelto puesto en la carpeta del mod.** El
mismo `file_exist=0` prueba de paso que no estaba empaquetado, asi que no hay confusion
posible entre las dos causas.

## Lo que ESO no prueba, y es la mitad que faltaba

Hay dos sitios candidatos y la corrida solo mato uno:

- **(a) `<carpeta del mod>\DayZ_MCP\`** -- donde vive `mcp_hot.layout`. **REFUTADO.**
- **(b) `P:\DayZ_MCP\`** -- el destino de `deploy-addon.ps1`, y la convencion clasica de
  Bohemia: filePatching busca bajo `P:\<PBOPREFIX>\`, y `$PBOPREFIX$` aqui es `DayZ_MCP`.
  **SIN PROBAR**, porque el unico fichero no empaquetado del sistema estaba en (a).

Que (b) sea la convencion habitual lo convierte en la hipotesis fuerte, no en la
descartada. Si (b) funciona, `deploy-addon.ps1` ya copia al sitio correcto y el
empaquetado deja de ser obligatorio para iterar scripts.

## Experimento armado (coste cero en la proxima corrida)

Copiado, el 2026-09-09:

    P:\DayZ_MCP\gui\layouts\hot\mcp_hot.layout    sha256-16 3F27EC8B553F7617, 8324 bytes

El sondeo corre en cada arranque de cliente y ya escribe su veredicto. **No hay que
hacer nada durante la corrida**: arrancar y leer la linea `probe idx=1`.

    file_exist=1  -> (b) SIRVE. filePatching funciona desde el arbol de deploy y cada
                     cambio de script deja de costar un empaquetado.
    file_exist=0  -> (b) tampoco. filePatching no alcanza este mod por ninguna via y la
                     linea de comandos lo lleva de adorno; toca empaquetar siempre.

**CONDICION DE NULIDAD.** Si se reconstruye el PBO con el fichero presente en el arbol
de deploy, el empaquetador lo incluira y un `file_exist=1` no probara nada. Huella del
PBO vigente:

    sha256-16 B19882F0814328B4    237401 bytes    2026-09-09 01:49:01

Antes de creerse un positivo, comprobar que el PBO sigue siendo ese. Si cambio, rehacer
el armado: **empaquetar PRIMERO y copiar el fichero DESPUES**.

Ya paso una vez, el mismo dia: el empaquetado que llevo el techo de admision del cliente
habria anulado el experimento. Se resolvio aparcando el fichero fuera del arbol de
deploy, empaquetando, y devolviendolo. Comprobado por bytes, con control positivo:

    contenido de mcp_hot.layout dentro del PBO     -> False
    contenido de mcp_dialog.layout dentro del PBO  -> True   (control: sabe detectarlo)

Ojo con `grep -c` sobre el PBO para esto: cuenta LINEAS, y en un binario las lineas
dependen de donde caigan los bytes de salto. Dio 1 antes y 2 despues sin que cambiara
nada relevante. La comprobacion buena es contar ocurrencias del contenido, no lineas.
