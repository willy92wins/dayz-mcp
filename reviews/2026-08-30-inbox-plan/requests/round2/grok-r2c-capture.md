Gate Grok focal nuevo y de solo lectura. No edites, no escribas, no uses MCP/red ni lances DayZ.
Revisa individualmente este hash contra los bytes reales, `product-spec.md` y
`plans/inbox-20260830/00-execution-dag.md`:

- `fb-20260828-224835-268a` | `plans/inbox-20260830/03-fb-20260828-224835-268a.md` | `23c2755ba3ec1a790bd49cf7a5c38fab8b3b5142549a73e11eda1ba5428b9606`

El manifiesto vigente debe tener SHA-256
`d35f71d2392bb16cb7cc24556ebcc0cc243de18e47705d2b7fb28fc8ce8f469a`. El hallazgo previo fue
usar `x/y` en vez de los campos wire existentes `left/top`. Verifica contra
`tools/mcp-grab.ps1` y `tools/mcp_capture.py` que ahora están correctos tipos, límites, contención,
orden antes de downscale y fail-closed D3, sin aliases ficticios.

Aplica además `[EXACT]`, DPF/Intent, compatibilidad, OWNS/DAG y criterios no tautológicos. Devuelve
exactamente `ID/PLAN/SHA256/VERDICT/FINDINGS/WHY` y `GROUP_VERDICT`; `PASS` requiere
`FINDINGS: - NONE`. Esta es la primera pasada; habrá calibración en la misma sesión.

