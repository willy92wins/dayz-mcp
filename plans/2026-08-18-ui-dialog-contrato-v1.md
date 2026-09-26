# Contrato `ui_dialog` v1 (fase 1, Python)

Cerrado por el orquestador. Fase 1 implementa validación, whitelist y espera
fuera de `tool_lock`. El PBO actual responde `unknown_command`; eso es lo
esperado hasta el ciclo 6.

Nombre idéntico en los tres sitios: tool MCP, `CLIENT_COMMANDS`, y el futuro
`if/else` Enforce. Peer `client`. Exige lease por el mismo mecanismo que el
resto de verbos mutantes (`command_requires_lease`: todo lo que no está en
`READ_ONLY_COMMANDS`). Relación con `notify_players`: no se duplica; el toast
vanilla sigue siendo unidireccional. `ui_dialog` solo existe cuando hay que
**saber** que el jugador respondió.

Alcance v1: el cliente local donde corre el puente. Sin `target_player`.

---

## Firma

```
ui_dialog(kind, title, message="", fields=None, timeout_s=60.0)
```

| Campo | Regla |
|---|---|
| `kind` | `acknowledge` \| `confirm` \| `form` (Literal + validación) |
| `title` | str, 1..80 chars tras strip |
| `message` | str, 0..600 chars. Obligatorio no vacío (tras strip) para `acknowledge` y `confirm`. Opcional en `form`. |
| `fields` | Solo con `kind="form"` (otro kind → ausente/None; si viene → `bad_args`). Lista de 1..6 objetos. |
| `timeout_s` | número finito 5.0..240.0; default 60.0 |

Cada elemento de `fields` tiene claves exactas `{id, label}` más opcionales
`{required: bool = true, default: str = ""}` en la tool (el agente sigue
enviando `default`). En el cable Enforce la clave es `default_text`
(`default` es palabra reservada). Ninguna otra clave.
`id` casa `^[a-z][a-z0-9_]{0,31}$` y es único en la lista. `label` 1..60
chars. `default` / `default_text` ≤ 256 chars.

7 campos (N+1) → `bad_args` **antes** de encolar. Claves desconocidas se
rechazan (no se descartan: en Enforce se perderían en silencio).

Presupuesto Python del puente = `timeout_s + 10.0` (≤ 250 < `MAX_TIMEOUT_S`
300.0). El sondeo usa `WAIT_FOR_MIN_POLL_INTERVAL_S` (0.5 s). El
`operation_timeout_s` encolado es ese presupuesto, no el timeout del jugador.

Toda violación de args en la tool → `ToolError("bad_args: <campo> <motivo/limite>")`.
En el daemon (`POST /enqueue`) el token es solo `"bad_args"` (sin eco del
texto del llamante: acaba en `abort_reason` y `_public_enqueue_error` lo
colapsaría a `remote_error`).

Mapeo tool → args del puente: **una** función, `ui_dialog.bridge_args`.
Fase 1 envía `fields` como array de objetos. Alternativa (no implementada):
aplanar a `field_ids` / `field_labels` / `field_required` / `field_defaults`
si el serializador de Enforce no acepta `array<ref T>` de entrada. La firma
de la tool no cambia.

Higiene: no loguear `values` ni `default` en INFO. Se cumple por
ausencia de logging de esos campos (no hay helper cableado).

---

## Ejemplos — tres `kind`

### acknowledge

Tool:

```json
{"kind": "acknowledge", "title": "Mod loaded", "message": "Hidden Stash is ready."}
```

Puente (`bridge_args`):

```json
{
  "kind": "acknowledge",
  "title": "Mod loaded",
  "message": "Hidden Stash is ready.",
  "timeout_s": 60.0
}
```

`fields` no viaja.

### confirm

```json
{
  "kind": "confirm",
  "title": "Delete stash?",
  "message": "This cannot be undone.",
  "timeout_s": 30.0
}
```

