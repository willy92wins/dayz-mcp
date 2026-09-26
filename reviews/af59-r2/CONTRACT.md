# CONTRACT -- `dayz-test.ps1` compartido: la superficie observable

Lo escribe el orquestador. **Es el unico punto de acuerdo entre dos lanes que no se ven**:
una escribe el script, otra escribe el arnes que lo juzga. Si algo aqui te parece ambiguo,
**declaralo en tu `STATE.md` y elige la lectura mas estricta**; no lo resuelvas inventando
una convencion nueva.

## 1. Invocacion

El script se invoca siempre asi, desde cualquier directorio:

    powershell -NoProfile -ExecutionPolicy Bypass -File <ruta>\dayz-test.ps1 `
        -Mod <Nombre> -Build -BuildOnly [-PackOnly] [-Clean] [-Release] [-Source <dir>]

`-BuildOnly` construye y **sale sin lanzar el juego**. Es el unico camino que se ejercita.
Nada de esto arranca DayZ, ni el daemon MCP, ni el AddonBuilder real.

## 2. Overrides de entorno

El tronco ya documenta la convencion "DAYZ_* env var -> Steam default" y ya la usa para
`DAYZ_GAME_PATH`, `DAYZ_DIAG_PATH`, `DAYZ_TOOLS_PATH` y `DAYZ_WORK_DRIVE`. El contrato la
extiende con dos mas:

| variable | que sustituye | quien la fija |
|---|---|---|
| `DAYZ_DEV_ROOT` | `$DevRoot`, hoy `Split-Path -Parent $PSScriptRoot` | el shim de cada mod, y el arnes |
| `DAYZ_WORK_DRIVE` | `$WorkDrive`, hoy `'P:\'`; de el sale `$ModsDir = <WorkDrive>\Mods` | el arnes |
| `DAYZ_ALLOW_PLAIN_MODS` | `1` autoriza una carpeta Mods normal YA existente como destino intencional; no autoriza crear Mods | operador / arnes |
| `DAYZ_ADDONBUILDER` | la ruta al ejecutable de AddonBuilder | el arnes |

Las dos nuevas son `DAYZ_DEV_ROOT` y `DAYZ_ADDONBUILDER`. Con `DAYZ_ADDONBUILDER` apuntando
a un `.cmd`, `Start-Process -FilePath` lo ejecuta igual que a un `.exe`.

## 3. Layout que el arnes monta y el script tiene que aceptar

    <tmp>\af59\dev\<Mod>_dev\tools\         DAYZ_DEV_ROOT = <tmp>\af59\dev\<Mod>_dev
    <tmp>\af59\dev\<Mod>_dev\_server\
    <tmp>\af59\dev\<Mod>_dev\_client\
    <tmp>\af59\P\                            DAYZ_WORK_DRIVE = <tmp>\af59\P
    <tmp>\af59\P\<Mod>\config.cpp            fuente del mod
    <tmp>\af59\P\<Mod>\scripts\5_Mission\dummy.c
    <tmp>\af59\P\Mods\@<Mod>\Addons\         destino; aqui vive <Mod>.pbo
    <tmp>\af59\ab\AddonBuilder.cmd           DAYZ_ADDONBUILDER

**El camino `-Build -BuildOnly` tiene que ser alcanzable con ESTE layout**: sin `P:` real,
sin DayZ instalado y sin DayZ Tools. El arnes prepara `Mods` de antemano y fija
`DAYZ_ALLOW_PLAIN_MODS=1`. No se permite que el sujeto fabrique `Mods` si falta,
ni siquiera con ese override. Puede crear `@<Mod>\Addons` dentro de un destino preparado.

Por defecto, `Mods` debe ser un directorio existente de tipo reparse point (junction o
link resoluble). Es una convencion de este host, no una propiedad universal de DayZ.
El operador puede aceptar una carpeta normal existente mediante el override explicito.
Ni un reparse point ni ese override prueban por si solos que el motor lee ese destino:
el operador debe preparar el mapping correcto. No se comprueba una instalacion real.
El preflight de build se ejecuta dentro de Invoke-Build y tambien con
`-BuildOnly -Preflight` / `-Mode none -Preflight`; comprueba destino, fuente y builder,
no juego, mission ni herramientas distintas del builder seleccionado.
El preflight general tampoco crea un junction automaticamente.

## 4. Salida observable -- es lo unico que se mira

- **Exito**: exit code **0** y stdout contiene la subcadena `[ok] deployed:`
- **Rechazo**: exit code **distinto de 0** y stdout **no** contiene `[ok] deployed:`

