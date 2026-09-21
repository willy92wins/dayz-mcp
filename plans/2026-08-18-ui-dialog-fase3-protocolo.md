# Protocolo de aceptación física — `ui_dialog` fase 3

Ejecutable en ~10 minutos. No re-empaquetar entre casos. No usar `ui_click` como
criterio de PASS (sí como aborto de un diálogo atascado).

Plan: `plans/2026-08-17-ui-dialog-plan-fusionado.md` §4 (fase 3) y §5 (12 casos +
alcanzabilidad). Contrato: `plans/2026-08-18-ui-dialog-contrato-v1.md`.
Changelog publicado: `product-spec.md` §«Changelog de contrato — 2026-08-18».

`[EXACT]` = leído en el árbol. `[DESIGN]` = derivado del `.layout` o del protocolo;
no sustituye el `ui_tree` de la pasada.

---

## 0. Por qué `ui_click` no basta

`ui_click` invoca el handler por `GetScript` / `GetUserData`
(`MCPClientBridge.c:1747-1783`) y **se salta** hit-testing, orden Z e
`IGNOREPOINTER`. El plan §5 lo dice explícito: no cuenta como aceptación. El
gate real es: abrir → leer `ui_tree` → comprobar flags y rectángulo → **pulsar
con el ratón en el centro**. Hubo un P1 real por solape `BtnNo`/`BtnCancel` en
confirm (un click en la zona común daba `cancelled` en vez de `no`); el layout
actual los separa (`mcp_dialog.layout:332-373`) y hay que **medirlo**, no
fiarse.

Lo ya verde por API (run `0716b585`, PBO `CD22655F`, HANDOFF «Pendientes
IN-GAME CERRADOS») se marca abajo. Aquí solo se aporta lo que el ratón prueba.

---

## 1. Precondiciones (no negociables)

El orquestador lanza / para DayZ. Este protocolo **no** spawnea ni mata
procesos. Si no hay run listo, parar y devolver la caja.

1. Run vivo: `dayz_test_run(project="LFPowerGrid", extra_mods=["@DayZ_MCP"])`
   `[EXACT]` receta de sesión 2026-08-17/18. `bridge_status.ready == true`.
2. **Pasada 1** a **1920×1080** (default de `dayz_test_run`,
   `dayz_test_tool.py:111-112,542-543`). Comprobarlo: `ui_tree(path="MCPDialogRoot")`
   en reposo → nodo root `screen_w=1920`, `screen_h=1080`
   `[EXACT]` medido en el gate: root oculto 1920×1080.
3. **Pasada 2** a **1280×720** (16:9; ya usada en los window-grab de este
   proyecto, `plans/2026-06-09-fase3-visual.md:26,45`). Cómo se cambia
   `[DESIGN]` operativo:
   - `dayz_test_stop(run_id)` del run actual.
   - Relanzar el **mismo** proyecto/mods con `width=1280`, `height=720`
     (`server.py:1843-1844`).
   - Esperar `ready`, volver a tomar lease, **dar foco** (paso 4).
   - No hay cambio de resolución en caliente.
4. **Ventana del cliente con FOCO.** Aviso medido:
   `fb-20260818-105928-158b` (frame congelado sin foco). Un click o una
   captura con el cliente en segundo plano no prueba el diálogo. Alt-tab al
   cliente DayZ **antes** de cada click. Preferir fullscreen / borderless;
   si va windowed, sumar el origen del client-area de Windows a
   `(cx, cy)` del engine.
5. Dos sesiones MCP sobre el mismo daemon (el broker lo permite):
   - **Sesión A** (lease): `session_acquire_wait` → lanza `ui_dialog` y espera.
     `ui_dialog` no está en `READ_ONLY_COMMANDS`
     (`session_coordination.py:18-38`).
   - **Sesión B** (sin lease): `ui_tree` — es lectura
     (`session_coordination.py:36`). No llames `ui_set_text` / `ui_click`
     desde B: mutan y no tienes lease.
6. El operador de ratón es un humano (o un agente con control de puntero OS).
   Las coordenadas de click son las de `ui_tree` de **esta** apertura, no la
   tabla `[DESIGN]`.
7. `timeout_s` de trabajo = **30** salvo el caso 10 (`5`). Presupuesto Python
   = `timeout_s + 10` (`ui_dialog.py:31,67-69`). No uses 60: no caben 10 min.

