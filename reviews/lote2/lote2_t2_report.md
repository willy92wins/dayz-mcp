# Lote 2 — T2 Steam

## Cambio
- `tools/dayz_mcp/steam_preflight.py:60`: motivo seguro `steam_remediation_reason`, solo en resultado de remediacion; contrato de evaluacion sin cambios.
- `steam_preflight.py:307-340`: espera con reloj monotono; cada sleep se limita al presupuesto restante. Presupuestos existentes: shutdown 15 s y ActiveProcess 20 s. No espera real en tests.
- `steam_preflight.py:342-353`: todo motivo de fracaso fuerza `steam_session_stale`, aunque otra lectura vea una sesion sana.
- `steam_preflight.py:373-416`: abortos con motivos `steam_executable_unavailable`, `shutdown_failed`, `shutdown_timeout`, `process_list_unreadable`, `relaunch_failed`, `active_process_timeout`. Se conserva el ultimo veredicto del sondeo; no hay lectura adicional despues del timeout que lo pueda promover a exito.
- El sondeo usa `evaluate_steam_session`: ActiveUser entero no booleano distinto de cero, dos snapshots iguales, PID positivo existente e imagen basename steam.exe. La enumeracion sigue siendo diagnostica, no se cambia su contrato.
- `tools/dayz_mcp/dayz_test_tool.py:1412-1427`: fracaso publica `steam_remediated: false` y `steam_remediation_reason`; mantiene los campos existentes.

## ROJO observado antes del arreglo
`tools/tests/lote2_t2_red.txt`: 5 tests, 4 fallos.
1. Motivo no propagado: None != active_process_timeout.
2. Timeout seguido de snapshot sano: devolvia error_code None (falso exito).
3. Shutdown que nunca termina con sesion sana: devolvia error_code None (falso exito).
4. Timeout carecia de motivo.
El quinto test ya pasaba: el codigo existente SI sondeaba la condicion compuesta. No se atribuye ese sondeo al nuevo arreglo.

## VERDE
- `tools/tests/lote2_t2_green.txt`: los mismos 5 tests pasan. Matriz interna cubre usuario cero, PID muerto, imagen ajena y PID registrado distinto hasta convergencia.
- `tools/tests/lote2_t2_existing.txt`: 17 tests existentes de steam_preflight pasan.
- Tests nuevos en `tools/tests/test_lote2_t2_steam.py`; proveedor, reloj, invocaciones y launcher inyectados. El test del envelope no permite ejecutar el launcher.

## Comandos ejecutados
Desde cwd del encargo:
```sh
PYTHONPATH=tools:tools/tests /mnt/c/Python314/python.exe -m unittest discover -s tools/tests -p test_lote2_t2_steam.py -v > tools/tests/lote2_t2_red.txt 2>&1
# Despues del arreglo:
PYTHONPATH=tools:tools/tests /mnt/c/Python314/python.exe -m unittest discover -s tools/tests -p test_lote2_t2_steam.py -v > tools/tests/lote2_t2_green.txt 2>&1
PYTHONPATH=tools:tools/tests /mnt/c/Python314/python.exe -m unittest discover -s tools/tests -p test_steam_preflight.py -v > tools/tests/lote2_t2_existing.txt 2>&1
```

Todas las escrituras se releyeron y comprobaron sin NUL. Sin git, red, servicios, bundle real ni modificaciones de server.py. Suite completa reservada al coordinador. El presupuesto acota el sondeo y sus sleeps; no promete interrumpir una llamada de proveedor/WinAPI bloqueada.
