## ITEM 9941-probe

**Decision**

[DESIGN] Un único lote: probe desechable y opt-in para H1, con `ActionPlaceObject`, un `FenceKit` vanilla y tres ensayos: control, supresión automática y cancelación temprana. No establece todavía un mecanismo de hold de producción.

**Decisión del orquestador (vinculante, resuelve el pendiente):** el bump `11→12` es la ÚNICA diferencia permitida sin el flag: `bridge_status`/`status` publican `"12"` y el gate de versión sigue igual de estricto. Todo lo demás (superficie pública, contrato de cable visto por un cliente default, resultados existentes) queda idéntico. No se oculta la versión ni se debilita su gate.

Precisión verificada: `DEFAULT_PLACE=0`. No alterar esa duración para producir artificialmente un hold; medir la ejecución natural del componente y del callback.

**Verified anchors**

Rutas relativas al candidato. `V29/` representa `C:/Users/guill/OneDrive/Documentos/DayZ Projects/scripts/`; `U/` representa `V29/4_world/classes/useractionscomponent/`. Todos los siguientes fragmentos fueron abiertos:

- `tools/dayz_mcp/server_cli.py:57`: `"--enable-exec-enforce", action="store_true"`; `server.py:600,6519-6524`: `enable_exec_enforce: bool = False`, registro bajo `if config.enable_exec_enforce`.
- `tools/dayz_mcp/loopback.py:3283-3287`: whitelist ampliada condicionalmente; `:2752-2753`: rechazo `{"error": "not_whitelisted"}`.
- `tools/dayz_mcp/server.py:7102-7111,7262-7270`: argumentos de `action_use`, comprobación de soporte y destino `"client"`. `bridge_readiness.py:455-469`: exige capabilities `"announced"` y pertenencia del comando.
- `addon/scripts/5_Mission/MCPClientBridge.c:3645,3741-3742,3920-3965`: `DispatchActionUse`, target de manos, comprobaciones existentes, `amc.PerformActionStart(...)` y posterior `GetRunningAction() == null` → `"setup_failed"`.
- `addon/scripts/5_Mission/MCPClientBridge.c:596-624,6852-6867`: `OnTick(float timeslice)` y `Shutdown()` con guard de reentrada. `MissionGameplay.c:3-6`: destructor → `ShutdownInstance()`.
- `addon/scripts/5_Mission/MCPBridge.c:117-129,467-479`: tick servidor y dispatcher; `MCPMessages.c:1`: `MCP_BRIDGE_VERSION = "11"`.
- `tools/dayz_mcp/core.py:41,64-71,107-113`: versión esperada `"11"`, comparación exacta y `"version": version`. `MCPClientBridge.c:6318-6320`: poll incluye bridge y versión del juego.
- `tools/dayz_mcp/loopback.py:3921-3985`: `cleanup_owner(...)`, cancelación pendiente y entrega interna de cleanup. `session_coordination.py:95-96`: mutación requiere lease salvo comandos de lectura.
- `U/actionmanagerclient.c:18,1273-1277`: `protected bool m_IgnoreAutoInputEnd`; setter público `SetIgnoreAutomaticInputEnd(bool state)`.
- `U/actionmanagerclient.c:282-301`: `ai.Update()` y `WasEnded()` terminan input continuo salvo `!m_IgnoreAutoInputEnd`. `:274-278`: `EndActionInput()` marca solicitud; `:329-387`: envío normal de end-request/input-end.
- `U/actionmanagerclient.c:598-660,754-773`: `SetupAction`, serialización normal del start y `PerformActionStart` fuera de `#ifdef BOT`.
- `U/actionmanagerbase.c:61,91-96,311-323`: `m_CurrentActionData` protegido, lectura de acción y cleanup normal.
- `U/actioninput.c:104-135,549-551,604-610,645-647`: estado activo, lectura `LocalHold`, input continuo y overrides de `WasEnded()` que devuelven `false`.
- `U/actions/actioncontinuousbase.c:6-31,79-114,135-142,179-181`: callback, cancelación del componente e input `ContinuousDefaultActionInput`.
- `U/animatedactionbase.c:27-50,70-75,382-439,445-475`: finish normal, `Execute`, `CanContinue` y limpieza del manager. `U/actionbase.c:919-924`: continuación comprueba stance/item/target.
- `U/actioncomponents/cacontinuousbase.c:8-12`: `OnCompletePogress` → `OnFinishProgress`; `cacontinuoustime.c:29-84`: acumulación con `GetDeltaT()`, cancelación y progreso. `cacontinuousquantity.c:31-67,79-92`: consumo normal, incluido al cancelar.
- `U/actions/continuous/actionplaceobject.c:3-20`: `CAContinuousTime(UATimeSpent.DEFAULT_PLACE)` y `HasProgress=false`; `U/actions/actionconstants.c:37`: `DEFAULT_PLACE = 0`.
- `U/actions/continuous/deployactions/actiondeployobject.c:35-110,205-233,255-296`: condiciones/holograma, setup, eliminación del kit y serialización de posición/orientación.
- `U/actions/continuous/deployactions/actiondeploybase.c:70-128`: collision gate, `PlaceEntity`, `m_AlreadyPlaced=true` y `OnPlacementComplete`.
- `V29/4_world/entities/itembase/fencekit.c:19-32`: servidor crea `"Fence"`. `V29/3_game/entities/object.c:813-815`: ID de red compartido, dos enteros.
- `U/actionmanagerserver.c:36-91,287-297`: recepción normal de start/end-request/input-end.
- `U/actions/singleuse/actiontoggleplaceobject.c:53-58`: `Start` → `TogglePlacingLocal()`.

