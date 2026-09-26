# Gates: lote C — schema UI del ingress (M06-UI-INGRESS)

Scope: `tools/dayz_mcp/loopback.py` acepta en los cuatro verbos UI el contrato aprobado
(`root` opcional en los cuatro; `mode` y `bubble` solo en `ui_click`) sin abrir la puerta a
ningun campo que el contrato no nombra, y sus dos ficheros de test lo fijan.

Escrito por la sesion orquestadora ANTES de delegar. El worker NO edita este fichero ni
`gate/oracle_ui_schema.py`: los dos se rehashean al recibir y un cambio produce INCONCLUSIVE.

```gates
[ ] G1: el ingress acepta el contrato aprobado y sigue rechazando todo lo demas
  CHECK: "C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe" gate/oracle_ui_schema.py
  EXPECT: UI_INGRESS_SCHEMA_OK
  EVIDENCE: pending

[ ] G2: los tests propios del modulo pasan enteros
  CHECK: "C:/Users/guill/OneDrive/Documentos/DayZ Projects/DayZ_MCP_dev/tools/.venv-mcp/Scripts/python.exe" -m unittest tests.test_loopback tests.test_validate_command_args_table
  EXPECT: OK
  CWD: tools
  EVIDENCE: pending

[ ] G3: la suite completa no pierde nada por el cambio
  EVIDENCE: pending

ABANDON: G3 imposible EN ESTE WORKSPACE por decision del orquestador, no por el cambio.
La copia aislada solo trae tools/ y tres .c, y buena parte de la suite son gates documentales que
leen el arbol entero: medido hoy, 2077 tests con 12 failures y 39 errors, frente a 2293 con 2
failures en el arbol vivo — ~216 tests ni se recogen. Handoff: la corre el ORQUESTADOR al integrar,
sobre el arbol real, comparando contra el baseline vivo. El delegado no la ejecuta y no se le
cuenta en contra.

[ ] G4: calibracion — un schema que acepta cualquier cosa pone G1 rojo
  EVIDENCE: pending
```

G4 es manual y la corre el orquestador al recibir, fuera del workspace: si `_matches_schema_variant`
devuelve `True` sin mirar, G1 tiene que enrojecer por los quince casos de RECHAZO. Un oraculo que
solo comprueba las aceptaciones se satisface ensanchando el schema, que es justo el defecto.

G3 (ABANDONADA arriba) heredaria del arbol dos rojos ajenos (centinela de M03, ficha `fb-20260902-194845-bd90`), rojos por
decision del usuario. Su EXPECT se juzga contra el baseline medido antes de delegar, no contra `OK`
absoluto; el baseline vive en `runs/BASELINE.txt`.
