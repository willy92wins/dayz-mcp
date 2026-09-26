# TRIAGE-SCHEMA — DESIGN (run_id `20260903n`)

Estatus: `[DESIGN]` + `[NEEDS: TRIAGE-SCHEMA]` del plan
`plans/2026-09-03-sistema-triaje-y-tratamiento-del-buzon.md` §6.
**No es esquema de producción.** Esta hoja fija el contrato antes de escribir
la primera línea en `%LOCALAPPDATA%\DayZ_MCP\inbox\triage.jsonl`.
Esta pasada **no escribió** `triage.jsonl`.

## schema_version

Propuesta de diseño (no consumida): `1`.

Charset de `run_id`: `[A-Za-z0-9._-]` (citable como `evidence_ref`, `inbox.py:40-46`).
Esta pasada: `run_id=20260903n`.

## Dónde persistiría (DECIDIDO §9.3; no ejecutado aquí)

| Artefacto | Ruta | Esta pasada |
|---|---|---|
| Registro de triaje | `%LOCALAPPDATA%\DayZ_MCP\inbox\triage.jsonl` | **NO escrito** |
| Ledger de gates del lote | `DayZ_MCP_dev\gates\triage-<run_id>\GATES.md` | directorio creado, **GATES.md no** (no inventar EXPECT) |
| Cola del humano | `DayZ_MCP_dev\reviews\triage-<run_id>\HUMANO.md` | no |
| Vista humana | `DayZ_MCP_dev\reviews\triage-<run_id>\RESUMEN.md` | no |

Raíz `GATES.md` v9 y `gates\inbox-*.md` (35) **congeladas**: no se tocan.

## Campos (plan §6)

Una línea JSON = una fila de una ficha en una pasada. Campos:

- `schema_version`
- `id`, `kind`, `project`, `ts` (copiados del buzón)
- `run_id`
- `clase`, `disposicion`, `disposicion_previa`, `confianza`
- `senales` (D1–D7 con su valor)
- `senal_que_decide`, `motivo`
- `proponentes[]`, `decisor`, `lane_fallo[]`
- `duplicado_de`
- `estado`
- `requiere_autorizacion`
- `rama`, `commit`
- `suite` (línea OK/FAILED **y el intérprete**, nunca el rc de un pipe)
- `pyz_ok`
- `gate_id`, `gate_veredicto` (`PASS` / `FAIL` / `ABANDON`)
- `rondas`, `familia_hallazgo[]`
- `backlog[]`
- `preguntas[]`, `bloquea[]`
- `resolution_text`
- `evidence_ref` (vacío mientras el hueco de `pipeline_resolve` siga abierto)
- `hash_contenido` (SHA-256 de `title+"\n"+body`; único consumidor = duplicados exactos entre entradas distintas; si no se usa para eso, se retira)

`clase` ∈ {`X`,`C1`,`C2`,`C3`,`C4`} ∪ `null` (DIFERIDO).
`confianza` ∈ {`FIRME`,`DUDOSA`}.
`estado` ∈ {`SIN_TRIAR`,`TRIADO`,`DIFERIDO`,`DESCARTADO`,`EN_CURSO`,`BLOQUEADO_HUMANO`,`EN_VERIFICACION`,`RESUELTO`} — ocho, ni uno más.
`ABANDON` no es estado: vive en `gate_veredicto`.
`ORCHESTRATOR_NEEDED` es motivo de `BLOQUEADO_HUMANO`.

## Escritor único por pasada (writer-once)

Un solo escritor por `run_id`. No hay reescritura de líneas previas.
Idempotencia: gana la **última línea por `id`**. Una fila ya `TRIADO` nunca se pisa.
`SIN_TRIAR` y `DIFERIDO` reentran en la pasada siguiente.

```
pendientes = {id de pipeline_inbox(include_resolved=False)}
           − {id del registro con estado ∈ {TRIADO, EN_CURSO, EN_VERIFICACION,
                                            BLOQUEADO_HUMANO, RESUELTO, DESCARTADO}}
```

## Append acotado (una línea, un write)

Mismo patrón que `inbox.py:_append_jsonl`:

- `json.dumps(..., ensure_ascii=False, separators=(",", ":")) + "\n"`
- `os.O_APPEND | os.O_CREAT | os.O_WRONLY`
- un `write` por fila; el fichero **nunca se reescribe**
- sin rewind, sin truncate, sin lock de árbol entero

## Recuperación de cola parcial

Última línea truncada (JSON incompleto al leer) = **se descarta**.
Cuenta como `malformed`, no se repara in-place.
El lector legacy ignora líneas que no parsean (`inbox.py:169-176` como modelo).

Retirada segura: dejar de appender; el buzón sigue siendo la fuente de
`id`/`kind`/`title`/`body`. El JSONL es registro lateral, no autoridad.

## Fuera de este DESIGN

- No tool MCP nueva, no campo nuevo en `feedback.jsonl`.
- No `hash_contenido` como guardia de idempotencia de la misma entrada
  (las entradas no se reescriben).
- Hoja markdown por ficha solo C3/C4; C1/C2 = la fila del registro.
