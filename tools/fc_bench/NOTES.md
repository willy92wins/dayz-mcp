# fc_bench (ticket C6, fb-20261028-131118-19f6)

Axis-C gate: 12 scenarios, scripted daemon, catalog passed with `--catalog`.
Run it against the endpoint the caller gives. A change on axis C does not
close without a passing run. This tree does not start a model.

Seed found outside the repo at
`C:\Users\guill\dzmcp_gauntlet\council-live-20261028\bench\`
(`fc_bench.py`, `scenarios.json`, four catalog snapshots). The snapshots
were not copied. `catalog_admin.json` here is a compact stand-in with the
same 22 tool names. Point `--catalog` at a real `list_tools` dump to score
one.

## Supuestos de trabajo (no confirmados por Guillermo)

- D1: el mundo empieza con `godmode: false` en los dos jugadores. El bench
  no enciende godmode ni el refill. No cambia el PBO.
- D4: lecturas y avisos no piden confirmación; una mutación que el modelo
  dispara se aplica en el simulador para que pueda releerla. La confirmación
  humana de mutaciones reales queda fuera de este ticket. Este bench es la
  puerta que el eje C tiene que pasar antes de fiarse de esas mutaciones.
- D7: no lo implementa este ticket. El tope de 5000 tokens del catálogo
  admin es C1. Cada run guarda `prompt_tokens` en `runs.jsonl`.

`packaged-modules.lock.json` no cambia: no se toca `server.py` ni un módulo
sellado.

## Qué mira una persona

- `python -m unittest tests.test_fc_bench` desde `tools/` (el simulador, S10, S11 y el registro).
- Una pasada real: `python tools/fc_bench/fc_bench.py --base-url URL --model NOMBRE --catalog tools/fc_bench/catalog_admin.json --scenarios tools/fc_bench/scenarios.json --out <dir>`.
- En `<dir>/runs.jsonl`, una línea por escenario con `score`, `calls`, `answer` y `world_after`.
