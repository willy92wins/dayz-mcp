# Herramientas del paquete de fencing

Autocontenido a proposito: los originales vivian en un scratchpad de sesion y en `%TEMP%`,
y los dos desaparecen.

- `..\enforce-fencing\*.c` — las TRES fuentes Enforce del fencing (las unicas piezas del
  cambio que no estan en el arbol ni se pueden reconstruir desde `delta.diff` sin aplicarlo).
- `pbo_still_valid.py` — dice si el PBO del paquete sigue valido contra el arbol de AHORA.
  Coge automaticamente el `DayZ_MCP_fence_*.pbo` mas reciente de la carpeta padre.
  **Correr SIEMPRE antes de desplegar**: la sesion de UI trabaja en el mismo addon y cada
  despliegue suyo caduca este PBO.
- `build_fence_pbo.ps1` — reconstruye mezclando el addon del ARBOL con las tres `.c` de
  `enforce-fencing\`. Empaqueta a un `%TEMP%` con marca de tiempo; **no despliega**.
- `verify_pbo.py <ruta.pbo> <dir staging>` — comprueba que cada entrada del PBO es
  byte-identica a su fuente y que `&inst=` aparece 4 veces.

Orden: `pbo_still_valid` -> si caduco, `build_fence_pbo` -> `verify_pbo` -> desplegar.