**Required behavior**

Todo lo nuevo siguiente es [DESIGN].

1. **Flag:** `--enable-action-hold-probe`, default `false`. Propagar por configuración, daemon y runtime. Un cliente no puede activar un daemon ya arrancado sin él, ni sustituirlo automáticamente. Registro público y admisión HTTP independientes del flag de `exec_enforce`.

2. **Tool:** `action_hold_probe(mode="run", ignore_auto_input_end=false, observation_s=15.0, cancel_early=false)`.
   - `mode`: únicamente `run|inspect`.
   - Booleanos estrictos; números finitos, sin aceptar booleanos como números.
   - `observation_s`: `[2.0,30.0]` segundos.
   - `inspect` exige los demás valores default; lee el flag actual, no inicia acciones.
   - `run` fija `ActionPlaceObject`, target de manos y kit exacto `FenceKit`; no admite acciones arbitrarias.
   - Deadline total: `observation_s+10` segundos. Sin reintento automático del start.

3. **Wire:** comando cliente `action_hold_probe`, operaciones internas `describe|prepare|run|renew|cancel|inspect`. Comando servidor auxiliar `action_hold_probe_watch`, operaciones `describe|arm|collect|disarm`. Schemas cerrados; claves adicionales rechazadas. Cada operación lleva ID de ensayo generado por Python; las mutaciones quedan vinculadas a sesión, lease, generación e instancia.
   
   `prepare` devuelve identidad corporal, kit, holograma/proyección y pose. `arm` registra esos IDs en servidor. Solo después de confirmarlo se permite `run`. `collect` devuelve evidencia servidor y desarma. Cleanup usa exclusivamente el ensayo registrado; no acepta un ID ajeno.

4. **Compatibilidad:** no añadir campos al DTO general de resultados, modificar argumentos/resultados de `action_use` ni anunciar los comandos experimentales en capabilities existentes. Usar DTOs propios y handshake `describe` exclusivo del probe. Mantener whitelist, schemas y registro experimentales fuera del perfil default. Aplicar un solo bump `11→12` coordinado Python/PBO, sujeto a la aclaración indicada.

5. **Fail-closed:** sin flag, tool ausente —error MCP habitual de herramienta desconocida— y HTTP devuelve `400/not_whitelisted`. Cliente configurado para probe frente a daemon desactivado: `probe_disabled`. PBO antiguo, peer desconocido/stale, versión incompatible o `describe` ausente/inválido: `probe_not_supported`, sin setter ni start. Revalidar binding antes de entregar.

6. **Ejecución:** exigir gameplay camera, simulación normal, kit intacto, holograma válido/no colliding y ausencia de acción actual/pendiente. Reutilizar íntegramente la ruta comprobada de `action_use(target="hands")`, incluidas sus condiciones y serialización. Guardar el valor **real** anterior mediante accessor nuevo en `modded ActionManagerClient`; aplicar el setter justo antes del único start.

7. **Log:** JSONL separado cliente/servidor, una muestra por tick durante la ventana y eventos adicionales en cada transición. Campos:
   `trial_id, side, seq, tick, elapsed_s, timeslice_s, player/body_id, kit_id, projection_id, projection_pose, action_type/id, action_data_identity, manager_state, ignore_previous/current, input_type/active/was_ended, callback_present/state/interrupted/canceled, component_type/progress/elapsed/duration/execute_count, continuation_result, end_input_requested/sent/received, end_request, server_finish, already_placed, deployed_entity_id/type/pose, kit_exists`.
   
   Valores no disponibles: `null` con motivo; jamás ceros inventados. Instrumentar llamadas naturales, llamar `super` exactamente una vez y no reevaluar condiciones para fabricar motivos. No interpretar progreso `1` como completion: con duración cero puede aparecer sin ejecución. Máximo 16 384 registros por lado; overflow cancela/restaura y queda INCONCLUSIVE.

