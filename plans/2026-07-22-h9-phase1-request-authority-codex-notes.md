# H9 Fase 1 — Nota Codex para autoridad filesystem de la request

Fecha: 2026-07-22  
Estado: **aprobación operativa heredada del usuario; implementación TDD pendiente**  
DPF: H9/H10 (`product-spec.md:135-136`)  
Plan padre: `plans/2026-07-22-bug046-h9-native-launcher-plan.md:21-29,165-170`

## Problema verificado

- [EXACT] `RequestProjectPolicy` sólo contiene strings y
  `parse_dayz_test_request` confina con `ntpath.commonpath`; no abre objetos ni compara
  volume/file identity (`tools/dayz_mcp/dayz_test_request.py:123-164,174-219,222-380`).
- [EXACT] El plan aprobado exige `request-policy.json` sellado, roots exactas con
  volume/file identity, `dev_root` exacto y rechazo de reparse inesperado antes de crear
  `operation_id`/enqueue (`plans/2026-07-22-bug046-h9-native-launcher-plan.md:23-29,162-170`).
- [EXACT] El registro/launcher productivo continúa vacío/fail-closed; por tanto esta fase
  puede añadir la frontera y sus fixtures sin lanzar procesos ni cambiar un formato vivo
  (`tools/approved-launchers.json:1-4`; `tools/dayz_mcp/secure_launcher.py:46-53`).

## Delta autorizado sin cambiar arquitectura

- [DESIGN] Añadir `request_path_authority.py`, stdlib/Win32 read-only. Consume una policy
  semántica ya parseada y una policy sellada con identidades esperadas; nunca aprende
  autoridad desde request, cwd, env, listener ni existencia casual.
- [DESIGN] Abrir cada root/directorio por handle con `FILE_READ_ATTRIBUTES`, compartir
  read/write pero no delete, `FILE_FLAG_BACKUP_SEMANTICS|OPEN_REPARSE_POINT`; comparar
  FileId+volume, tag, final path y objeto resuelto. Mantener handles hasta cerrar la
  transacción para bloquear rename/delete de la cadena acreditada sin impedir build dentro.
- [DESIGN] Rechazar cualquier reparse interno. La única excepción futura es la junction
  raíz exacta `P:\Mods`: tag mount-point, identidad lexical, target final e identidad target
  deben coincidir con el manifest; symlink u otro tag no equivalen a junction.
- [DESIGN] Resolver cada mod relativo dentro de exactamente una raíz; cero o varias
  coincidencias falla cerrado. Paths absolutos, source y misión custom deben pertenecer y
  acreditarse contra exactamente una root de su misma entrada policy.
- [DESIGN] La captura de identidades disponible en esta fase será privada y test-only. La
  vía productiva desde `secure_launcher` no se conectará hasta que Fase 3 aporte el
  `request-policy.json` manifest-bound; no se crea un registro mutable alternativo.

## Viability tests antes de producción

1. [DESIGN] Árbol temporal regular: dev/source/mission/mods se acreditan, conservan handles
   durante el contexto y los cierran al salir.
2. [DESIGN] Root reemplazada tras sellarla: identity drift falla antes de enqueue.
3. [DESIGN] Dos roots que contienen el mismo mod relativo: ambiguous root falla cerrado.
4. [DESIGN] Policy semántica mezclada con identidades de otra entrada: rechazo.
5. [DESIGN] Reparse interno: rechazo. Junction raíz sólo pasa con el tag/identidades/target
   sellados exactos; si el host no permite crearla, el fixture se marca skip y queda gate
   host explícito, no PASS implícito.
6. [DESIGN] Auditor: el módulo no importa launcher/backend ni contiene process/HTTP sinks.

No hay migración/rollback persistente en esta fase: no existe todavía
`request-policy.json` productivo y `approved-launchers.json` no cambia. El formato futuro
se cierra con compatibilidad/rollback en Fase 3 antes de escribirlo.
