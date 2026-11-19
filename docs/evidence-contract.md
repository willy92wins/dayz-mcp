# Raw-Evidence Contract (`dayz-mcp-evidence-v1`)

`tools/dayz_mcp/evidence.py` defines the common shape and validator for tools
that return raw measurement rows from DayZ.

## Purpose

The MCP measures; the consumer decides what a measurement means. Tools publish
raw quantities with explicit units and never verdicts, scores, weights, or
thresholds. Nothing measured is filtered out, and anything that could not be
measured is omitted with a reason rather than published as `0` or `false`.

## Envelope

`evidence_result(kind, rows, *, truncated)` returns a dict with exactly five
members:

- `schema`: `"dayz-mcp-evidence-v1"` (`EVIDENCE_SCHEMA`).
- `kind`: the declared `EvidenceKind.name`.
- `units`: `"tick_ms": "ms"` followed by every `number` and `vec3` field on the
  kind with its declared unit, in declaration order.
- `rows`: the list returned by `normalize_rows(kind, rows)`. An empty list is
  valid.
- `truncated`: mandatory keyword-only `bool` (no default). `true` when the
  producer left out measured rows for that request due to its row or size cap;
  any continuation cursor lives outside the envelope in the tool's own reply.

## Common Row Members

Every wire row and every published row carries three mandatory members:

- `uid`: non-empty string identifying the subject (`GetPlainId`, the same
  identifier `query_all_players` reports).
- `tick_ms`: non-negative integer (`GetGame().GetTime()` on the server, in
  milliseconds). `tick_ms` is the only clock in the contract.
- `gap_kind`: `""` when no gap occurred, or a name declared in
  `kind.gap_kinds`.

## Field Types and Units

All names (`kind.name`, field names, and `gap_kinds`) must match
`[a-z][a-z0-9_]*`. Field names cannot be `uid`, `tick_ms`, or `gap_kind`, and
cannot end in `_available`.

Closed `FIELD_TYPES`:

- `number`: finite JSON number (int or float, never `bool`). Requires a unit
  from `UNITS`.
- `vec3`: list of three `number` values. Requires a unit from `UNITS`.
- `int`: integer (never `bool`). Must declare `unit=""`.
- `uid`: non-empty string. Must declare `unit=""`.
- `token`: non-empty string. Must declare `unit=""`.
- `flag`: `bool` or integer `0` / `1` on the wire, published as `bool`. Must
  declare `unit=""`.

Closed `UNITS`: `m`, `m/s`, `deg`, `s`, `ms`.

Every `EvidenceKind` must declare at least one `number` or `vec3` field: a kind
made only of `flag`, `token`, `int`, and `uid` fields is rejected. Row
validation checks types, never plausibility or ranges. Any wire member not
declared by the contract or the kind is rejected rather than copied or silently
dropped.

## Absence: `optional` and `absent_with`

The Enforce bridge serializes classes with `JsonSerializer` (`WriteToString` in
`addon/scripts/5_Mission/MCPBridge.c`), which always emits every member with its
default value (`0`, `false`, `""`). Because an unmeasured quantity arrives on
the wire as `0`, `normalize_rows` strips absent fields before publication using
two mutually exclusive declarations on `EvidenceField`:

1. **`optional=True`** (for a value the server may not be able to read): the
   wire always includes `<field>_available` as a `flag`. When `true`, `<field>`
   must be present with its declared type and both `<field>` and
   `<field>_available: true` are published. When `false`, only
   `<field>_available: false` is published and the wire value of `<field>` is
   neither inspected nor copied.
2. **`absent_with`** (a non-empty subset of `kind.gap_kinds`, for a measurement
   that could not be taken): when the row's `gap_kind` is in `absent_with`, the
   field is omitted from the published row regardless of what the wire carries.
   Otherwise the field must be present with its declared type.

A field cannot set both `optional=True` and a non-empty `absent_with`. There is
no other way to omit a measurement: without a sample, there is a `gap_kind`.

## Forbidden Vocabulary

`kind.name`, every field name, and every name in `gap_kinds` is split on `_`;
if any whole segment is in `FORBIDDEN_NAME_PARTS`, constructing the
`EvidenceKind` raises `ValueError`:

`cheat`, `cheater`, `cheating`, `ban`, `banned`, `kick`, `kicked`, `sanction`,
`sanctioned`, `verdict`, `veto`, `vetoed`, `weight`, `weights`, `weighted`,
`score`, `scored`, `limit`, `limits`, `ceiling`, `threshold`, `suspect`,
`suspected`, `suspicious`, `violation`, `detect`, `detected`, `detection`.

Whole-segment matching means names such as `band_m`, `urban_m`, and
`ray_budget_exceeded` pass, while `weight_hint`, `speed_limit`, `kick_reason`,
and `is_cheater` are rejected.

## Wire and Published Example

For a kind `probe` with fields `distance_m` (`number`, `m`), `pos` (`vec3`,
`m`), `seq` (`int`), `target_uid` (`uid`), `hit_class` (`token`), `in_vehicle`
(`flag`), `pitch_deg` (`number`, `deg`, `optional=True`), and `clearance_m`
(`number`, `m`, `absent_with=frozenset({"ray_skipped"})`), and `gap_kinds`
`{"ray_skipped", "clock_not_monotonic"}`:

Wire row:

```json
{
  "uid": "76561198000000001",
  "tick_ms": 120000,
  "gap_kind": "",
  "distance_m": 12.5,
  "pos": [1.0, 2.0, 3.0],
  "seq": 7,
  "target_uid": "76561198000000002",
  "hit_class": "-",
  "in_vehicle": 0,
  "pitch_deg": 0,
  "pitch_deg_available": 0,
  "clearance_m": 0.75
}
```

Published envelope (`evidence_result(kind, [wire_row], truncated=False)`):

```json
{
  "schema": "dayz-mcp-evidence-v1",
  "kind": "probe",
  "units": {
    "tick_ms": "ms",
    "distance_m": "m",
    "pos": "m",
    "pitch_deg": "deg",
    "clearance_m": "m"
  },
  "rows": [
    {
      "uid": "76561198000000001",
      "tick_ms": 120000,
      "gap_kind": "",
      "distance_m": 12.5,
      "pos": [1.0, 2.0, 3.0],
      "seq": 7,
      "target_uid": "76561198000000002",
      "hit_class": "-",
      "in_vehicle": false,
      "pitch_deg_available": false,
      "clearance_m": 0.75
    }
  ],
  "truncated": false
}
```

## Producer Requirements

Every tool producing `dayz-mcp-evidence-v1` responses must:

- Publish every measured row in order without filtering by value or threshold.
- Define a per-response row cap and document the byte budget showing that a
  full response stays below the daemon's 1 MiB body cap (`MAX_BODY_BYTES` in
  `tools/dayz_mcp/loopback.py`).
- Pass `truncated` explicitly (`true` whenever measured rows were omitted for
  that request, `false` otherwise).
- Use `tick_ms` as the only clock in the contract.
- Register any new server bridge commands in `SERVER_CAPABILITIES` and
  `SERVER_ARG_CONTRACT` in the producer's own PR, updating the `ach` hash in
  Python and Enforce together.

## Versioning

Any change to the envelope, the common row members, or the validation rules in
this document requires a new schema name (`v2`). Each `EvidenceKind` and its
field table belong to its producer.
