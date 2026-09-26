# Revisión adversarial: dos entregables de otra familia de modelos

Eres el revisor independiente. Otro modelo produjo estos dos entregables y **no** ha
participado en escribir este encargo. Tu trabajo es intentar demostrar que están mal.

**No escribas ningún fichero y no ejecutes ningún comando de shell.** Todo tu resultado
va en tu respuesta de texto. Lee los ficheros y responde.

## Qué revisas

### A) El plan sobre una auditoría
- `reviews/audit-plan-2026-09-08/QA-TRIAJE.md`
- `reviews/audit-plan-2026-09-08/PLAN.md`
- `reviews/audit-plan-2026-09-08/STATE.md`

Contexto: una auditoría del proyecto (`AUDITORIA_MCP_2026-09-07.md`, 350 líneas) fue
triajada contra el árbol vivo en `reviews/audit-triage-2026-09-08/REPORT.md`, con este
recuento: VIVO 60, YA ARREGLADO 2, MOVIDO 3, FALSO 19, NO VERIFICABLE 19. Después, el
autor del plan hizo un control de calidad de ese triaje (dice 12/12 de acuerdo) y convirtió
los 60 VIVOS en **2 HACER, 4 DECIDIR, 16 NO HACER**.

Lo que quiero que ataques, en este orden:
1. **¿16 NO HACER de 60 es un descarte honesto o una racionalización?** Coge al menos
   cuatro de los NO HACER y comprueba en el código si el motivo del descarte se sostiene.
   Un "no compensa" sin coste medido es una opinión, no un veredicto.
2. **¿El control de calidad 12/12 vale?** La muestra la eligió el propio autor. Mira si es
   una muestra que podía fallar. Repite tú al menos tres de esas verificaciones abriendo el
   `path:line` y di si coincides.
3. **¿Hay algún hallazgo marcado FALSO en el triaje que en realidad sea VIVO?** Ese es el
   error caro: entierra un defecto real. Comprueba al menos tres.
4. **¿Los 2 HACER son de verdad lo más importante de los 60?** Si crees que hay un VIVO
   descartado que debería estar arriba, nómbralo con su cita.

### B) El veredicto sobre un conflicto de compartición
- `reviews/shareconflict-2026-09-08/VERDICT.md`
- `reviews/shareconflict-2026-09-08/STATE.md`
- el código: `tools/dayz_mcp/host_config.py`, `tools/dayz_mcp/control_client.py`
- los tests: `tools/tests/test_provenance_gate.py` y cualquier `test_shareconflict_*`

Contexto: un gate de acreditación rechazaba clientes sanos. Se arregló comparando la
registración MCP parseada en vez de los bytes del fichero. Quedó viva la hipótesis de que
el incidente real lo causara un conflicto de compartición al abrir el fichero, no la
deriva de bytes. Esta lane dice haberlo demostrado como mecanismo (24 combinaciones
nativas, `winerror=32`) pero **no** haberlo atribuido al incidente histórico.

Lo que quiero que ataques:
1. **¿Ha abierto alguna vía por la que un cliente NO acreditado pase?** Esto pesa más que
   todo lo demás. Lee el diff de esos dos ficheros con ojo de atacante. Busca en concreto:
   ¿se ha relajado algo con la excusa de "es solo diagnóstico"? ¿el motivo publicado filtra
   rutas del host o algo aprovechable? ¿queda algún camino donde una excepción se traduzca
   en aceptación en vez de en rechazo?
2. **¿La matriz de 24 combinaciones prueba lo que dice probar?** ¿O hay combinaciones que
   faltan y que cambiarían la conclusión?
3. **¿La distinción "mecanismo demostrado / atribución no demostrada" es honesta**, o es
   una forma elegante de vender como resultado algo que no lo es?
4. **¿Los controles negativos siguen vivos?** En particular uno llamado T2, que debe
   comprobar que una registración CAMBIADA sigue siendo RECHAZADA. Si ese test se ha
   debilitado, es lo más grave que puedes encontrar.

## Formato de tu respuesta

Para cada hallazgo, exactamente esto:

```
[A o B][número] SEVERIDAD: critico | alto | medio | bajo
QUÉ: una frase.
DÓNDE: fichero:línea que TÚ has abierto.
POR QUÉ: qué se rompe o qué se ha ocultado.
CÓMO SE COMPRUEBA: el paso concreto que lo confirmaría.
```

Y al final, obligatorio y sin excepción, estas dos secciones:

**QUÉ PUEDE ESTAR MAL EN LA PREMISA DE ESTE ENCARGO.** No la dejes vacía y no escribas
"nada". Piensa contra el encargo mismo: ¿te estoy haciendo mirar donde no está el problema?
¿doy por bueno algo que no lo es? ¿la pregunta correcta es otra?

**LO QUE NO PUDE VERIFICAR**, línea a línea con su motivo. Si el motivo es una restricción
de este encargo, dilo: puede ser un error mío y quiero saberlo.

Si no encuentras nada grave, dilo claramente. Un revisor que siempre encuentra algo no
sirve de control. Pero un "todo bien" sin haber abierto ficheros tampoco: cita lo que leíste.
