# gpt-6.1-sol scoped review of the loopback integration onto #206 (unedited)

VERDICT: APPROVED

## FINDINGS

Sin hallazgos P1–P3 en la integración revisada.

- **Capacidad y orden:** `tools/dayz_mcp/loopback.py:2903` conserva `refusal = self._capability_refusal_locked(peer, cmd, fence_instance)` y responde `(409, {"error": refusal})`. Ocurre antes de asignar ID, ejecutar `commit`, registrar propietario/bot o publicar. Las dos admisiones llaman al mismo helper (`:2806`, `:2833`), por lo que ambas incluyen este rechazo. Se conservan `bridge_capability_missing` y `arg_contract_mismatch`.

- **Seguimiento por lease:** en `tools/dayz_mcp/loopback.py:2998`, `if commit is not None and not commit(command_id):` devuelve `lease_invalid` antes del registro. Después, `self._bot_by_lease.setdefault(owner_lease_id, set()).add(raw_object_id)` (`:3009`) precede inmediatamente a `queue.append(command)` (`:3012`), dentro del mismo lock. El escenario commit fallido → reintento válido produjo cero bots registrados tras el fallo y exactamente un comando publicado tras el éxito.

- **Conservación de #206:** ambas admisiones y publicaciones siguen bajo `with self._lock` (`tools/dayz_mcp/loopback.py:2805`, `:2832`); la consulta de liveness queda fuera. `if repin == gate_pin and gate_verdict == "dead":` (`:2838`) mantiene el rechazo `client_process_gone` antes de publicar. Los escenarios ejecutados confirmaron la segunda comprobación de parada, capacidad y fence, y que un destino cambiado no hereda el resultado «dead».

- **Limpieza:** `tools/dayz_mcp/loopback.py:3976` conserva `report = self.enqueue_bot_stops_for_lease(lease_id)`. Los tests comprobaron la entrega del `bot_stop` al liberar el lease y la degradación cuando su admisión se rechaza.

La comparación de los diffs conserva todas las adiciones y eliminaciones de g2, salvo la indentación y la adaptación del retorno al helper. Los cuatro métodos extraídos por #206 conservan su AST tras descontar las dos inserciones de g2.

**Validación:** 19 tests de `test_player_kill` y `test_bot_verbs`: OK. Ocho escenarios adicionales ejecutados en memoria: OK.

## NOT VERIFIED

- La suite completa `test_client_process_gone` no pudo validarse: sus fixtures requieren `TemporaryDirectory`, bloqueado por el sandbox de solo lectura. Los escenarios adicionales usan fixtures y probes controlados; no prueban el lifecycle nativo completo.
- Ejecución in-game y comportamiento fuera de esta integración.