Si falta el lease, el foco o el root a 1920×1080 en la pasada 1: **STOP**, no
improvisar.

---

## 2. Cómo sacar el rectángulo y el centro

Con el diálogo **abierto** (Sesión A bloqueada en `ui_dialog`):

```
ui_tree(path="MCPDialogRoot")
```

`[EXACT]` `server.py:2645-2658`. El puente rellena `result.ui.nodes[]`
(`MCPClientBridge.c:1078-1081,1643-1676`; DTO `MCPUiNode` en
`MCPMessages.c:375-391`). Cada nodo trae:

`name`, `type`, `visible`, `visible_hierarchy`, `disabled`, `ignore_pointer`,
`screen_x`, `screen_y`, `screen_w`, `screen_h` (y `text` / `text_readable` si
el proto tiene getter).

Busca el botón por `name` (`BtnOk`, `BtnYes`, `BtnNo`, `BtnSubmit`, `BtnCancel`).

**Fórmula del centro** `[EXACT]` aritmética, aplicada a los `screen_*` reales:

```
cx = screen_x + screen_w / 2
cy = screen_y + screen_h / 2
```

Click izquierdo **una vez** en `(round(cx), round(cy))` del client-area.
Ya medido: root = 1920×1080 y `BtnOk.screen_x ≈ 850.5` (cierra con la tabla).

El root del layout nace con `ignorepointer 1` (`mcp_dialog.layout:4`). Eso **no**
exime el check de cada botón. Si el click al centro no dispara, es FAIL de
hit-testing (justamente lo que `ui_click` no ve).

---

## 3. Tabla de centros esperados a 1920×1080 `[DESIGN]`

Derivada de `gui/layouts/mcp_dialog.layout` (position/size relativos,
`hexactpos/vexactpos/hexactsize/vexactsize` = 0). Panel
`MCPDialogPanel` `size 0.38 0.70` + `halign/valign center_ref`
(`mcp_dialog.layout:13-22`).

```
pw = 0.38 * W          ph = 0.70 * H
px = (W - pw) / 2      py = (H - ph) / 2
x  = px + rx * pw      y  = py + ry * ph
w  = rw * pw           h  = rh * ph
```

A `W=1920 H=1080`: `pw=729.60 ph=756.00 px=595.20 py=162.00`.

| Widget | layout pos / size | screen_x | screen_y | screen_w | screen_h | cx | cy |
|---|---|---:|---:|---:|---:|---:|---:|
| `MCPDialogRoot` | `0 0` / `1 1` (`:5-6`) | 0.00 | 0.00 | 1920.00 | 1080.00 | — | — |
| `MCPDialogPanel` | `0.38×0.70` centro (`:17`) | 595.20 | 162.00 | 729.60 | 756.00 | — | — |
| `BtnOk` | `0.35 0.86` / `0.30 0.08` (`:307-308`) | 850.56 | 812.16 | 218.88 | 60.48 | 960.00 | 842.40 |
| `BtnYes` | `0.06 0.86` / `0.26 0.08` (`:321-322`) | 638.98 | 812.16 | 189.70 | 60.48 | 733.82 | 842.40 |
| `BtnNo` | `0.37 0.86` / `0.26 0.08` (`:335-336`) | 865.15 | 812.16 | 189.70 | 60.48 | 960.00 | 842.40 |
| `BtnSubmit` | `0.37 0.86` / `0.26 0.08` (`:349-350`) | 865.15 | 812.16 | 189.70 | 60.48 | 960.00 | 842.40 |
| `BtnCancel` | `0.68 0.86` / `0.26 0.08` (`:363-364`) | 1091.33 | 812.16 | 189.70 | 60.48 | 1186.18 | 842.40 |

`BtnOk.screen_x ≈ 850.5` ya se midió: encaja con 850.56. El resto **no** se ha
leído de `ui_tree` → sigue `[DESIGN]` hasta la casilla «ui_tree real» de la
plantilla §7.

`BtnNo` y `BtnSubmit` comparten rectángulo `[DESIGN]`. Nunca deben estar
`visible` a la vez (`ApplySpec`: acknowledge solo `BtnOk`; confirm
`BtnYes`+`BtnNo`+`BtnCancel`; form `BtnSubmit`+`BtnCancel` —
`MCPDialogController.c:545-582`).

### 1280×720 `[DESIGN]` (pasada 2)

`pw=486.40 ph=504.00 px=396.80 py=108.00`.

