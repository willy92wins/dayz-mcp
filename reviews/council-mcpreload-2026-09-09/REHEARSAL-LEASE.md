# Ensayo del lease — 34/34, y una modalidad que queda fuera del problema

Ultimo paso de diseno antes de implementar el supervisor. El spike del 2026-09-09 probo la
FORMA del reciclo y dejo escrito que no tocaba ni identidad ni lease; esto cubre justo eso.
**Ejecutado el 2026-09-10.** Sin daemon, sin DayZ, sin lease pedido contra la caja
compartida: nada mutado.

Codigo en [rehearsal/rehearse_lease.py](rehearsal/rehearse_lease.py). Se corre con el
interprete de `tools/.venv-mcp`.

**Que es real y que es doble.** Real: `SessionCoordinator`, la clase de produccion que el
daemon instancia en `daemon.py:464`, y `ClientIdentity`. Dobles: solo el reloj (para que el
TTL sea aritmetica y no espera), los generadores de token/id y los sumideros de audit y
cleanup — el mismo reparto que usan los tests de produccion
(`tools/tests/test_session_coordination.py:180`). Los 34 veredictos los emite el coordinador.

    VEREDICTO: PASA. 34 comprobaciones, todas verdes.

## Lo que queda probado

1. **El defecto es real, con cita.** La identidad se acuna por PROCESO en `server.py:1210`
   con `os.getpid()`, `os.getppid()`, `datetime.now(utc)` y `uuid4()`: cuatro de los seis
   campos cambian en un trabajador recien nacido. La validacion compara por VALOR el
   dataclass entero (`session_coordination.py:3433`), asi que un reciclo ingenuo pierde el
   lease con `lease_invalid` — y el lease sigue vivo, solo se ha vuelto inalcanzable.
2. **Congelar la identidad ENTERA funciona.** El trabajador nuevo muta, late, se ve dueno en
   el `status` y suelta limpio, y es **el mismo `lease_id`**, no uno nuevo.
3. **Y funciona con la identidad RECONSTRUIDA, que es lo que de verdad pasara.** Los primeros
   pasos comparaban el mismo objeto de Python — `w0 == w0` es una tautologia. El grupo F
   serializa la identidad a JSON, la reconstruye con `from_payload` y comprueba que **con esa**
   el token sigue valiendo. Sin ese grupo, el ensayo se habria aprobado a si mismo.
4. **No abre una llave maestra.** Un ajeno con el token robado es denegado; acertar el
   `session_id` tampoco basta (la igualdad es de los seis campos); y un token ya soltado no
   se puede reproducir.
5. **Congelar no hace mentir a nada mas.** `client.pid` es carga util en **un solo sitio de
   todo el paquete**: la clave de orden de los tombstones (`session_coordination.py:3072`).
   Nada lo usa como asa de un proceso vivo. Eso es lo que hace seguro el congelado.

## El congelado parcial no es insuficiente: es una trampa

Llevarse solo el `session_id` — la solucion que parece razonable— deja al trabajador nuevo
**sin ninguna salida**:

| intento | resultado |
|---|---|
| usar el lease | `lease_invalid` |
| pedir uno nuevo | **403 `identity_mismatch`** — el guardia de colision de `session_coordination.py:2766` |
| soltar el que quedo colgado | tampoco |

Es peor que no congelar nada: un reciclo ingenuo al menos puede pedir un lease nuevo. El
parcial se queda mirando la caja hasta que caduca el TTL. Nadie lo habia nombrado.

## El presupuesto, con numeros

Durante el reciclo **no late nadie**: el latido vive en el proceso del trabajador
(`lease_supervisor.py:12`, cadencia 45 s sobre un TTL de 120 s). Asi que el reciclo corre
contra el reloj del lease.

- Latir **justo antes** de matar al viejo deja el TTL entero. Contra los **2,24 s** que midio
  el spike con una llamada de 2 s en vuelo, el margen es **x54**.
- Pasarse del TTL tiene consecuencia medida: un rival que sondea **se lleva la caja**, y la
  identidad congelada vuelve a un lease que ya no es suyo.
- Y de paso: **la cola corre en el mismo reloj**. Un rival que se calla los 120 s pierde
  tambien su ticket (`ticket_invalid`). Un sobrepaso no solo entrega la caja: puede vaciar
  la cola.

## Lo que el ensayo encontro y no buscaba

**La modalidad `embedded` queda fuera de este problema, y tiene otro.** Es el default pelado
(`server.py:861`) y su `LoopbackServer` se construye **sin `coordination=`**
(`server.py:919`, `loopback.py:881`), asi que ahi no hay coordinador ni lease que preservar.
Pero enlaza el puerto del loopback **en su propio proceso** (`server.py:5423`): dos
trabajadores no pueden convivir, y el solape-y-drena del spike **no vale tal cual** en esa
modalidad. El lease de este ensayo aplica exactamente a la modalidad cliente/daemon — que es
la que corre esta sesion.

**`_release_and_exit` no suelta el lease.** El nombre lo sugiere y estuve a punto de
publicarlo como riesgo; el cuerpo (`server.py:5404`) solo hace `stop_loopback()` y
`os._exit(0)`. El vigia de muerte-del-padre que lo invoca sigue siendo relevante por otra
razon: bajo un supervisor, el padre del trabajador pasa a ser el supervisor.

## Requisitos que esto le pasa a la implementacion

1. **El portador lleva dos cosas, y son dos secretos distintos**: los seis campos de la
   identidad y el `lease_token`, que es `secrets.token_urlsafe(32)`
   (`session_coordination.py:206`) y **no viaja dentro de la identidad**. Por eso el token no
   puede ir por `argv`: la lista de procesos de esta caja la ven varias sesiones.
2. **Re-tipar al llegar.** Una variable de entorno entrega texto y `from_payload` rechaza un
   `pid` en `str` (`session_coordination.py:79`). Falla cerrado, que es lo correcto, pero
   significa que el portador convierte, no pega.
3. **Latir inmediatamente antes de matar al viejo**, y poner al reciclo un plazo duro muy por
   debajo del TTL. Con x54 de margen sobra, pero el plazo tiene que existir.
4. **El trabajador nuevo revalida antes de su primera mutacion.** Si el reciclo se paso, la
   caja ya es de otro; asumir la propiedad seria mutar DayZ mientras otra sesion tiene el
   lease.

## Lo que este ensayo NO prueba

- **Nada por HTTP.** El coordinador es real, pero el `ControlClient`, la credencial, el
  orphan guard y la persistencia del daemon no se tocan.
- **No se reciclo ningun proceso.** El spike probo el reciclo; esto prueba que el lease lo
  sobrevive *a juicio del coordinador*. Los dos no se han corrido juntos todavia.
- **El portador no existe.** El grupo F prueba QUE tiene que llevar, no que algo lo lleve.
- **Nada de operaciones en vuelo a traves del reciclo**: el registro `_command_owner` del
  loopback (`loopback.py:927`) y los pines de operacion no se han ejercitado.
- **Ni concurrencia**: un reciclo, un rival, ninguna llamada simultanea.
