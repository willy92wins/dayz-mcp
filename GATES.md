# Gates — cierre integral del buzón DayZ MCP (2026-08-30)

Este ledger raíz gobierna la cadena completa. Las 35 entradas conservan identidad en
`gates/inbox-*.md`; ninguna hoja puede cerrarse por cardinalidad ni por el PASS de otra. El
contrato base contiene cinco gates por entrada y ocho gates raíz: 183. La autoridad sucesora
no-S15 añade cinco gates en `gates/non-s15-successor.md`: 188 en total tras promoción. La
autoridad sucesora S15 añade siete más en `gates/s15-successor.md`: 195 en total tras promoción.
Los 70 registros Sonnet retirados se preservan en
`reports/2026-08-31-sonnet-retirement-manifest.md`.

```gates
[ ] ROOT-RESEARCH: Fase 0 sha256:453f84bff71103fa359841fb0d49a7cfbbd9e1541bb95108999e5f62c4f1d895 citada y copiada a memoria durable
  EVIDENCE: research/2026-08-30-pipeline-inbox-triage-codex.md y C:/Users/guill/ObsidianVault/AI/10_Projects/DayZ_MCP/research/2026-08-30-pipeline-inbox-triage-codex.md son byte-idénticos sha256:453f84bff71103fa359841fb0d49a7cfbbd9e1541bb95108999e5f62c4f1d895

[ ] ROOT-DPF: enmienda DPF aprobada ligada a product-spec sha256:ccd509199e1faa5452e0d70ce1da8fa5e3ba2cab2b2b59c94ac2c44e8bb288f4
  EVIDENCE: product-spec.md sha256:ccd509199e1faa5452e0d70ce1da8fa5e3ba2cab2b2b59c94ac2c44e8bb288f4

[ ] ROOT-PLANS: 35 planes ligados a plan-manifest sha256:ce42f14db8a7d7985d17e12f77a7cb8cfdb29bdc7c49ce197cf8b544cbbe3d0b y grok-plan-review-manifest sha256:36fe605a916c59f60502c410655365be4d37a2f7034b8cf186c1176762000cb2
  EVIDENCE: plans/inbox-20260830/plan-manifest.sha256 sha256:ce42f14db8a7d7985d17e12f77a7cb8cfdb29bdc7c49ce197cf8b544cbbe3d0b; reviews/2026-08-31-inbox-plan-grok-v1/grok-plan-review-manifest.json sha256:36fe605a916c59f60502c410655365be4d37a2f7034b8cf186c1176762000cb2

[ ] ROOT-IMPLEMENTATION: cada feedback aprobado obtuvo implementación o cierre por evidencia
  EVIDENCE: pending

[ ] ROOT-REVIEWS: cada implementación obtuvo revisión única Grok 4.6 Medium mediante Cursor sin hallazgos abiertos
  EVIDENCE: pending

[ ] ROOT-INBOX: el buzón no conserva ninguna de las 35 entradas sin resolución durable
  EVIDENCE: pending

[ ] ROOT-VERIFY: suites, gates no tautológicos y hashes finales pasan sobre los bytes entregados
  EVIDENCE: pending

[ ] ROOT-CLOSE: session_status final, memoria durable y handoff documentan un cierre limpio
  EVIDENCE: pending
```


## Canonical Python test invocation

Run the suite through `tools/run-tests.ps1`. The script selects this checkout's
`tools/.venv-mcp/Scripts/python.exe`, sets the working directory to `tools/` and
sets `PYTHONPATH` to the repository root. It restores the caller's working
directory and `PYTHONPATH`, prints the executed test count and exit code, and
returns that exit code. No environment activation or dependency installation is needed.

From the repository root (full suite):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./tools/run-tests.ps1
```

Single module (also accepts the short name `test_lote2_t2_steam`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./tools/run-tests.ps1 tests.test_lote2_t2_steam
```

From `tools/`, use `-File ./run-tests.ps1`; from another directory, use the
script's absolute path. The underlying unittest arguments are
`discover -s tests -t . -v`, or `<module> -v` when a module is supplied.
A failing import is a failure, and an empty suite returns exit code 5.
Compare test identities as well as counts on the same source revision.
Run the full suite serially; concurrent lanes must pass an explicit module.

The focused import gate runs only `tests.test_lote2_t2_steam` and
`tests.test_steam_preflight` from `tools/`, once without `PYTHONPATH` and once
with the repository root in `PYTHONPATH`. Both runs must return 0 and execute
the same nonempty set of tests. Its reproducible command from the repository root is:

```powershell
./tools/.venv-mcp/Scripts/python.exe -B ./reviews/suite-invocation-2026-09-08/verify-gate.py
```

Evidence: `reviews/suite-invocation-2026-09-08/gate.log` and `STATE.md`.