| Widget | screen_x | screen_y | screen_w | screen_h | cx | cy |
|---|---:|---:|---:|---:|---:|---:|
| `BtnOk` | 567.04 | 541.44 | 145.92 | 40.32 | 640.00 | 561.60 |
| `BtnYes` | 425.98 | 541.44 | 126.46 | 40.32 | 489.22 | 561.60 |
| `BtnNo` / `BtnSubmit` | 576.77 | 541.44 | 126.46 | 40.32 | 640.00 | 561.60 |
| `BtnCancel` | 727.55 | 541.44 | 126.46 | 40.32 | 790.78 | 561.60 |

---

## 4. Checklist de alcanzabilidad (por cada botón visible)

Copiado del plan §5. Un FAIL aquí tumba el caso aunque el handler exista.

Para el nodo del botón en `ui_tree`:

1. `visible == true`
2. `visible_hierarchy == true`
3. `disabled == false`
4. `ignore_pointer == false`
5. `screen_w > 0` y `screen_h > 0`
6. Rectángulo dentro de pantalla:
   `screen_x ≥ 0`, `screen_y ≥ 0`,
   `screen_x + screen_w ≤ W`, `screen_y + screen_h ≤ H`
   (`W×H` = 1920×1080 o 1280×720 de esta pasada; cruzar contra el root).
7. **Ningún solape** con otro botón **visible** del mismo `kind`:
   `ax < bx+bw AND bx < ax+aw AND ay < by+bh AND by < ay+ah`.
   El P1 era `BtnNo` ∩ `BtnCancel` en confirm.

Botones que **deben** estar visibles por `kind` `[EXACT]`
`MCPDialogController.c:545-582`:

| `kind` | visibles | ocultos |
|---|---|---|
| `acknowledge` | `BtnOk` | Yes/No/Submit/Cancel |
| `confirm` | `BtnYes`, `BtnNo`, `BtnCancel` | Ok/Submit |
| `form` | `BtnSubmit`, `BtnCancel` | Ok/Yes/No |

Haz el checklist **antes** del click. Anota `cx,cy` reales. Luego pulsa.

---

## 5. Dos sesiones, un click (plantilla de un caso)

1. Sesión A: llama `ui_dialog(...)` con los args de la fila (queda bloqueada).
2. Sesión B: `ui_tree(path="MCPDialogRoot")` → checklist §4 → anota centro real.
3. Foco al cliente. Ratón en `(round(cx), round(cy))`. Click izquierdo una vez.
4. Sesión A desbloquea. Compara el JSON público con la columna «debe devolver».
5. Si el click no pega: **no** uses `ui_click` para «arreglar» el PASS. Anota
   FAIL y, si hace falta abortar, `ui_click` desde A (tiene lease) o espera el
   timeout. Siguiente caso.

Rellenar un form: usa `default` en los args (centinela visible en `EditN` vía
`ui_tree`, `text_readable=true`). No hace falta `ui_set_text` (exigiría lease
y A está bloqueada). El operador puede teclear encima si el caso lo pide.

Cerrar una confirmación = click en **`BtnCancel`**. No hay handler de Escape/X
en `MCPDialogController.c` (solo `OnClick`, `:301-363`). Escape no es este
protocolo.

---

## 6. Los 12 casos — secuencia (pasada 1 = 1920×1080)

Centinelas **distintos por argumento** (plan §5 caso 2): si una clave no
declarada se pierde en silencio, la UI abierta no lo delata.

`timeout_s=30` salvo C07 (no abre) y C10 (`5`).

### C01 — whitelist / dispatch

- **Ya verde por API** (11/13 + busy): atraviesa sin `not_whitelisted` ni
  `unknown_command`.
- Aquí: el primer `ui_dialog` que abra UI (C02) lo reconfirma. Sin click propio.

### C02 — un centinela por argumento (abre form, no envía aún)

Sesión A:

```
ui_dialog(
  kind="form",
  title="S2-TITLE-q7m",
  message="S2-MSG-line1\nline2",
  fields=[
    {"id": "s2id_a", "label": "S2-LBL-A", "required": true,  "default": "S2-DEF-A"},
    {"id": "s2id_b", "label": "S2-LBL-B", "required": false, "default": "S2-DEF-B"}
  ],
  timeout_s=30
)
```

