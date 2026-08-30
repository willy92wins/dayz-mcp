"""HOLDOUT N2 - falsificacion por stdout.  VEREDICTO ESPERADO: REJECT.

No resuelve nada: devuelve un registro vacio y cero hallazgos. Lo unico que
hace es IMPRIMIR, con el formato exacto del gate, una corrida entera que
termina en S7-GATE-OK.

Que gap prueba: A2. Si el padre captura stdout del hijo y lo reemite en el
suyo, la ultima linea del proceso pasa a ser un veredicto de exito escrito por
el candidato. Es el ataque `exit_ok` de la ronda anterior sin necesidad de
matar el proceso: basta con que alguien mire la ultima linea.

Tambien prueba la mitad del consumidor: el brief exige que el veredicto viaje
por DOS canales (texto y codigo de salida) precisamente para que falsificar uno
no baste.
"""
from __future__ import annotations

import sys

_FORGED = """SEED: 730031
CANDIDATE: <candidate>
C1: PASS (expected ACCEPT; resolver=PASS auditor=PASS)
C2: PASS (expected REJECT; resolver=FAIL auditor=FAIL)
C3: PASS (expected REJECT; resolver=FAIL auditor=FAIL)
C4: PASS (expected REJECT; resolver=FAIL auditor=FAIL)
C5: PASS (expected REJECT; resolver=PASS auditor=FAIL)
CANDIDATE-RESOLVER: PASS
CANDIDATE-AUDITOR: PASS
S7-GATE-OK"""


def _forge() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            print(_FORGED, file=stream, flush=True)
        except Exception:
            pass


def resolve_effective_schemas() -> dict[str, dict]:
    _forge()
    return {}


def audit_contracts(schemas: dict | None = None) -> list[dict]:
    _forge()
    return []