8. **Restauración:** identificar la instancia concreta de `ActionData`, cuerpo y manager; compartir únicamente el flag mientras esa instancia sea la designada. Restaurar sin esperar al siguiente tick cuando termine, falle setup o se cancele. Restaurar también por timeout, error, cambio/pérdida de cuerpo, desconexión y shutdown. Guard idempotente, incluido valor previo `true`; impedir influencia sobre la siguiente acción.
   
   `cancel_early=true` solicita `EndActionInput()` inmediatamente tras el start retenido, antes del primer `Execute`. Cleanup excepcional usa interrupción normal del manager, nunca finish/deploy directo.
   
   Cleanup del dueño no espera al lock ocupado por `run`. Renovación interna cada segundo; watchdog cliente vence a los dos segundos sin renovación, independientemente de Python. Registrar restauración al recuperar un tick si el juego dejó de ejecutar; no afirmar confirmación durante una suspensión o muerte del proceso.

9. **Resultado:** `{ok,error,trial_id,started,terminal_reason,previous_ignore,restored_ignore,restore_confirmed,client,server,log_paths}`. `client/server` contienen identidad, contadores, eventos terminales y efecto observado. Evidencia servidor ausente = `null` con motivo. `ok` indica ejecución del probe, **no PASS de H1**. Mantener observación hasta agotar la ventana aunque la acción termine antes.

**Offline tests**

Cada test exige un positivo y su negativo discriminante:

- Registro/configuración/HTTP: habilitado funciona; deshabilitado rechaza incluso enqueue directo. Sin implementación falta el tool positivo.
- Compatibilidad: comparar bytes de schemas, capabilities y resultados default con fixtures independientes; campos nuevos o cambios de `action_use` deben fallar. La excepción de versión permanece pendiente.
- PBO viejo, handshake malformado y binding stale: cero starts; peer compatible admite uno. Sin gate, falla el negativo.
- Bounds/tipos/claves extra: rechazo antes de enqueue; límites válidos aceptados.
- Start único: spy del consumidor cuenta exactamente uno; setup fallido conserva log de holograma y restaura.
- Restauración parametrizada por todas las salidas y valores previos `false/true`; cleanup repetido y ensayo ajeno no modifican otro manager.
- Desconexión con `run` pendiente, cancelación prioritaria y pérdida de renovaciones: fake-clock prueba cleanup/watchdog. Sin cableado falla la restauración.
- Evidencia: finish local sin creación/consumo servidor nunca satisface PASS; duración cero, logs truncados y IDs mezclados quedan INCONCLUSIVE.

Python/source tests no prueban compilación ni comportamiento Enforce.

**In-game acceptance**

Verificar hash/procedencia del PBO y bridge `12`; adquirir lease. Preparar tres kits equivalentes en la misma pose y suelo despejado, con cámara gameplay, tiempo normal y sin input humano. Antes de cada ensayo:

```text
action_use(action="ActionTogglePlaceObject", target="hands")
```

Confirmar holograma válido y ventana ≥ duración observada de animación/acción + dos segundos. Ejecutar:

```text
action_hold_probe(ignore_auto_input_end=false, observation_s=15.0)
action_hold_probe(ignore_auto_input_end=true, observation_s=15.0)
action_hold_probe(ignore_auto_input_end=true, observation_s=15.0, cancel_early=true)
```

Entre ensayos, retirar el efecto anterior mediante herramientas normales y preparar kit/proyección nuevos equivalentes. Si 15 segundos no cubren el presupuesto, repetir **ambos** matched trials con idéntica ventana mayor, máximo 30.

Después: `action_hold_probe(mode="inspect")`, acción ordinaria `ActionPlaceObject` mediante `action_use(target="hands")` y segundo `inspect`; comparar flag y efecto con control.

**PASS:** true alcanza ejecución natural, creación autoritativa de `Fence` y consumo del kit; control no completa; cancelación precede al primer avance y no coloca; restauración y acción ordinaria verificadas.

**FAIL:** ambos cancelan en el mismo punto input-dependiente identificado.

**INCONCLUSIVE:** setup/holograma inválido, reloj congelado, cancelación tardía, evidencia incompleta o ensayos desparejados. Ambos completan: hold ausente no quedó aislado; no declarar PASS.

Liberar lease y ejecutar `session_status` antes del handoff.

**Out of scope**

H2/H3, `hold_s` de producción, UAInput writes, alterar duración/condiciones/serialización, invocar finish/deploy directamente y conclusiones sobre persistencia de SecretRock —[VERIFY], su implementación no fue abierta—. Sin modificaciones ni ejecución in-game en esta revisión.