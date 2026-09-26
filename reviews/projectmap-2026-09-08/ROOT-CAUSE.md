# PROJECT-MAP.md: quién lo pisaba, y por qué regenerarlo no bastaba

Cierre de la ficha `fb-20260908-134346-7229`. Medido el 2026-09-08 por la sesión
que abrió la ficha.

## No era una sesión: eran dos generadores que se contradicen

Hay **dos** `gen-project-map.ps1` en el árbol, y escriben formas incompatibles del
mismo apartado.

| generador | qué hace | qué escribe del HANDOFF |
|---|---|---|
| `DayZ Projects\tools\gen-project-map.ps1` (2026-09-01) | barre **todos** los `<Mod>_dev` con un `HANDOFF.md` (`Get-DevDirs`) y escribe un mapa en cada uno | `:108` `` `HANDOFF.md` tiene **N líneas**; el bloque vivo termina en la **línea E** `` y `:110` `` **Leerlo así:** `Read(HANDOFF.md, limit: E)` `` |
| `DayZ_MCP_dev\tools\gen-project-map.ps1` (commit `cef8567`) | solo este proyecto | `:128` «leer el bloque entre `<!-- LIVE-STATE:START -->` y `<!-- LIVE-STATE:END -->`» |

El gate de este proyecto —`tools/tests/test_docs_truth.py`,
`ProjectMapHandoffCountsDocsTest`— **prohíbe las tres formas con número** por regex y
exige la cadena `LIVE-STATE:END`. Su docstring dice el motivo: el bloque vivo se
reescribe en cada cierre de sesión, así que un número de línea, un `limit` o un tamaño
en KB están caducados por construcción.

Consecuencia: **cada pasada del barredor global ponía el gate en rojo.** Tres veces en
un día, que es justo la frecuencia con la que la ficha lo observó. Y el arreglo que el
`HANDOFF.md` recomendaba —correr el generador local— solo aguantaba hasta la siguiente
pasada.

Cómo se distingue a simple vista quién escribió el mapa: el barredor global pone
cabecera en **inglés** («This answers WHERE things are»); el local, en **castellano**.

## Arreglo: el generador local manda

En `Get-DevDirs` del barredor global, saltar cualquier `<Mod>_dev` que traiga su propio
`tools\gen-project-map.ps1`, anunciándolo. Regla general, no un nombre a fuego: un
proyecto que se trae su generador tiene una opinión sobre su mapa, y el barredor no es
quién para pisarla. Hoy afecta a un solo proyecto — es el único con generador propio.

El fichero **no está bajo git** (`DayZ Projects\` no es un repositorio). Rollback por
copia: `tools\gen-project-map.ps1.bak-20260908-preskip` (9772 B, el original intacto).

## Verificación

- Generador local ejecutado: `PROJECT-MAP.md` 4652 B,
  sha256 `E21106093332318229BDB8E62787344B58CF6FEF5F27C4BE2B85A8B34AF57D3B`.
- `tests.test_docs_truth`: **Ran 20 tests, OK (skipped=2)**. Antes: 2 fallos
  (`test_entry_points_exist`, `test_handoff_claims_are_current`).
- **Control positivo** — el barredor global ya no puede romperlo:
  `gen-project-map.ps1 -Project DayZ_MCP` imprime
  `skip DayZ_MCP_dev: ships its own tools\gen-project-map.ps1`, cierra con
  `Total: 0 maps` y el sha256 del mapa queda **idéntico**.
- **Control negativo** — sigue sirviendo a los demás: `-Project LFHeli` genera
  `Total: 1 maps` y actualiza su contenido
  (`399D1E00…` → `39BA1B0B…`).

## Lo que NO se ha hecho, a propósito

- No se ha tocado la forma que el barredor escribe para los otros 19 mods. El número de
  línea les da un `Read(..., limit: N)` mecánico; imponerles la política de este
  proyecto no era esta decisión. La alternativa —enseñar al barredor la forma con
  marcador para todos— sigue disponible y es mejor a largo plazo, porque el número
  caduca en el cierre siguiente de cualquier mod, no solo de este.
- No se ha metido el barredor bajo control de versiones. Es una herramienta compartida
  por ~20 proyectos que hoy vive sin historial ni rollback más allá de copias `.bak`.
