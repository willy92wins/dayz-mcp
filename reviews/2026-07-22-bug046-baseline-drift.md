# BUG-046 — drift previo al baseline estable

- Fecha: 2026-07-22.
- Producción modificada antes del gate: no.
- Digest agregado de fuentes durante todas las pasadas: sin cambios.
- Secuencia global observada: 70, 70, 67, 61, 61, 56, 56 resultados no verdes.
- Estado convergido aceptado: 654 tests descubiertos, 56 no verdes, digest normalizado `EC731958197E3879478F489DF2B9013C484B26C84BEBB4A2AD575A3AB3192323` en tres pasadas globales consecutivas.
- Focal convergida: 190 tests, 1 no verde, digest normalizado `7C4447822EA2DC948C79C03083370F73C51693127FB035AB8107C3895F944ED9` en dos pasadas consecutivas.
- Interpretación: estado externo/preexistente drenado por ejecuciones; no se atribuye a BUG-046. Desde este checkpoint no se acepta ningún ID, kind o fingerprint nuevo.