El resto del texto es libre: los mensajes de diagnostico no son contrato.

## 5. Los nueve escenarios

El stub de AddonBuilder se comporta como le diga el arnes. Cada fila es una corrida
independiente del script completo.

| # | el stub imprime | exit del stub | que hace con el PBO | veredicto esperado | quien lo caza |
|---|---|---|---|---|---|
| S1 | `Build Successful` | 0 | escribe uno NUEVO (bytes distintos) | **exito** | control positivo |
| S2 | `[ERROR]: Build failed` | 0 | no lo toca | **rechazo** | texto y hash |
| S3 | `[ERROR]: Build failed` | 0 | escribe uno NUEVO | **rechazo** | **solo el texto** |
| S4 | `Build Successful` | 0 | no lo toca | **rechazo** | **solo mtime/hash** |
| S5 | nada | 0 | no lo toca | **rechazo** | falta `Build Successful` |
| S6 | `Build Successful` | 3 | escribe uno NUEVO | **rechazo** | el exit code |
| S7 | `Build Successful` | 0 | lo CREA (no habia PBO previo) | **exito** | primer build |
| S8 | `Build Successful` | 0 | escribe PBO valido; hay un sentinel anidado en el temp del mod | **exito**, sentinel ausente al entrar al stub | borrado de temp |
| S9 | `Build Successful` | 0 | escribe PBO valido sin entradas .c, con fuente solo-scripts | **rechazo** | conteo .c siempre activo |

Los PBO nuevos de S1-S8 contienen una entrada real `scripts\5_Mission\dummy.c`.
S9 contiene `config.cpp`; el cuerpo lleva un texto senuelo `.c` seguido de NUL, que no
cuenta como entrada. El contador lee la tabla PBO, no busca texto en sus primeros 4 MiB.
Si `scripts\` contiene .c, debe haber al menos ese numero de entradas .c en el PBO,
con packonly automatico, explicito y binarizacion. Una tabla ilegible se rechaza.
S8 comprueba tambien que no se borra el temporal de otro mod.

Mutaciones sobre copias: borrar el bloque de wipe solo hace fallar S8; borrar el bloque
de conteo solo hace fallar S9. Cada corrida mutada debe dar PASS=8 FAIL=1 y exit 1.
El gate normal debe dar PASS=9 FAIL=0 y exit 0.

**S3 y S4 son el par que discrimina**: si se borra el chequeo de texto, S3 se vuelve verde;
si se borra el de mtime/hash, S4 se vuelve verde. Los dos controles quedan asi demostrados
como portantes, uno por uno.

**S7 es el falso positivo simetrico**: un primer build legitimo, sin PBO anterior, tiene que
SOBREVIVIR al filtro. Un arnes que solo prueba rechazos no mide nada.

En S2, S4 y S5 el PBO previo existe y el arnes comprueba ademas que **sigue byte a byte
igual** despues de la corrida.

## 6. Comprobacion adicional sobre los argumentos (C1)

El stub deja en un fichero de log la linea de argumentos que recibio. Durante S1 se
comprueba que:

    el argumento -temp=<ruta> existe, y <ruta> NO empieza por el valor de DAYZ_WORK_DRIVE

Esa es la otra mitad del arreglo de af59: el `temp` de AddonBuilder tiene que vivir **fuera**
del disco de trabajo, porque una sesion concurrente con `-addon="P:"` deja ficheros ajenos
abiertos y el "Clearing temp folder" falla dejando el exit code en 0.

## 7. El bug, en una frase

AddonBuilder imprime `[ERROR]: Build failed` y **sale con codigo 0**. El wrapper de hoy mira
`if ($p.ExitCode -ne 0)` y luego `Test-Path $pbo`; las dos pasan, porque el PBO de la
construccion anterior sigue en su sitio. Resultado: `[ok] deployed` sobre un PBO de hace
media hora, y se prueba in-game un binario viejo.

## 8. Reglas de PowerShell 5.1 (las dos lanes)

No existen `&&`, `||`, el operador ternario, `??`, `?.` ni `ConvertFrom-Json -AsHashtable`.
`New-Item -Force` sobre un fichero lo TRUNCA. `Set-Content` sin `-Encoding` escribe en la
codificacion ANSI del sistema. Todo `.ps1` que escribas va en **ASCII puro**: sin acentos,
sin flechas, sin comillas tipograficas. Comentarios y nombres en ingles.
