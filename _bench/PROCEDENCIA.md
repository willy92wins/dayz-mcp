# Banco de regresion v8 - 37 casos, y de donde sale cada uno

30 en `must_reject\` + 5 en `must_accept\` + 1 en
`accepted_by_declared_limit\` + el modulo real
(`tools\dayz_mcp\effective_schema.py`, juzgado con
`python -I -S -B _gate.py` sin argumentos de candidato).

**Ninguno de los `.py` de casos se toca.** Son el criterio de aceptacion. Un banco que
ajusta el implementador no mide nada. `run_bench.py` solo descubre, ejecuta y compara los
veredictos; falla si los recuentos no son exactamente 30 + 5 + 1, mas el modulo real.

## Como correrlo: el interprete no es cualquiera

    <venv>\Scripts\python.exe -I -S -B _gate.py                 # el modulo real
    <venv>\Scripts\python.exe -I -S -B _gate.py --candidate X    # un candidato
    <venv>\Scripts\python.exe -I -S -B _bench\run_bench.py       # los 37 casos
    <venv>\Scripts\python.exe -I -S -B _bench\coverage.py        # el inventario de cobertura

Los tres flags son **obligatorios** y sin ellos el gate falla cerrado con
`S7-GATE-ENVIRONMENT: unsafe judge startup`. `-S` es el que impide que un candidato
plante un `.pth` en un site-packages escribible y el juez lo importe al arrancar.

★ **Consecuencia que hay que tener en cuenta al montar el entorno:** `-I` implica `-s`, o
sea que el hijo **NO ve el user site-packages**. Un `pip install --user` no le sirve. Las
dependencias tienen que estar en el site-packages del propio interprete:

    python -m venv .gate-venv
    .gate-venv\Scripts\python.exe -m pip install -r tools\requirements-mcp.txt pytest

Si el hijo no las alcanza, el gate lo dice como `S7-GATE-ENVIRONMENT` y **no** lo confunde
con un defecto del candidato: un fallo de arranque del entorno no se cobra del que se juzga.

## `must_reject\` - 30

| fichero | origen | por que debe salir ROJO |
|---|---|---|
| `c2_toy.py` | Codex, v3 | cadenas enlatadas, nunca llama a `build_app` |
| `c3_snapshot_dispatch.py` | Codex, v3 | snapshot JSON estatico + despacho por fixture |
| `c4_constant.py` | Codex, v3 | forma correcta, contenido constante |
| `c5_resolver_only.py` | Codex, v3 | resuelve bien, audita mal |
| `H1_canned_auditor.py` | receptor, v3 (privado) | resolutor real, auditor con literal congelado; sobre el arbol real daba los mismos hallazgos que el modulo bueno |
| `H2_over_reporter.py` | receptor, v3 (privado) | denuncia todo nombre compartido por dos o mas tools |
| `H3_dead_types.py` | receptor, v3 (privado) | nombres y alias correctos, pero tipos, enum y default muertos |
| `P1_systemexit.py` | receptor, v3 | `sys.exit(0)` durante la resolucion |
| `P2_osexit.py` | receptor, v3 | `os._exit(0)`: exit 0 sin linea de veredicto |
| `steal_oracle.py` | Grok, v3 (P1) | lee `expected` del frame de `evaluate_candidate` |
| `forge_counter.py` | Grok, v3 (P1) | falsifica el contador `calls` por PEP 667 |
| `monkeypatch_judge.py` | Grok, v3 (P1) | sustituye `_first_difference` y `_validate_findings` tras los autocontroles |
| `exit_ok.py` | Grok, v3 (P1) | imprime `S7-GATE-OK` y sale con `os._exit(0)` |
| `steal_bootstrap.py` | Grok, v4 (P1) | el worker era el gate y exponia `_expected_registry` y `tools` |
| `steal_channel.py` | Grok, v4 (P1) | escribia el `result.json` cuya ruta viajaba en `argv` |
| `N2_stdout_forgery.py` | receptor, v4 (privado) | imprime una corrida falsa terminada en `S7-GATE-OK` por stdout y stderr |
| `N1_incomplete_auditor.py` | receptor, v4 (privado, accidental) | copia real incompleta: omite media auditoria aunque coincide sobre el arbol real |
| `steal_reference.py` | receptor, v6 (P1) | carga y delega en `effective_schema.py` desde la copia completa del runtime v5 |
| `steal_gatefile.py` | receptor, v6 (P1) | carga `_gate.py` desde `cwd.parent`, usa `_expected_registry` y delega en la referencia copiada |
| `steal_channel_v5.py` | receptor, v6 (P1) | usa la referencia copiada para forjar una trama `S7R2` correcta y el token antes de `os._exit(0)` |
| `N3_frame_input.py` | receptor, v6 | convierte de verdad, pero obtiene `tools` del frame del worker por una via inexistente en produccion y nunca llama a `build_app` |
| `steal_expected.py` | Grok, v7 (G6-02) | roba del padre una sola familia y sus respuestas; no transforma la segunda familia independiente ni funciona fuera del juez |
| `steal_venv.py` | Grok, v7 (G6-03) | planta un `.pth` para parchear la siguiente invocacion; el juez v7 arranca con `-I -S -B` y nunca ejecuta lineas `.pth` |
| `falsy_default.py` | atacante, v8 (G7-01) | colapsa defaults falsy a `None`; los fixtures v8 emiten `0`, `0.0`, `False` y `""` |
| `missing_required.py` | atacante, v8 (G7-02) | exige que exista la clave `required`; los fixtures v8 incluyen tools que la omiten |
| `narrow_union.py` | atacante, v8 (G7-03) | ignora ramas de union con `items` o `additionalProperties`; los fixtures v8 emiten ambas |
| `pipe_no_strip.py` | atacante, v8 (G7-04) | separa pipes sin quitar espacios; los casos v8 incluyen una lista coherente con espacios |
| `assumed_boolean_enum.py` | atacante, v8 (G7-05) | inventa `[True, False]` para booleanos sin `enum`; los fixtures v8 incluyen esa forma |
| `auditor_first_param.py` | atacante, v8 (G7-06) | liga todos los pipes al primer parametro; los casos v8 incluyen dos params y dos grupos |
| `pipe_after_name.py` | atacante, v8 (G7-07) | solo liga pipes si el nombre aparece antes; los casos v8 ponen la lista antes del nombre |

`N1_incomplete_auditor.py` es un control especialmente importante: la copia perdio
`_audit_desc_enum_mismatch`. El arbol real no delata la omision, pero el fixture sintetico
`runtime-enum-mismatch` si. El control lento pero completo es `N1b_slow_faithful.py`.

## `must_accept\` - 5, mas el modulo real

| fichero | origen | por que debe salir VERDE |
|---|---|---|
| `legit_alt.py` | Grok, v3 | implementacion alternativa legitima |
| `legit_alt2.py` | Grok, v4 | segunda alternativa legitima, con otra estrategia |
| `N1b_slow_faithful.py` | receptor, v4 (privado) | modulo real integro con 6 s de retraso; controla que el timeout no sea demasiado corto |
| `legit_alt3.py` | receptor, v6 | tercera implementacion legitima, con ensamblador iterativo y auditorias independientes |
| `legit_alt4.py` | Grok, v7 | cuarta implementacion legitima, con visitante por tabla y auditorias independientes |

El sexto aceptado por merito es `tools\dayz_mcp\effective_schema.py`, la implementacion real.
Un gate que rechaza cualquiera de estos seis ha cambiado un agujero por un gate inutil.

## `accepted_by_declared_limit\` - 1

| fichero | origen | por que sale VERDE |
|---|---|---|
| `steal_parent.py` | Grok, v7 (G6-01) | carga la implementacion real del arbol del juez y delega; hace el trabajo, aunque su procedencia sea plagio |

Este tercer grupo no concede merito. Documenta el limite deliberado: un gate conductual puede
decidir si el candidato hace el trabajo, pero no si lo hizo el. Con el mismo usuario y sistema
de ficheros, la procedencia no es observable sin aislamiento de sistema operativo. Por eso
`steal_parent.py` debe seguir verde y la salida del gate declara el limite junto al veredicto.
