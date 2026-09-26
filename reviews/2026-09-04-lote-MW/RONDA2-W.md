# Ronda 2 del lote W — aplicada por el orquestador (2026-09-04)

Ronda 1: Composer 2.5 por `cursor-agent` entregó W6, W7, W8, W9 y W12 a CIEGAS (shell bloqueado
por los pre-hooks del host, ver lote M). Recepción: sello 4/4, write-set limpio, G1 16/16 VERDE,
G2 ROJA: `test_doctor` 8 failures + 1 error.

Causa, medida: `_check_native_bundle_closure` se cableó en `_diagnose` sin pasar por `sources`, así
que cada diagnóstico sintético de los tests abría el registro REAL del host (`open_approved_launcher`
usa `<tools>/approved-launchers.json`, copiado al workspace) y añadía `NATIVE_BUNDLE_EXTERNALS_OK
checked=23` a listas de findings que los tests comparan por igualdad. Es exactamente el modo de
fallo que el brief no nombró (límite del ENCUADRE: mi S3 no dijo que el doctor inyecta todo por
`DoctorSources`). Dato de propina: los 23 externals del bundle desplegado casan hoy con el disco.

Parche del orquestador (3 sitios, <15 líneas, reversible):
1. `DoctorSources.native_launcher_id: str | None = None` (campo nuevo, defaulted, al final).
2. `_check_native_bundle_closure(sources, findings)`: retorna sin finding cuando el id es None;
   usa `sources.native_launcher_id`. Call-site en `_diagnose` actualizado.
3. `default_sources(...)` fija `native_launcher_id="dayz-test-v1"`: la corrida real sí comprueba.
4. `tests/test_doctor.py`: `NativeBundleClosureWiringTests` (sin id → sin finding; launcher
   ilegible → un único WARN `NATIVE_BUNDLE_MANIFEST_UNREADABLE`).

Todo lo delegado y lo parcheado se revisa por otra familia (Codex) sobre el diff integrado.