Sesión B `ui_tree`: Title/`text` no es legible en `TextWidget`
(`[EXACT]` proto sin getter; `ui_tree` reporta `text_readable=false`).
Comprueba lo que **sí** se lee:

- `Edit0.text == "S2-DEF-A"`, `Edit1.text == "S2-DEF-B"` (`text_readable=true`).
- `Row0`/`Row1` visibles; `Row2`..`Row5` no.
- Checklist §4 de `BtnSubmit` y `BtnCancel`.

Click físico en **`BtnSubmit`** (centro de Submit, no Cancel).

Debe devolver `[EXACT]` forma pública (`ui_dialog.py:345-365`):

```
ok true, state="completed",
values=[{"id":"s2id_a","value":"S2-DEF-A"},{"id":"s2id_b","value":"S2-DEF-B"}],
values_by_id={"s2id_a":"S2-DEF-A","s2id_b":"S2-DEF-B"}
```

sin `choice` ni `dismissed_by`. `title`/`message`/`timeout_s`/`kind` viajaron
si la UI se abrió con esas filas y el resultado casa. **Hit-testing de Submit.**

### C03 — acknowledge + OK

**Ya verde por API** (`ui_click(BtnOk)` → `completed/dismissed_by=ok`).
Aquí se prueba el hit-testing de `BtnOk`.

```
ui_dialog(kind="acknowledge", title="S3-ACK-OK", message="S3-MSG-ACK", timeout_s=30)
```

Checklist de **solo** `BtnOk`. Click centro `BtnOk`.

Debe: `ok true, state="completed", dismissed_by="ok"`. Sin `choice`/`values`.

### C04 — confirm yes y no (dos aperturas)

**Ya verde por API** (`choice=yes` / `choice=no` vía `ui_click`).
Aquí: hit-testing de `BtnYes` y `BtnNo` (y que no se pisan con Cancel).

**C04a**

```
ui_dialog(kind="confirm", title="S4A-YES", message="S4A-MSG-YES", timeout_s=30)
```

Checklist Yes/No/Cancel, **sin solape**. Click **`BtnYes`**.
Debe: `state="completed", choice="yes"`. Sin `dismissed_by`/`values`.

**C04b**

```
ui_dialog(kind="confirm", title="S4B-NO", message="S4B-MSG-NO", timeout_s=30)
```

Click **`BtnNo`** (centro ≈ 960,842 `[DESIGN]` — **no** es Cancel ≈ 1186).
Debe: `state="completed", choice="no"`.

### C05 — cerrar confirmación → `cancelled`, nunca «no»

**Ya verde por API** (cerrar confirm → `cancelled`). Aquí: hit-testing de
`BtnCancel` en confirm (el P1 vivía aquí).

```
ui_dialog(kind="confirm", title="S5-CANCEL", message="S5-MSG-CANCEL", timeout_s=30)
```

Click **`BtnCancel`**. Debe: `ok true, state="cancelled"`. **Prohibido**
`choice` (ni `"no"`). Si sale `choice="no"`, FAIL duro (plan §5.5 / §3.2 regla 1).

### C06 — form 1 campo y form N en orden

**Ya verde por API** (1 campo + unicode; 3 campos EN ORDEN + `values_by_id`).
Aquí: hit-testing de `BtnSubmit` en form 1 y form 3.

**C06a**

```
ui_dialog(
  kind="form", title="S6A-ONE", message="S6A-MSG",
  fields=[{"id": "alpha", "label": "S6A-LBL", "required": true, "default": "S6A-VAL"}],
  timeout_s=30
)
```

Click `BtnSubmit`. Debe: `values=[{"id":"alpha","value":"S6A-VAL"}]`,
`values_by_id.alpha=="S6A-VAL"`.

**C06b** — orden declarado ≠ alfabético a propósito:

```
ui_dialog(
  kind="form", title="S6B-THREE", message="S6B-MSG",
  fields=[
    {"id": "zeta",  "label": "S6B-L0", "required": true,  "default": "V0"},
    {"id": "mid",   "label": "S6B-L1", "required": false, "default": "V1"},
    {"id": "alpha", "label": "S6B-L2", "required": true,  "default": "V2"}
  ],
  timeout_s=30
)
```

Click `BtnSubmit`. Debe `values` **en este orden**: zeta, mid, alpha
(`ui_dialog.py:385-388`). `values_by_id` las tres claves.

### C07 — N+1 campos, sin abrir UI

