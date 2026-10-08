## ITEM broker

**Implementar primero una infraestructura Python común para identidad completa y abandono dirigido.** Los lotes 86a3 y 9941 consumirán este contrato en su ronda final. No modificar Enforce, PBO, launcher ni módulos sellados.

**Base y alcance.** Árbol leído: `C:\Users\guill\dzmcp_gauntlet\base_v11f`, exportación atribuida por el encargo a `46833d7`, sin `.git` verificable. No se ha comprobado el despliegue v11b. Las propuestas siguientes son **[DESIGN]**; los fragmentos existentes son **[EXACT]**. Esta especificación es de solo lectura.

### 1. Anclajes verificados

**[EXACT]** La identidad completa ya incluye instancia, epoch, PID y creación. `tools/dayz_mcp/loopback.py:2059`:

```python
return (
    f"{binding.instance}|{binding.epoch}|{binding.pid}|"
    f"{binding.creation_time_utc}"
)
```

**[EXACT]** El HTTP pierde incluso la información disponible al admitir. `tools/dayz_mcp/loopback.py:4528`:

```python
response = {"id": payload["id"]}
if "cleanup_degraded" in payload:
    response["cleanup_degraded"] = payload["cleanup_degraded"]
self._json(200, self._with_renewed_lease(response))
```

**[EXACT]** El abandono broker es vacío. `tools/dayz_mcp/server.py:2210`:

```python
async def abandon_bridge(self, command_id: int, reason: str) -> None:
    # Client mode has no daemon /abandon route. An undelivered command
    # expires via COMMAND_TTL_S; a delivered one is reaped by its
    # operation deadline. This method is a documented no-op.
    return
```

**[EXACT]** El transporte acreditado está compuesto en `tools/dayz_mcp/server.py:1139`:

```python
daemon_credential.RefreshingDaemonCredential(
    policy=daemon_policy,
    request_fn=lambda **kwargs: (
        orphan_guard.verified_daemon_http_request(
            time_fn=self._time_fn,
            **kwargs,
        )
    ),
)
```

**[EXACT]** 86a3 obtiene tokens completos solo con estado local; broker conserva prefijos. `C:\Users\guill\dzmcp_gauntlet\r2c_86a3\ws\tools\dayz_mcp\action_cursor.py:99`:

```python
token = getattr(state, "bound_instance_token", None) if state is not None else None
if not callable(token) or not isinstance(run_id, str):
    return fence
```

**[EXACT]** 9941 espera directamente por la ruta sin abandono. `C:\Users\guill\dzmcp_gauntlet\r2b_9941\ws\tools\dayz_mcp\server.py:2275`:

```python
result = await self._await_result(
    "action_hold", command_id, "client", timeout_s
)
```

### 2. Identidad y fences

**[DESIGN]** Exponer identidad en **ambas superficies**:

- `/status`: permite capturar juntos los peers que participan en una operación.
- Sobres `/enqueue` y `/await`: atribuyen el comando concreto, independientemente del binding actual.

Añadir a `/status` un bloque `broker_infra` con versión `1` y comandos con política de liberación registrada. Cada peer expone `run_id` y `binding_token` completo, conservados también por `core.build_status`. El token es opaco; el consumidor compara igualdad exacta. No es una credencial.

Construir la captura de generación y peers bajo **un mismo lock**. Solo una selección única, acreditada y BOUND produce token; ausencia, ambigüedad o identidad ilegible producen `null`. Un binding `offline` puede cubrir ambos peers y compartir token.

Añadir un argumento opcional `expected_fence` a la admisión y a los wrappers runtime. Contiene generación y pares peer→`{run_id, binding_token}`. Comparar **todos los peers indicados bajo el lock de publicación**, además de las comprobaciones actuales de lease, capacidades y destino. Mismatch devuelve `409 binding_changed`, sin publicar comando. Una fence mal formada devuelve `400 bad_binding_fence`.

El recibo de admisión contiene generación, ID, peer, run, token del destino y fence aceptada. `/await`, pendiente o terminado, conserva ese recibo; terminado añade `executed_by`, obtenido del poll/result acreditado contra la identidad conservada. Nunca reconstruirlo desde el binding actual ni confiar en campos enviados por Enforce.

