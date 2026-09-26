Gate Grok focal nuevo, estrictamente de solo lectura. No edites, escribas, uses MCP/red ni lances
DayZ. Revisa contra los bytes vivos, `product-spec.md` y el DAG:

- `fb-20260829-104543-47c9` | `plans/inbox-20260830/15-fb-20260829-104543-47c9.md` | `47eb55cda3a4bdb5c01e70d528b21b695a9870485034d8ad5faf313b06100448`

La entrada debe coincidir con `plans/inbox-20260830/plan-manifest.sha256` (snapshot del manifiesto:
`24c9b68f70001f0f65243e315c0d23b6931c842fd82cbf3a533763b8a1a88b78`). El hallazgo previo era
que la ficha reclamaba M06/`loopback.py` aunque el DAG sólo le asigna M05. Comprueba que ahora M06
es dependencia consumida, no bytes poseídos, y que el cambio sigue cerrando B4 sin rama especial
ScriptView.

Valida también `[EXACT]`, DPF/Intent, compatibilidad, fail-closed, OWNS/dependencias y criterios no
tautológicos. Devuelve un bloque `ID/PLAN/SHA256/VERDICT/FINDINGS/WHY` y `GROUP_VERDICT`; `PASS`
requiere `FINDINGS: - NONE`. Primera pasada; habrá calibración en la misma sesión.