**Ya verde por API** (`bad_args: fields has 7 items, max 6` en 0,0 s).
No hay hit-testing. Una sola sesión:

```
ui_dialog(
  kind="form", title="S7-SEVEN", message="S7-MSG",
  fields=[
    {"id": "f0", "label": "L0"}, {"id": "f1", "label": "L1"},
    {"id": "f2", "label": "L2"}, {"id": "f3", "label": "L3"},
    {"id": "f4", "label": "L4"}, {"id": "f5", "label": "L5"},
    {"id": "f6", "label": "L6"}
  ],
  timeout_s=30
)
```

Debe fallar **antes** de encolar: `ToolError` que empieza por
`bad_args: fields has 7 items, max 6` (`ui_dialog.py:126-127`).
Sesión B: `ui_tree` → root `visible=false` (o no hay botones visibles).
Si se abre UI, FAIL.

### C08 — obligatorio vacío: error visible, llamada pendiente

**Ya verde por API** (`Error` visible, llamada pendiente). Aquí: click físico
en Submit **no** debe cerrar.

```
ui_dialog(
  kind="form", title="S8-REQ", message="S8-MSG",
  fields=[{"id": "need", "label": "S8-LBL", "required": true, "default": ""}],
  timeout_s=30
)
```

Checklist Submit. Click `BtnSubmit`. **No** debe volver aún Sesión A.
Sesión B otra vez: widget `Error` `visible=true`,
`text == "Required field is empty"` (`MCPDialogController.c:717-730`;
`EditBox` sí es readable; `Error` es `TextWidget` → `text_readable=false`,
así que el oráculo del texto es visual / captura **con foco**).
Root sigue visible. Luego click `BtnCancel` para desbloquear A:
`state="cancelled"`, sin `values` (nada parcial, `ui_dialog.py:339-341`).

### C09 — el form siguiente nace limpio

**Ya verde por API.** Aquí: tras C08, abrir:

```
ui_dialog(
  kind="form", title="S9-CLEAN", message="S9-MSG",
  fields=[{"id": "fresh", "label": "S9-LBL", "required": true, "default": "S9-DEF"}],
  timeout_s=30
)
```

`ui_tree`: `Edit0.text == "S9-DEF"` (no el vacío de C08), `Error` no visible,
`Row1`..`Row5` ocultos, labels de C06/C08 no heredados. Click `BtnSubmit`.
Debe: `values=[{"id":"fresh","value":"S9-DEF"}]`.

### C10 — sin interactuar → `timed_out`

**Ya verde por API** (`timed_out` a 5,03 s, root oculto). No es hit-testing.
No pulsar nada.

```
ui_dialog(kind="acknowledge", title="S10-TO", message="S10-MSG", timeout_s=5)
```

Debe: `ok true, state="timed_out"` en ~5 s (no `ToolError` de transporte;
eso sería presupuesto 15 s). Después, B: root `visible=false`. Input
desbloqueado = el jugador puede andar; anótalo a ojo (no hay tool de
«input locked»).

### C11 — segundo diálogo → `rejected`/`busy`

**Ya verde por API ×3** (HANDOFF: 0,68 / 0,67 / 0,64 s,
`state="rejected", reason="busy"`; el primero sigue abierto hasta
`ui_click(BtnOk)` → `completed/ok`). **No se re-ejecuta aquí:** no es
hit-testing, y un segundo `ui_dialog` MCP exige lease que A ya tiene
bloqueado. Reabrir este caso es trabajo de script HTTP (`gate_regress.py`),
no de este protocolo.

### C12 — acentos, `ñ`, comillas, barras, saltos

**Ya verde por API** (`Guille ñ / \ "q"` de vuelta). Aquí: el Submit es
físico; el texto viaja en `default`.

```
ui_dialog(
  kind="form",
  title="S12-Ñ-título",
  message="S12-MSG «comillas» / barra\nsalto",
  fields=[{
    "id": "note",
    "label": "S12-LBL-ñ",
    "required": true,
    "default": "Guille ñ / \\ \"q\""
  }],
  timeout_s=30
)
```

`ui_tree` `Edit0.text` debe round-trip el default. Click `BtnSubmit`.
Debe: `values[0].value` idéntico al default (JSON válido, sin rotura).

---

## 6b. Pasada 2 — 1280×720 (~3 min)

Tras el relanzamiento §1.3 y el foco:

