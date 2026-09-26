# Gates: L6 procedencia
Consumidor: Claude, mediante unittest por nombres. No necesita otro arnes.
PLAN: brief L6, T1/T2/T3. MEDIDA: host_config.py:337-407; control_client.py:148-155.
Presupuesto: una implementacion y hasta dos correcciones ante repro ejecutable; nunca ronda 4.
VERDE: T1 reproduce rojo por comparacion de archivo durante resolucion y verde semantico; T2 rechaza cambios reales antes/despues; T3 distingue causas sin enviar HTTP.
Lista cerrada: rewrite entre llamadas; rewrite entre lectura/reopen; command/keyfile/timeout/argv; entrada ausente o malformada; reparse; errno/token/mensaje sensible; cero HTTP rechazado.
Regresion: modulos importadores enumerados individualmente, con excepciones del brief y expectativas obsoletas declaradas. Sin daemon de produccion, juego, suite completa, commit ni resellado.
