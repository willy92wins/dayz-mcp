# Base del arbol SIN TOCAR (medida antes de arrancar la lane)

`Ran 1610 tests` con 2 fallos + 2 errores. **Ninguno es un defecto**: los cuatro
eran artefactos de como se monto la copia, y estan cerrados.

| Rojo | Causa | Estado |
|---|---|---|
| `tests.test_session_e2e` (ImportError) | la copia excluia `tools\_broker`, que el modulo importa | copiado; `Ran 14 tests OK` |
| `test_readme_tool_count_matches_instantiated_app` | faltaba `DayZ_MCP_dev\README.md` en la copia | copiado; verde en la misma tanda |
| `test_real_run_daemon_crash_boundaries_recover_in_second_wave` | flake de CARGA | `Ran 7 tests OK` en solitario |
| `test_run_daemon_candidate_wave_and_cross_port_publish_one_generation` | flake de CARGA | idem |

**Los dos flakes, con su mecanismo**: el demonio arranca con
`idle_timeout=0s` y `poll=5s`, asi que si el cliente del test no conecta dentro
de la primera ventana de 5 s, el watchdog suelta el puerto y sale. La propia
traza lo dice: `IDLE-WATCHDOG: idle 5s >= 0s; releasing port and exiting`. Con
la maquina ocupada (habia otra sesion trabajando) esa ventana no se cumple.

**Conclusion para la lane**: la base esta VERDE. Cualquier rojo nuevo es tuyo,
con una excepcion: si ves rojo `DaemonStartupElectionProcessTest`, vuelve a
correr ESE modulo solo antes de tocar nada — es carga, no regresion.