`executed_by` identifica quién recibió y respondió al comando; no prueba por sí solo que la acción produjo su efecto.

`Runtime` y `ClientRuntime` preservan estos metadatos bajo una clave reservada `_broker`, añadida después del pruning. Los resultados de negocio permanecen iguales. Los wrappers de admisión separada conservan un recibo por ID, sin un atributo global «último comando» compartido entre tareas.

**Compatibilidad:** antes de admitir una operación que exige fence o liberación, comprobar versión, tokens necesarios y política registrada. Daemon antiguo o respuesta incompleta: `broker_infrastructure_missing`. Nunca sustituir por `instance_prefix`. Si la incompatibilidad aparece después de admitir y existe recibo válido, abandonar ese ID y devolver error.

### 3. Registro de liberación y estado retenido

**[DESIGN]** Registro interno del daemon, instalado al componer `ServerState`; ningún cliente HTTP puede registrar políticas ni proporcionar comandos de cleanup arbitrarios.

Contrato propuesto:

```python
# [DESIGN]: firmas nuevas, no implementación existente.
register_release(
    command: str,
    *,
    build_release: Callable[[AdmittedCommand], ReleaseSpec],
    is_terminal: Callable[[AdmittedCommand, dict, str], bool],
) -> None
```

`AdmittedCommand` es una vista inmutable: generación, ID, sesión, lease original cuando corresponda, comando, argumentos efectivos —incluidos identificadores acuñados— y destino completo. `ReleaseSpec` contiene comando y argumentos; **el destino lo impone la infraestructura**. `is_terminal` recibe exclusivamente resultados acreditados del original o de su liberación registrada; el tercer argumento distingue ambos.

Validar el registro al arrancar: duplicados, comando de liberación desconocido y ciclos son errores. Validar el `ReleaseSpec` antes de publicar el original. Las funciones son deterministas, sin I/O ni callbacks que muten el broker.

Conservar un registro por admisión con:

- Identidad del propietario y prueba verificable de la lease original.
- Destino completo, incluyendo creación; no solo el triple actual de `_command_fence`.
- Estado queued/delivered, abandono, resultado consumido y cierre de recurso, separados.
- Política, liberación derivada y único ID de liberación, si se publica.

Marcar delivered al retirar el original para un poll acreditado. Consumir `/await?remove=1` no destruye la autoridad necesaria para abandonar o reconciliar. La base elimina propiedad al consumir (`loopback.py:3964`) y fence al abandonar (`loopback.py:3346`); esas eliminaciones no deben borrar el nuevo registro.

Retención **[DESIGN]**: terminales durante 300 segundos; pendientes y recursos sin reconciliar permanecen hasta resolución o fin de generación. Acotar capacidad y rechazar nuevas admisiones antes de agotar registros o capacidad reservada de cleanup. Nunca eliminar incertidumbre para permitir otra operación.

### 4. Abandono autorizado por ID

**[DESIGN]** Un único endpoint nuevo: `POST /abandon`. Cuerpo: identidad de sesión, lease original cuando corresponda, generación, ID y motivo acotado (`cancelled`, `tool_timeout`). No admite peer, destino ni release elegidos por el solicitante.

`ClientRuntime.abandon_bridge(id, reason)` obtiene esos datos del recibo conservado y usa `_call` y el transporte acreditado existente. No introducir `urllib` directo ni un bypass de acreditación.

Autorizar contra **el propietario registrado de ese comando**. Para comandos con lease, exigir prueba verificable de la misma lease y sesión de admisión; una lease nueva no sustituye a la original. Permitir cleanup exacto después de expiración/release mediante esa prueba retenida, sin renovar ownership ni autorizar trabajo nuevo. Identidad declarada y API-key global, por sí solas, no bastan.

Generación distinta: `409 daemon_generation_changed`. ID desconocido o propietario incorrecto: rechazo sin mutación. ID debe ser entero positivo, excluyendo booleanos.

Bajo el lock compartido con poll, resultado y retirement:

| Estado | Transición |
|---|---|
| Queued | Retirar exactamente ese original; abandonar; ninguna liberación. |
| Delivered, recurso abierto | Abandonar; publicar una única liberación al destino conservado. |
| Terminal confirmado | Responder terminal; ninguna nueva liberación. |
| Ya abandonado | Devolver estado e ID de liberación existentes. |