### form

```json
{
  "kind": "form",
  "title": "Name the stash",
  "message": "Shown on the map pin.",
  "fields": [
    {"id": "label", "label": "Display name"},
    {"id": "note", "label": "Note", "required": false, "default": ""}
  ]
}
```

Puente (objetos; `required`/`default_text` normalizados):

```json
{
  "kind": "form",
  "title": "Name the stash",
  "message": "Shown on the map pin.",
  "timeout_s": 60.0,
  "fields": [
    {"id": "label", "label": "Display name", "required": true, "default_text": ""},
    {"id": "note", "label": "Note", "required": false, "default_text": ""}
  ]
}
```

Aplanado (alternativa ciclo 6, **no implementada**):

```json
{
  "kind": "form",
  "title": "Name the stash",
  "message": "Shown on the map pin.",
  "timeout_s": 60.0,
  "field_ids": ["label", "note"],
  "field_labels": ["Display name", "Note"],
  "field_required": [true, false],
  "field_defaults": ["", ""]
}
```

---

## Resultado y cinco estados terminales

`MCPResult.state` ya es `ref MCPPlayerState` (objeto podable). La fase 2
**no** puede emitir un string ahí. El desenlace del diálogo viaja anidado
bajo **una** clave nueva `dialog` — la única clave de primer nivel nueva
en `MCPResult`:

```json
{"ok": 1, "id": 12, "dialog": {"state": "completed", "dismissed_by": "ok", "elapsed_s": 4.2}}
```

`dialog` ausente o no-objeto → `ToolError("bridge_bad_result: dialog missing")`.
Python (`interpret_result`) valida el objeto anidado y devuelve al agente el
resultado **público aplanado**: `{ok, state, dismissed_by?, choice?, values?,
values_by_id?, reason?, elapsed_s}` más passthrough de `id` y `_server`.

`cancelled` / `timed_out` **no** se convierten en `choice:"no"` ni en error:
son respuestas válidas (`ok` true, `state` lo dice).

Un `state` fuera del enum, `values` que no casan con los ids declarados
(mismo conjunto, mismo orden) o `choice` fuera de `yes`/`no` →
`ToolError("bridge_bad_result: ...")`.

El vencimiento del presupuesto Python **sin** resultado →
`ToolError("timeout waiting for ui_dialog ...")` (transporte; distinto de
`timed_out`).

Python añade `values_by_id` (dict) cuando hay `values`.

### completed — acknowledge

Cable:

```json
{"ok": 1, "dialog": {"state": "completed", "dismissed_by": "ok", "elapsed_s": 4.2}}
```

Público:

```json
{"ok": 1, "state": "completed", "dismissed_by": "ok", "elapsed_s": 4.2}
```

### completed — confirm

Cable:

```json
{"ok": 1, "dialog": {"state": "completed", "choice": "yes", "elapsed_s": 2.1}}
```

Público:

```json
{"ok": 1, "state": "completed", "choice": "yes", "elapsed_s": 2.1}
```

`choice:"no"` es el botón No, no un cierre.

### completed — form

Cable:

```json
{
  "ok": 1,
  "dialog": {
    "state": "completed",
    "elapsed_s": 12.0,
    "values": [
      {"id": "label", "value": "North cache"},
      {"id": "note", "value": ""}
    ]
  }
}
```

Público (Python añade `values_by_id`):

```json
{
  "ok": 1,
  "state": "completed",
  "elapsed_s": 12.0,
  "values": [
    {"id": "label", "value": "North cache"},
    {"id": "note", "value": ""}
  ],
  "values_by_id": {"label": "North cache", "note": ""}
}
```

Nunca parciales: si el jugador no envía, no hay `values`.

### cancelled

Cerrar una confirmación (X / Escape / cierre general), no "No":

Cable: `{"ok": 1, "dialog": {"state": "cancelled", "elapsed_s": 1.4}}`

