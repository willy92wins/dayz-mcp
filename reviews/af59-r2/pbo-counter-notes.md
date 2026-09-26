# PBO script-entry counter - ronda 3

`Get-PboScriptEntryCount -Path <pbo>` devuelve Int64 solo cuando completa la tabla.
Cuenta nombres terminados en `.c` sin distinguir mayusculas. Nunca busca texto en
el payload. `Invoke-Build` compara con todos los `.c` recursivos de `$src`, aunque
no exista `scripts/`; packonly y binarizacion atraviesan el mismo control.

Politica de errores, escrita tambien en el codigo:
- `IO.InvalidDataException`: truncamiento de strings/metadata despues de reconocer
  el layout o sumas de DataSize que exceden los bytes disponibles => `Die`.
- `NotSupportedException`: primer registro no reconocible, metadatos Vers no
  soportados, entrada sin nombre atipica o Vers con nombre => `Warn INCONCLUSIVE`.
  La incapacidad del lector NO prueba que un PBO este roto. No devuelve un cero
  inventado ni imprime `[ok] script check` para este caso.
- Otro fallo del lector (incluido I/O) => `Warn INCONCLUSIVE (reader failure)`.
  Permite continuar la secuencia del build sin ascender el chequeo a verificado.
  Los gates independientes de af59, exit, texto, existencia, mtime/hash siguen
  aplicandose ANTES. No se ha cambiado su comportamiento ni el preflight H1.

No es un validador general de PBO. Reconocer una tabla no demuestra que el juego
pueda cargar su contenido. Los metodos de compresion no se decodifican: se usan los
DataSize almacenados. Las propiedades desconocidas se consumen como parejas de
strings. Los trailers se toleran, pero este contador NO verifica checksums.
Los recuentos no demuestran identidad: sustituir un script por otro con el mismo
numero de entradas puede pasar. Corregir eso excede estos cuatro riesgos.

Fuente de formato consultada: [Bohemia PBO File Format](https://community.bistudio.com/wiki/PBO).
Se contrastaron el layout de cabecera y los tamanos almacenados con la fixture real.
No se afirma cobertura de todas las variantes de PBO.

Pruebas: `test-af59.ps1`, S1-S9 originales y S10-S16 de ronda 3; H1 se conserva.
S10 usa metadatos deliberadamente no soportados para medir el contrato del lector,
NO pretende ser un PBO legitimo producido por una herramienta. S11 recorta un byte
del payload de una tabla sintetica conocida y mide el rechazo complementario.
S14 usa los 205008 bytes del PBO real de AddonBuilder -packonly conservado en
`fixtures/DayZ_MCP_fence_DCC8730F.pbo`, fijado por SHA-256; inventario y procedencia en
`fixtures/README.md` y `fixtures/real-pbo-inventory.json`. El control exige 9/9.
No se ha ejecutado AddonBuilder en esta lane (prohibido expresamente por el brief).

Correccion de las notas anteriores: el arnes vivo ya escribia PBO sintetico
estructurado en nueve escenarios; lo de siete escenarios con bytes arbitrarios
era informacion obsoleta. El archivo `pbo-counter.txt` y CONTRACT.md son material
historico sin actualizar, FUERA DE MI ALCANCE: rige el brief R3 y el codigo vivo.

Repeticion completa, serial y offline (desde raiz del repo):
`tools/.venv-mcp/Scripts/python.exe reviews/af59-r2/run-r3.py --mutations`
El driver valida exit codes, identidad de todos los casos y exactamente un FAIL
por mutacion. Deja gate-r3.log, H1-r3.log, mutants-r3.log y M3-r3.log..M9-r3.log.
Las mutaciones no cambian ni el sujeto final ni el arnes.