Publicación y vínculo original→release deben ser atómicos. No admitir sobre el rol actual y después trasladar la liberación. No reenviarla al reemplazo. Timeout, abandono explícito, cleanup de lease y retirement invocan la misma transición.

La respuesta distingue `dropped`, `release_queued`, `terminal` y `cleanup_degraded`, con ID de liberación cuando exista. **Encolado no significa cierre confirmado.** Fallo de transporte/publicación o destino desaparecido conserva incertidumbre; no devuelve cleanup completo.

Auditar autorización/rechazo, transición, release ID y reconciliación, con sesión, lease ID y fingerprint completo del destino; nunca credenciales. Fallo de auditoría no debe impedir detener un recurso ya autorizado: ejecutar cleanup, declarar degradación y conservar el fallo observable, sin devolver éxito limpio.

### 5. Cancelación, retirement y reconciliación

**[DESIGN]** Ambos runtimes utilizan una guardia común alrededor de admisión y espera:

1. Conservar la tarea de admisión y capturar ID/recibo antes de cualquier await posterior, incluida renovación.
2. Ante `CancelledError`, resolver la admisión en curso sin perder su respuesta; si admitió, ejecutar abandono.
3. Crear una tarea de cleanup retenida y esperarla con shield. Una segunda cancelación no la cancela ni evita esperar su resolución.
4. Propagar la cancelación original; un fallo de cleanup se registra separadamente.
5. Aplicar también abandono al timeout de espera. Los caminos de admisión/espera separadas ofrecen la misma guardia.

Cleanup tiene presupuesto independiente y finito **[DESIGN: 5 s]**, aunque el deadline del tool haya vencido. Si la admisión queda ambigua sin ID, conservar su tarea/continuación y abandonar cuando aparezca un recibo; registrar incertidumbre mientras tanto. No repetir `/enqueue` tras fallo post-request. Un reintento de `/abandon` solo usa la misma generación e ID.

Tras retirement, permitir exclusivamente:

- Poll de la liberación registrada por su proceso original, verificado contra identidad retenida.
- Reconciliación mediante resultado del ID original o su release ID registrado.

No reactivar el binding ni aceptar trabajo ordinario. Verificar instancia completa y proceso/creación; no usar la epoch global nueva como sustituto de la identidad original. El handler de resultados actual solo pasa `instance` (`loopback.py:5061`): extender la acreditación Python necesaria.

Un resultado tardío válido puede reconciliar el recurso sin volver a entregarse al caller abandonado. Un ack de interrupción no confirma cierre salvo que `is_terminal` lo establezca. IDs desconocidos, destino incorrecto, protocolo incompatible o identidad ilegible no llegan al validador ni eliminan fences.

### 6. Pruebas de las uniones reales — LL-473

**[DESIGN]** Añadir `tools/tests/test_broker_binding_abandon.py`. Fixture: `ClientRuntime` real → HTTP handlers reales → `ServerState` y coordinador reales. Registrar únicamente en tests un comando dummy con release dirigido por ID y terminal explícito.

Existe composición reutilizable. **[EXACT]**, `tools/tests/session_http_helpers.py:69`:

```python
self.httpd = loopback.create_http_server(
    0, self.state, log_sink=lambda _message: None, reclaim_orphans=False
)
```

Preferir HTTP real en puerto efímero; un adapter puede sustituir solo socket/acreditación del fixture. No mockear `_call`, admisión, espera, abandono, status ni limpieza. Sin procesos DayZ o autospawn. Usar barreras, no sleeps probabilísticos.

Casos obligatorios y mutaciones aisladas:

| Caso verificable | Mutación que debe hacerlo rojo |
|---|---|
| Tokens sobreviven status→core→cliente y recibos; reemplazo mismo run/prefijo entre pasos invalida. | Truncar/omitir token en cada unión. |
| Reemplazo entre status y enqueue rechaza antes de publicar. | Quitar comparación bajo lock. |
| Cancelar queued elimina solo ese ID; siguiente comando sobrevive. | Desconectar `/abandon` de state. |
| Cancelar delivered, mediante tool registrado, publica una release original; cubrir espera normal y separada. | Omitir cleanup en cualquiera de los caminos. |
| Segunda cancelación y cancelación durante admisión/renovación siguen limpiando. | Quitar shield o conservación del recibo. |
| Repetir abandono, timeout y cleanup concurrentes conserva un solo release ID, incluso después del poll. | Quitar deduplicación retenida. |
| Reemplazo/retirement no recibe release ajena; terminal original tardío reconcilia. | Resolver por rol actual o rechazar todo resultado retired. |
| ID desconocido con identificador eco, peer ajeno y protocolo inválido no reconcilian. | Autorizar por eco u omitir acreditación. |
| Lease ajena/nueva, generación nueva y daemon antiguo fallan cerrados. | Omitir checks o aceptar prefijos. |
| Auditoría/cleanup fallidos permanecen degradados y observables. | Convertir incertidumbre en cierre. |

Cada mutación debe producir una **aserción fallida específica**, no solo import error. Restaurar entre mutaciones.

Mantener verdes: `test_client_mode`, `test_client_runtime_control_composition`, `test_loopback`, `test_instance_fence`, `test_session_http_contract`, `test_session_e2e`, `test_lease_ttl_and_file_wait`, `test_coordination_authority_fence`, `test_coordination_audit_faults`, `test_accredited_daemon_transport` y `test_native_source_seal`; al integrar, también `test_action_cursor_look_at` y `test_action_hold`.

### 7. Contrato de integración

**86a3 [DESIGN]:**

- Sustituir `observed_fence` y acceso directo a `loopback.state` por captura común con tokens completos; eliminar fallback a prefijos.
- Pasar la misma fence esperada al cursor/look-at y correlación posterior; comparar recibos y captura final. Cambio devuelve `snapshot_invalidated`.
- Registrar release `player_look_at_release` con `{"command_id": original.id}`.
- Eliminar `_release_look_at_on_cancel`, `_look_at_commands`, deduplicación/colas/traslado propios de `_enqueue_look_at_release`; usar guardia y ledger comunes.
- Conservar parsing, capabilities y validación visual/de negocio.

**9941 [DESIGN]:**

- Hacer `enqueue_action_hold`/`await_action_hold` wrappers de admisión y espera protegidas comunes, conservando `hold_id` acuñado.
- Registrar `action_hold_cancel` con ese `hold_id`; conservar su validación de protocolo/identificador y significado de cierre como política.
- Eliminar asignación de IDs, cola, fence y deduplicación propias de `_queue_hold_cancel_locked`; sustituir hooks de lease/retirement por transición común.
- Eliminar recuperación por `body["hold_id"]` para IDs desconocidos (`loopback.py:4504` del workspace).
- Conservar `hold_unreconciled`, drain y decisiones de dominio; basarlos en cierre acreditado, nunca en transmisión del cancel. F3/F15 deben probarse con historia retenida; los demás hallazgos Enforce del lote siguen siendo responsabilidad de su ronda.

### 8. Riesgos y no verificado

No se han ejecutado pruebas ni aplicado cambios. No se afirma PASS, cierre de F6/F11/F13 ni funcionamiento in-game.

Identidad de sockets REST tras retirement, orden de locks coordinador/state, cancelación durante I/O de admisión y capacidad de cleanup necesitan pruebas. Un proceso terminado no puede confirmar una liberación; el estado debe permanecer degradado. Esta propuesta no garantiza recuperación de recursos tras reinicio del daemon.

El alcance previsto queda en Python no sellado. **[EXACT]**, `tools/dayz_mcp/native_bundle.py:62`:

```python
_HASHED_MODULES = {
    "dayz_test_readiness_sha256": "dayz_test_readiness.py",
    "dayz_test_request_sha256": "dayz_test_request.py",
    "dayz_test_worker_sha256": "dayz_test_worker.py",
    "native_broker_protocol_sha256": "native_broker_protocol.py",
}
```

No cambiar esos cuatro módulos ni reseñar el launcher como parte de este lote. Si aparece una dependencia imprescindible sobre ellos, documentar el bloqueo antes de ampliar el alcance.