Público:

```json
{"ok": 1, "state": "cancelled", "elapsed_s": 1.4}
```

### timed_out

El job del cliente venció; el jugador no contestó. La UI queda oculta.

Cable: `{"ok": 1, "dialog": {"state": "timed_out", "elapsed_s": 60.0}}`

Público:

```json
{"ok": 1, "state": "timed_out", "elapsed_s": 60.0}
```

### disconnected

El cliente/sesión se fue con el diálogo abierto.

Cable: `{"ok": 1, "dialog": {"state": "disconnected", "elapsed_s": 8.8}}`

Público:

```json
{"ok": 1, "state": "disconnected", "elapsed_s": 8.8}
```

### rejected

Segundo diálogo con uno abierto. Rápido, `reason: "busy"`. No altera el primero.

Cable: `{"ok": 1, "dialog": {"state": "rejected", "reason": "busy", "elapsed_s": 0.05}}`

Público:

```json
{"ok": 1, "state": "rejected", "reason": "busy", "elapsed_s": 0.05}
```

Tres reglas:

1. Cancelar nunca es "No".
2. Nada de respuestas parciales.
3. `timed_out` ≠ error de transporte.

---

## Espera fuera de `tool_lock`

`ui_dialog` no envuelve la espera en `call_bridge` bajo el lock. Sigue la
forma de `execute_wait_for`: encolado breve bajo `tool_lock`, cada sondeo
breve bajo `tool_lock`, `asyncio.sleep` fuera. Superficie aditiva en
`Runtime` y `ClientRuntime`:

- `enqueue_bridge(cmd, args, peer, timeout_s) -> int`
- `probe_bridge_result(cmd, command_id, peer) -> dict | None`
- `abandon_bridge(command_id, reason)`

Embebido: `enqueue_command` / `take_result` / `abandon_command`.
Cliente: `POST /enqueue` + `GET /await?remove=1`. `ClientRuntime.abandon_bridge`
es no-op: el daemon no expone ruta de abandono; reaping por `COMMAND_TTL_S`
(si no se entrega) o por el deadline de operación.

`call_bridge` no cambia. `wait_for`, `ui_dialog` y `playbook_run` son las
tres entradas que duermen fuera del lock.

---

## Sonda 1 — creación del host oculto (evidencia de fuente + pregunta abierta)

**Evidencia.** El puente cliente nace en el hook de misión, no en un
constructor de widgets:

- `MissionGameplay.OnMissionStart` (`DayZ_MCP\scripts\5_Mission\MissionGameplay.c:8-16`)
  llama `MCPClientBridge.Get()` y `OnTick(0.0)`.
- `OnUpdate` (`:19-27`) vuelve a `Get()` + `OnTick`.
- `Get()` (`MCPClientBridge.c:162-169`) construye el singleton la primera vez.
  El constructor (`:135-155`) arma Rest/jobs/pending; **no** llama
  `GetWorkspace()` ni `CreateWidgets`.
- Teardown: `~MissionGameplay` → `ShutdownInstance()` (`MissionGameplay.c:3-6`).
- `ResolveUiRoot` (`MCPClientBridge.c:1356-1370`) es el único uso de
  `GetGame().GetWorkspace()` en el addon. Corre en el dispatch de
  `ui_tree` / `ui_set_text` / `ui_click` (`:1017`, `:1052`, `:1128`), con la
  misión ya rodando. Si no hay workspace, error `no_workspace`.
- Precedente ATM de bloqueo de input (lectura, no herencia; el ATM usa Dabs):
  `C:\Users\guill\OneDrive\Documentos\DayZ Projects\LFPowerGrid\scripts\4_World\LFPG_BTCAtmView.c:1261-1265`
  — `#ifndef SERVER`, `g_Game.GetMission().PlayerControlDisable(INPUT_EXCLUDE_ALL)`,
  flag `m_ControlsLocked`. El propio puente ya tiene el mismo disable en
  `SuppressGameplay` (`MCPClientBridge.c:2709-2728`).

