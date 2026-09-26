# Calibración adversarial — mismo lote, misma sesión

Esta es la segunda pasada obligatoria en la MISMA sesión. Usa el contrato, el grupo, los cinco
planes y tu primera respuesta ya presentes en contexto. No abras ningún prompt ni byte de
`reviews/**` desde disco y no listes directorios. Mantén todas las demás fronteras de contexto del
primer turno.

Busca activamente tanto falsos PASS como falsos REVISE: intenta refutar cada veredicto, reabre las
citas decisivas y comprueba que OWNS/dependencias cierran el grafo completo. Ataca especialmente
oráculos autocontenidos, expected derivado del mismo productor, snapshots copiados al candidato,
run/role/authority inventados, y cierres por evidencia que sólo prueben entrega en vez de efecto.

Reemite los cinco registros completos contra los mismos hashes. Devuelve exactamente un objeto
JSON válido, sin fences ni prosa exterior, con el mismo schema del primer turno y un campo superior
adicional `"calibration_result":"CONFIRMED"`. Si no puedes completar la calibración, usa
`"calibration_result":"INCONCLUSIVE"` y ningún grupo puede quedar PASS. La segunda salida
sustituye íntegramente la primera; no entregues un delta.
