# Servidor de datos de navegación de terrenos

Encargo explícito de Guillermo: añadir y validar esta opción en DayZ_MCP para
DayZ_Baltic_12km. Sesión única executor/integrator; revisión independiente Grok.
Base verificada: 28f76cd. Trazabilidad: product-spec E1/E5 (superficie tipada y
readiness interpretable), E4/F3 (coordinación y rechazo cerrado conservados).

## Contrato y límites

`dayz_test_run(..., mode="server", navmesh_data_server=True)` añade únicamente
`-startNavmeshDataServer` al argv del servidor. Booleano estricto, false por
defecto. Incompatible con otros modos, kill y pack_only; preflight valida sin
arrancar. El rechazo público precede a cola y takeover. No cambia el puente
Enforce, las políticas de rutas, credenciales, identidades ni el cierre por run_id.

Flujo: server.py -> dayz_test_tool.py -> dayz_test_request.py -> bundle sellado
-> dayz_test_worker.py -> broker/lifecycle existentes. Configuración de misión,
terreno y salida .nm pertenece al proyecto del mapa.

Solicitudes anteriores sin el campo siguen siendo entradas válidas y se
canonicalizan con el valor por defecto. Se conserva el requisito de identidad
exacta del request sellado: no se aceptan hashes de otro documento o bundle.
Actualizar conjuntamente las fuentes, bundle registrado y worker; un cliente
con esquema antiguo puede necesitar recarga del registro de tools.

## Criterios y evidencia offline

- PASS: booleano estricto, combinaciones inválidas rechazadas antes de efectos,
  ocho formas canónicas heredadas normalizadas, campos desconocidos rechazados.
- PASS: ejecución del worker conserva IDs/start/ack y hash del argv; la única
  diferencia de la opción true es el literal. Default y false son iguales.
- PASS: API pública transmite true/false y el adaptador sella true. No se
  atribuye conexión del generador al éxito del launcher.
- 146 pruebas focales PASS; 117 regresiones PASS con 2 skips declarados.
- Build aislado offline reproducible PASS; 64 pruebas de bundle/app/policy PASS.
- Pendiente: integración, registro por CAS, preflight y arranque reales,
  conexión del generador y cierre por run_id. La calidad de .nm y la carga
  jugable se verifican en el repositorio del mapa.

La guía del motor exige el flag y guardar explícitamente el archivo al finalizar:
https://community.bistudio.com/wiki?title=DayZ:Generating_navigation_mesh

## Integración y recuperación

Verificar main limpio y en la base prevista; integrar el commit propio.
Reconstruir con `tools/build_native_launcher.py --offline --verify-reproducible`.
La fuente actual contiene `launcher_registry_update.replace-dayz-test-v1`
(launcher_registry_update.py:298-395), transición atómica con CAS, backup y
recibo, que evita el registro temporalmente vacío del antiguo rollback/install.
Verificar ese contrato y sus tests antes de usarlo. Registrar el hash anterior
y posterior; recargar worker con la caja libre. No relajar verificaciones.
Ante fallo, conservar logs y no repetir un lanzamiento sin un discriminador.