**Pregunta abierta (bloquea el DTO de fase 2, no la fase 1):** ¿dónde crear
el host oculto **una sola vez**? Candidatos: `Get()` (constructor; workspace
**no** demostrado), primer `OnTick` tras `TryInit` (`:219`),
`MissionGameplay.OnMissionStart` después de `Get()`, o lazy en el primer
`ui_dialog`. `GetWorkspace()` vale en el dispatch; no hay evidencia de que
valga en el constructor del singleton.

---

## Sonda 2 — `array<ref T>` como ENTRADA (evidencia de fuente + pregunta abierta)

**Evidencia.**

- `MCPArgs` de entrada hoy: `array<float>` / `array<string>` (`pos`, `want`,
  …). Ningún `array<ref T>` en `MCPMessages.c:12-106`.
- El poll **sí** deserializa `array<ref T>` de entrada:
  `MCPCommandBatch.commands` es `ref array<ref MCPCommand>`
  (`MCPMessages.c:116-123`) y se llena con
  `JsonSerializer.ReadFromString(batch, data, parseError)`
  (`MCPClientBridge.c:326-329`; espejo server `MCPBridge.c:227-230`).
  Eso es el camino vivo de cada comando.
- `MCPCommand.args` es un `ref MCPArgs` anidado, también de entrada.
- Arrays de objetos en **resultados** (`MCPUiNode`, `MCPAllPlayer`, …) no
  responden la pregunta de entrada.
- Vanilla: `JsonSerializer.ReadFromString` (`scripts\3_game\gameplay.c:100`)
  documenta objetos anidados y arrays dinámicos de escalares, no un ejemplo
  de `array<ref T>`. `JsonApiStruct` es otra API.

**Conclusión: sí** — el serializador que el puente ya usa acepta
`array<ref T>` como entrada (`MCPCommandBatch.commands`).

**Pregunta abierta:** un miembro nuevo `ref array<ref T>` **dentro de
`MCPArgs`** (un nivel más de anidación que el batch) no está demostrado con
una petición de dos campos. El plan (`ui-dialog-plan-fusionado.md:266-268`)
exige esa petición antes de cerrar el DTO. Fase 1 envía el array de objetos;
si esa petición falla en el ciclo 6, se aplana en `bridge_args`.

---

## Cable (v1.1)

Versión de puente objetivo = 8 (bump post-gate).

Miembros Enforce exactos:

```
MCPArgs {string kind; string title; string message; float timeout_s; ref array<ref MCPDialogField> fields}
MCPDialogField {string id; string label; bool required; string default_text}
MCPResult.dialog: ref MCPDialogResult {string state; string dismissed_by; string choice; ref array<ref MCPDialogValue> values; string reason; float elapsed_s}
MCPDialogValue {string id; string value}
```

La tool sigue aceptando `default` del agente. `bridge_args` emite `default_text`.

Enforce serializa todos los miembros del DTO: strings sin asignar llegan
como `""` y arrays como `[]`. Python normaliza **antes** de validar:

- `choice` / `dismissed_by` / `reason` == `""` → ausente
- `values` == `[]` → ausente, salvo `completed` de `form` (fields ≥ 1, así
  que `[]` sigue siendo `bridge_bad_result`)
- `elapsed_s` sigue obligatorio

`ok=1` en todo estado terminal (`completed`, `cancelled`, `timed_out`,
`disconnected`, `rejected`).

---

## Fuera de este contrato

RPC cliente→servidor, jugadores remotos, tres tools, duplicar
`notify_players`, layout por pregunta, `CreateWidgets` por apertura, cola de
diálogos, tratar cancel/timeout como "No", devolver texto parcial, heredar de
Dabs, tocar Enforce/`DayZ_MCP\**` en esta fase.
