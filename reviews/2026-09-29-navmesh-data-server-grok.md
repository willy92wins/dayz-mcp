# Grok 4.7 - revision navmesh_data_server

Fecha: 2026-09-29. Ruta: grok-cli 1.0.41, modelo reportado: grok-4.7-build.
Sesion: 01a0ee0b-df86-7fa1-8fb8-28924fd86060. Solo read_file/grep/list_dir; sin herramientas de escritura o shell. Primera pasada limitada a15 turnos; cierre posterior de1 turno sin nuevas lecturas.

**SOUND.** No P1 or P2 defect in the boolean option.

The flag is a strict bool defaulting to false. `dayz_test_request.py:367-421` and `:464` default it, reject non-bools, and reject `mode != "server"`, `kill`, or `pack_only` before canonical bytes are built. Legacy keysets at `:62-70` still accept documents that omit it, and the refilled value is false, so default server argv does not gain the switch. `dayz_test_tool.py:301` and `:1539` put that bool into the document that is parsed and returned as `canonical_bytes`. `dayz_test_worker.py:273-274` appends the single literal `-startNavmeshDataServer` only on the server argv branch. Preflight returns at `:717-722` before `_start_core`. `server.py:5709` and `:5730-5733` publish `StrictBool = False` and reject other modes or `pack_only` before queue admission (`:5748`) and takeover (`:5850`). `kill` is not a `dayz_test_run` parameter (`:5688-5713`); the stop path leaves the flag at its default. Broker frames stay `{request_sha256}` plus hashed stdin (`native_broker_protocol.py:137-144`). The lifecycle start parser accepts any non-empty argv string and covers it with `launch_request_sha256` (`process_lifecycle.py:2477-2522`). No extra public argument channel was added.

The bundle-not-in-diff and old-hash replay concerns are not defects. You built the bundle with `build_native_launcher.py --offline --verify-reproducible` (64 tests, 0 skips); those binaries are gitignored. The sealed worker is not registered yet by design. Parser compatibility is omitted-key input and default false, not replay of a pre-navmesh byte string into another bundle.

**Unverified live gates**

- DayZDiag actually honoring `-startNavmeshDataServer`, and NavMeshGenerator connecting or writing a usable navmesh. `server.py:3205-3209` says a successful launch does not prove that. The adapter test only checks the sealed JSON, `bridge_ready is None`, and zero bridge calls.
- A registered launcher and a real broker/`Popen` against DayZDiag. Source admission does not strip the token; that host path is not integrated yet.

## Recepcion Codex

Sin defectos confirmados que corregir. Citas del transporte/parser/worker comprobadas; los gates live siguen abiertos. Construccion y64 tests de bundle fueron evidencia aportada por esta sesion, no ejecutada por el revisor. El transcript registra solo lecturas/busquedas;0 acciones de escritura del revisor. Arbol compartido sigue limpio en28f76cd.
