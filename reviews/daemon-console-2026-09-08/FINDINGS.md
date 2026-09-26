# El daemon de produccion tiene consola privada y conhost propio (medido in-vivo)

2026-09-08. Cierra como PRESENTE, sobre el proceso vivo, lo que
`evidence/20260908-steam-console-lifecycle/console-redirector-v3143-findings.md`
dejo como INCONCLUSO por aislamiento: alli la ventana se reprodujo en una
simulacion y el cierre nunca se ejecuto.

## Lo medido

Daemon de produccion en `:8765`, lanzado por `spawn_detached` con
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`:

| Proceso | Rol | Consola |
|---|---|---|
| 28500 | wrapper del venv (padre) | **NINGUNA** — `AttachConsole` da win32 error 6 |
| 20568 | interprete real (hijo) | consola privada, miembro **unico** |
| 17968 | `conhost.exe 0x4` | **hijo directo de 20568** |

`GetConsoleWindow()` desde dentro de la consola de 20568 devuelve
`PseudoConsoleWindow`, titulo vacio, `IsWindowVisible=true` pero
**rect (0,0,0,0)**. `EnumWindows` no encuentra ninguna ventana de nivel
superior propiedad de conhost 17968.

Contraste en la misma maquina y el mismo instante: los daemons efimeros que
lanza la suite heredan la consola del runner — `console_hwnd = null`, wrapper e
interprete **ambos** dentro, sin conhost propio.

Reproducido dos veces con ~15 min de separacion, identico.

## Lo que esto SI sostiene

El interprete que tiene el puerto y el puente del juego esta **solo** en una
consola que su propio wrapper no comparte, con un host dedicado colgando de el.
Un cierre de esa consola entrega `CTRL_CLOSE_EVENT` a un unico proceso: el
daemon. Y el arbol de procesos del daemon lleva un `conhost.exe` que ningun
diseno pidio.

## Lo que esto NO sostiene

**No hay ventana presentada al escritorio.** El rect es 0x0 y conhost no publica
ventana de nivel superior. La lectura "el usuario cierra una ventana y mata el
daemon" NO esta acreditada; Microsoft advierte ademas que `GetConsoleWindow`
bajo pseudoconsola puede devolver un HWND auxiliar que no es el presentado.
Tampoco se ejecuto ningun cierre: la letalidad sigue siendo semantica
documental, no reproduccion.

## Ficheros

- `live_console_probe.py` / `live-console-probe.txt` — pertenencia de consola por pid.
- `conhost_windows.py` / `conhost-windows.txt` — `EnumWindows` sobre daemon y conhost.
- `conhost-parentage.txt` — la paternidad de conhost 17968.
- `mutants.py` — control de mutacion del test que fija el valor del flag.

## Control lado a lado: que cambia el flag, y que NO

`flag_control.py` lanza un hijo efimero por el MISMO redirector del venv, una vez
con cada combinacion, y mide consola, pertenencia y paternidad del conhost.

| | OLD `DETACHED_PROCESS` | NEW `CREATE_NO_WINDOW` |
|---|---|---|
| wrapper | **sin consola** (error 6) | dentro de la consola |
| interprete | consola propia, miembro unico | misma consola que el wrapper |
| `GetConsoleWindow` | handle vivo (`28448586`) | **`None`** |
| padre del conhost | **el interprete** | el wrapper |
| existe un conhost | si | **si, tambien** |

**Correccion de una lectura facil y falsa:** `CREATE_NO_WINDOW` **no** evita el
conhost. Significa "consola sin ventana", no "sin consola". Lo que cambia es que
ya no hay objeto ventana de consola que cerrar, que el daemon deja de ser el
unico miembro de su consola, y que el conhost sale del arbol de procesos del
propio daemon.

Fichero: `flag_control.py` / `flag-control.txt`.
