# Addendum a la ronda 9 — revisión Opus del producto final (rondas 7-8), llegada con la 9 en vuelo

Fuera del sello a propósito: `gate/` está sellado para la ronda 9 y el brief está congelado (C6).

## Lo que Opus encontró (6 sondas, RC=0, producto con los hashes del brief)

- **H1 ALTA** — `begin_release_owner`, rama de limpieza ASÍNCRONA (`process_lifecycle.py:2308`):
  libera a `RUNNING_IDLE` sin `_drain_pending_for_runs`; `record_poll` no revalida el estado
  durable. Control positivo: la rama síncrona sí drena.
- **H2 MEDIA-ALTA** — `repair_manifest_recovery` llama a `recover_after_restart()` en caliente
  (`:2500-2503`, endpoint admin vivo vía `loopback.py:3101`): RUNNING con dueño → `RUNNING_IDLE`
  sin cercar ni drenar.
- P7 (tumba en crédito y basal), P8 y las regresiones P1/P4/P5 aguantan con control positivo.
- Familia: visitada (autoridad del destino); mecanismo nuevo: **la invariante se ató a los
  call-sites de la transición en vez de a la transición**. Productores de `RUNNING_IDLE`:
  `release_owner` directo, `begin_release_owner` síncrono, `begin_release_owner` asíncrono,
  `admin_reconcile` con supervivientes, `repair_manifest_recovery` → `recover_after_restart`.

## Cómo encaja con la ronda 9 en vuelo

El brief de la 9 manda «cerco lógico bajo el lock del loopback ANTES de publicar» y un
[DESIGN] «UN método del lifecycle quiesce → persistir → confirmar/revertir con el mismo nombre en
las dos entradas», nombrando `release_owner`, `begin_release_owner` y `admin_reconcile`. No
nombra la rama asíncrona ni `repair_manifest_recovery`. Si el implementador ató el cerco a la
transición (el diseño pedido), los cinco quedan cubiertos; si lo ató a los tres call-sites
nombrados, quedan dos abiertos.

## Qué hago al recibir la 9

1. Recepción normal (sello, write-set, gates corridos por mí, rojo-antes).
2. **Ejecutar las sondas de Opus** de `review7-opus/` (H1 rama asíncrona; H2 repair) contra el
   árbol entregado. Si reproducen → ronda 10 corta: los dos productores restantes por el mismo
   método único + `record_poll` revalida el estado durable antes de entregar (defensa en
   profundidad). Si no reproducen → constan como cerrados por la 9 con la sonda como evidencia.
3. El check N25 sólo mide `release_owner`; el gate no crece para esto: la sonda del revisor es
   el instrumento (precedente N10), y la ronda 10, si hace falta, lleva su ledger.