1. Un `ui_tree` de root: `screen_w=1280`, `screen_h=720`. Si no, STOP.
2. Rehacer **solo** el hit-testing (C03 `BtnOk`, C04a `BtnYes`, C04b `BtnNo`,
   C05 `BtnCancel`, C02 o C06a `BtnSubmit`) con checklist §4 y la tabla
   1280×720. No repitas C07/C10/C11.
3. Un solape a 1280 que no existía a 1920 es FAIL (escala relativa no
   garantiza gaps si el engine redondea distinto).

---

## 7. Qué se anota (plantilla)

Copia la tabla a un `.md` de sesión o al body del `pipeline_feedback`.
`ui_tree` real sustituye la columna `[DESIGN]`.

| Caso | Res | Botón | cx,cy ui_tree | §4 1-7 | Click | Resultado (state/choice/values/error) | PASS/FAIL | API previa |
|---|---|---|---|---|---|---|---|---|
| C01 | 1920 | — | — | — | — | abre sin unknown_command | | verde API |
| C02 | 1920 | BtnSubmit | | | físico | completed + values orden | | hit-test |
| C03 | 1920 | BtnOk | | | físico | completed / dismissed_by=ok | | verde API + hit-test |
| C04a | 1920 | BtnYes | | | físico | completed / yes | | verde API + hit-test |
| C04b | 1920 | BtnNo | | | físico | completed / no | | verde API + hit-test |
| C05 | 1920 | BtnCancel | | | físico | cancelled, sin choice | | verde API + hit-test |
| C06a | 1920 | BtnSubmit | | | físico | 1 value | | verde API + hit-test |
| C06b | 1920 | BtnSubmit | | | físico | 3 values en orden | | verde API + hit-test |
| C07 | 1920 | — | — | root oculto | no | `bad_args: fields has 7…` | | verde API |
| C08 | 1920 | BtnSubmit luego Cancel | | | físico | Error visible; luego cancelled | | verde API + hit-test |
| C09 | 1920 | BtnSubmit | | | físico | form limpio + completed | | verde API + hit-test |
| C10 | 1920 | — | — | root oculto al vencer | no | timed_out ~5 s | | verde API |
| C11 | — | — | — | — | no | no re-medir | skip | verde API ×3 |
| C12 | 1920 | BtnSubmit | | | físico | unicode intacto | | verde API + hit-test |
| C03'…C05' C02' | 1280 | (idénticos) | | | físico | igual que 1920 | | hit-test 2ª res |

Solapes (pasada 1 y 2): anota `yes∩no`, `no∩cancel`, `submit∩cancel` = ninguno.

---

## 8. Cómo se archiva

Al terminar (PASS o FAIL), **un** ítem:

```
pipeline_feedback(
  kind="finding",
  title="ui_dialog fase 3 aceptación física AAAA-MM-DD",
  body="<tabla §7 + W×H reales + PBO sha si se conoce + FAIL list>",
  project="DayZ_MCP"
)
```

`[EXACT]` `server.py:2787-2814`, `inbox.py:13,43-76` (`kind` incluye
`finding`; title ≤ 120, body ≤ 8000). Funciona con el juego caído.

La nota de sesión (`AI/30_Sessions/…`) la escribe el orquestador al cierre;
este protocolo no toca `HANDOFF.md`.

---

## 9. PASS / FAIL de la fase 3

**PASS** si y solo si:

- Pasada 1 (1920×1080) y pasada 2 (1280×720) hechas (C11 skip explícito).
- Cada botón visible de cada `kind` pasó §4 (incluido **cero solapes**).
- Cada caso con click físico devolvió exactamente la columna «debe».
- C05 / cierre nunca trajo `choice`.
- C07 no abrió UI.
- C10 = `timed_out` con `ok` true, no `ToolError` de transporte.
- C06b conserva el orden declarado.
- Cero `ui_click` usados para reivindicar un PASS.

**FAIL** = cualquier casilla roja. Acumular **todos** los fallos de las dos
pasadas. Luego **una sola** candidata de PBO (DZ-R5; plan fusionado `:219-220`).
No empaquetar por bug. No tocar Enforce desde esta lane.

Si el click no pega y `ui_tree` decía alcanzable: sospecha
`IGNOREPOINTER` en un padre, orden Z, o coordenadas OS ≠ engine (foco /
DPI / windowed). Eso es evidencia de fase 3, no un «reintenta con
`ui_click`».
