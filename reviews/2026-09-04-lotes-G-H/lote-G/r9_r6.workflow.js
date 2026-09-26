export const meta = {
  name: 'r9-lote-g-r6-audit',
  description: 'Auditoria R9 por angulos de la ronda 6 del lote G (snapshot coherente + ciclo de vida del binding), verificacion adversarial por indice y pasada cruzada',
  phases: [
    { title: 'Angulos', detail: 'un auditor por angulo que el diff toca (DZ-R9: techo 7)' },
    { title: 'Verificar', detail: 'verificador sin el contexto de los angulos: cita e inferencia por separado, casado POR INDICE' },
    { title: 'Cruzado', detail: 'pasada implementer-grade, contexto fresco, traza entre actores' },
  ],
}

const TOOLS = String.raw`C:\Users\guill\AppData\Local\Temp\claude\C--Users-guill-OneDrive-Documentos-DayZ-Projects\938664ff-361c-4575-aa56-c7c06c7ca2a8\scratchpad\lote-G\ws\tools`
const FICHA = String.raw`C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\plans\inbox-20260830\16-fb-20260829-104608-4d66.md`

const CONTRATO = `
EL CONTRATO DE LA RONDA 6 (lote G, ficha 16 ${FICHA}, bloques de actividad):
  P1. La caja publicada por box_occupancy NO MEZCLA INSTANTES: toma UNA foto (manifiesto por
      list_runs(), que ya clona bajo el lock del store, + _last_activity/_activity_unknown/
      revision bajo _activity_lock, sin E/S entre las dos lecturas), suelta, sondea diag/argv
      FUERA, y deriva runs, activity_state, last_activity_age_s y occupied SOLO de la foto.
      Nunca relee memoria viva despues. No toma _operation_lock en la ruta de lectura.
      (Reformulacion deliberada: la version anterior "ninguna respuesta lleva una actividad
      ya no cierta" era insatisfacible para cualquier diseno concurrente y cayo a cuatro
      ataques distintos; la pregunta observable es "no mezcla", no "la mas fresca".)
  P2. Las filas no se cachean; solo las sondas (foreign, ports_in_use, scan_known) con TTL
      1,5 s. Una mutacion durable del manifiesto sin incremento del contador se publica igual.
      El contador de revision ya NO es el mecanismo de coherencia de runs.
  P3. Una observacion sin binding (record_box_command_activity sin run_id) no borra un sello
      acreditado POSTERIOR (high-water mark); una observacion posterior sigue publicando unknown.
  P4. Ningun binding despachable sobrevive a la salida durable de RUNNING: la retirada
      (_retire_run_bindings) precede a la transicion durable en stop_run (desde STOPPING),
      begin_release_owner, repair_recovery_fault, repair_manifest_recovery, _reap_run_locked y
      admin_reconcile. Un run sin binding es la degradacion segura.
  P5. La actividad acreditada durante un intento de arranque que no llega a durable no
      sobrevive al rollback (_settle_failed_launch): el run restaurado publica unknown.
  P6. (ronda 7) Sin dueno no se despacha, pero el binding sobrevive: con el run en
      RUNNING_IDLE (release por release_owner/begin_release_owner; admin_reconcile con
      supervivientes) el enqueue por su binding se rechaza con un codigo propio distinto de
      binding_retired; la cola pendiente del antiguo dueno se vacia; adopt_run rehabilita el
      MISMO binding sin relanzar. STARTING sigue despachando.
  P7. (ronda 7) Todo borrado deja TUMBA: cada borrado de sello registra su epoca por
      (generacion, run_id) y ninguna escritura con epoch <= tumba aterriza (credito o basal).
      La compensacion de P5 corre DESPUES del rollback durable y olvida solo sellos con epoca
      en [inicio del intento, rollback]. El sticky unknown manda sobre todo.
  P8. (ronda 7) El sello del cache de sondas es cota INFERIOR del dato: la revision se
      muestrea ANTES de list_runs() y de la foto de actividad; una mutacion en vuelo deja la
      lectura sin cachear y el lector siguiente vuelve a sondear.
  (ronda 9) P6 es ESTRICTO: con el run en RUNNING_IDLE se rechaza TODO comando por su binding
      -- lectura, mutacion o internal=True -- con run_not_owned, y nada acredita actividad. El
      cerco es un estado logico de ServerState (_fenced_runs) activado y con la cola vaciada
      bajo el lock del loopback ANTES de que manifest.release_owner persista RUNNING_IDLE
      (_quiesce_then_release_owner, usado por release_owner, begin_release_owner en sus dos
      ramas, admin_reconcile con supervivientes y repair_manifest_recovery); revertido si la
      persistencia falla; levantado por adopt_run. El poll de un binding cercado devuelve
      commands: [].
  (ronda 9) P7': cerco transitorio de compensacion (_compensating) alrededor del rollback
      durable de _settle_failed_launch: mientras dura, _seal_activity_locked DESCARTA credito y
      basal para ese run; tras persistir se compensa [inicio, ahora], se eleva la tumba y se
      levanta el cerco (tambien si el replace falla).
  Sigue en pie: actividad en MEMORIA por (daemon_generation, run_id) — la ruta de lectura NO
  lee el audit jsonl (decision del humano: en Windows leer el fichero hace fallar su
  escritura); el audit registra pero no manda; sticky que una escritura buena no limpia;
  atribucion por el run_id del binding; basal que sella el proceso lanzado por este daemon;
  nada de actividad en un rechazo ni en un enqueue interno; fail-closed a unknown/null;
  frescura monotona; la actividad es diagnostico, no autoridad (occupied, stop y reap no la
  miran).
`

const YA_SABIDO = `
YA SABIDO POR EL ORQUESTADOR — no lo re-descubras; ataca sus consecuencias:
  - Seis hallazgos de la revision anterior y su tratamiento: H1 lector que pierde una carrera
    -> disuelto por P1; H2 pop sin comparar epocas -> P3; H3 start rechazado deja frescura por
    la ventana BOUND -> P5 por COMPENSACION (no se retrasa la publicacion del BOUND: decision
    escrita); H4 stop publica EXITED antes de retirar el binding -> P4 trazado a seis caminos;
    H5/H6 repair y cuarentena legacy sin incremento -> disueltos por P2.
  - RESIDUOS DECLARADOS (no son hallazgos salvo que encuentres dano concreto no acotado):
    (a) la atomicidad estricta entre list_runs() y la foto de actividad no se garantiza; la
    ventana es de nanosegundos y sin E/S; (b) occupied = bool(runs or foreign) puede combinar
    un runs fresco con un foreign de hasta 1,5 s; acotado y fail-closed aguas abajo (start_run
    rescanea el diag; snapshot desconocido cuenta como ocupado).
  - Orden de locks: _operation_lock -> _activity_lock. El inverso seria un hallazgo P0.
  - Cinco hallazgos de las revisiones ciegas de la ronda 6, TODOS verificados por el
    orquestador y cerrados en la ronda 7: release deja despachable un run RUNNING_IDLE
    (-> P6 por cerco, no por retirada: retirar romperia el reattach); credito tras la
    compensacion y antes del rollback durable, y escritor acreditado que muestrea epoch
    antes de la E/S del audit y resucita un sello borrado (-> P7, una tumba por epoca);
    sello del cache de sondas muestreado despues del manifiesto = caja vacia con un
    DayZDiag ajeno vivo (-> P8). Un revisor llamo NUEVAS a dos familias: sello-vs-dato y
    borrado-sin-tumba. Ataca sus consecuencias y sus hermanos, no las redescubras.
`

const HALLAZGOS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          file: { type: 'string' },
          lines: { type: 'string' },
          snippet: { type: 'string', description: 'El codigo LITERAL copiado del fichero. Sin esto el hallazgo se descarta.' },
          claim: { type: 'string', description: 'Una frase: que esta mal.' },
          failure: { type: 'string', description: 'Entradas/estado concretos -> resultado incorrecto. Si no puedes escribirlo, no es un hallazgo.' },
          severity: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
          kind: { type: 'string', enum: ['defect', 'improvement'] },
        },
        required: ['title', 'file', 'lines', 'snippet', 'claim', 'failure', 'severity', 'kind'],
      },
    },
  },
  required: ['findings'],
}

const VEREDICTOS = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          index: { type: 'integer', description: 'El numero de la afirmacion tal como se te dio (1-based). Obligatorio.' },
          title: { type: 'string' },
          snippet_matches_file: { type: 'boolean', description: 'Abriste el fichero y dice literalmente eso?' },
          actual_lines: { type: 'string', description: 'Las lineas REALES que leiste, pegadas.' },
          inference_holds: { type: 'boolean', description: 'La conclusion se sigue de ESE codigo, juzgado sin ver el razonamiento del auditor.' },
          why: { type: 'string' },
        },
        required: ['index', 'title', 'snippet_matches_file', 'actual_lines', 'inference_holds', 'why'],
      },
    },
  },
  required: ['verdicts'],
}

// DZ-R9 nombra los angulos y pone el techo en 7. La ronda 6 los toca todos.
const ANGULOS = [
  { key: 'race', foco: 'La foto de box_occupancy: alguna lectura de _last_activity, _activity_unknown, _box_revision o del manifiesto DESPUES de tomarla, en cualquier rama (cacheada, no cacheada, now=)? E/S dentro de un lock? Inversion _activity_lock -> _operation_lock? Un lector y stop/start/reap simultaneos: que se publica.' },
  { key: 'state-machine', foco: 'Ciclo de vida del binding frente a los estados del run en los SEIS caminos de P4: por cada uno, linea de la retirada y linea de la transicion durable, en ese orden; y el fallo intermedio (la transicion durable falla DESPUES de retirar): en que estado queda el run y si es seguro. Frontera de generacion: un run heredado tras restart.' },
  { key: 'data-loss', foco: 'Toda ruta que publique una frescura que el sistema no puede sostener, y toda ruta que PIERDA un sello legitimo: la compensacion de P5 (que olvida exactamente; puede borrar un sello anterior al intento o de un comando legitimo del run heredado solapado con el intento?) y el high-water mark de P3 (compara con la epoca correcta y respeta el sticky?).' },
  { key: 'persistence', foco: 'El manifiesto como unica fuente de las filas ahora que no se cachean: que sigue dependiendo del contador de revision, si el cache de sondas puede publicar un foreign/ports viejo con efecto NO acotado, y si alguna mutacion durable queda invisible por otra via que no sea la cache.' },
  { key: 'admin', foco: 'Lo que ve un operador: occupancy_error_fields y el error active_run_exists derivados de la foto; coherencia entre box.runs y el error; un unknown que se lea como "no hay nadie"; el hint de "paralo" sobre un run que la foto ya no contiene.' },
  { key: 'perf', foco: 'Coste de la foto en la ruta caliente del loopback: list_runs() clona todos los runs por lectura, copia de los mapas de actividad, y las sondas fuera. Cuantas veces se llama box_occupancy por operacion normal; comportamiento con muchos runs retirados en el manifiesto.' },
  { key: 'security', foco: 'Identidad: puede un run_id reciclado o restaurado heredar sellos o un binding de otra vida (rollback de start, retire y re-bind)? Puede un enqueue colarse por un binding retirado o por la cola legacy tras la salida durable de RUNNING?' },
]

phase('Angulos')

const porAngulo = await pipeline(
  ANGULOS,
  (a) => agent(
    `Eres el auditor del angulo **${a.key}** en una auditoria R9 (codigo data-critico: el estado
del lifecycle de un daemon de DayZ y el ciclo de vida de los bindings del loopback).

FOCO DE TU ANGULO, y solo el tuyo: ${a.foco}

CODIGO: ${TOOLS}\\dayz_mcp\\process_lifecycle.py (principal), loopback.py, daemon.py.
Tests en ${TOOLS}\\tests\\ (test_box_occupancy.py, test_process_lifecycle.py, test_loopback.py).
${CONTRATO}
${YA_SABIDO}

REGLAS, y son duras:
 - **Ensena, no cuentes.** Todo hallazgo PEGA el codigo literal con su path:lineas. Una
   descripcion sin el fragmento es una hipotesis y se descarta.
 - **Escribe el fallo concreto**: entradas o estado -> resultado incorrecto. Si no puedes
   escribirlo, no lo reportes.
 - **La tolerancia no es un defecto.** Una guarda redundante, una rama defensiva: robustez.
   Como mucho 'improvement'.
 - No propongas refactors ni renombrados. No auditas el gate, el oraculo ni los briefs.
 - Cero hallazgos es una respuesta legitima y preferible a rellenar.`,
    { label: `angulo:${a.key}`, phase: 'Angulos', schema: HALLAZGOS, effort: 'high' }
  ),
  // Verificacion adversarial: contexto fresco que NO ha visto el informe del angulo.
  // Los veredictos se casan POR INDICE, no por titulo: el verificador reformula titulos y
  // casar por igualdad de cadena mando 5 afirmaciones a "sin verificar" en la primera corrida.
  (informe, a) => {
    const claims = (informe?.findings ?? [])
    if (!claims.length) return { verdicts: [], angulo: a.key, sin_hallazgos: true }
    const lista = claims.map((f, i) =>
      `${i + 1}. "${f.title}"\n   fichero: ${f.file}  lineas: ${f.lines}\n   afirmacion: ${f.claim}`
    ).join('\n')
    return agent(
      `Verificas afirmaciones sobre un codigo. NO has visto quien las hizo ni por que, y es
a proposito: la coincidencia entre agentes que compartieron contexto es contagio, no
confirmacion.

Para CADA afirmacion, abre el fichero en esas lineas y devuelve DOS veredictos separados,
conservando su NUMERO en el campo index:
  1. \`snippet_matches_file\`: el fichero dice literalmente eso? Pega las lineas REALES.
  2. \`inference_holds\`: la conclusion se sigue de ESE codigo? Juzgalo tu, por tu cuenta.

Una cita puede ser exacta y la inferencia falsa. Ese es el caso que mas importa.
Refutar es un resultado bueno. Si refutas 0 de N, sospecha de ti antes que del lote.

RAIZ: ${TOOLS}

AFIRMACIONES:
${lista}`,
      // Opus a proposito: bajo Fable 5.1 las siete verificaciones cayeron con "safeguards
      // flagged this message ([reasoning_extraction])" -- el prompt con afirmaciones + codigo
      // dispara el filtro y la corrida se queda sin veredictos (medido 2026-09-04).
      { label: `verificar:${a.key}`, phase: 'Verificar', schema: VEREDICTOS, effort: 'high', model: 'opus' }
    ).then((v) => ({ angulo: a.key, claims, verdicts: v?.verdicts ?? [] }))
  }
)

phase('Cruzado')

const cruzado = await agent(
  `Pasada implementer-grade, contexto fresco. No has visto ninguna auditoria previa y no
falta que la veas.

Encuentra **toda** forma en que este codigo pueda perder, corromper o desviar en silencio el
estado que publica. Traza cada ESCRITOR hasta cada LECTOR, cada transicion durable del
manifiesto hasta cada binding que la acredita, cada marca en memoria hasta cada sitio que la
limpia o la consulta. Donde una de esas ternas este incompleta, ahi hay un bug.

Lo que importa no es el interior de una capa: es lo que cruza entre actores. Un escritor en
loopback.py, un lector en process_lifecycle.py y un consumidor en el error publico son tres
actores. El daemon que reinicia y cambia la generacion es un cuarto.

CODIGO: ${TOOLS}\\dayz_mcp\\ (process_lifecycle.py, loopback.py, daemon.py)
${CONTRATO}

Mismas reglas: pega el codigo literal, escribe el fallo concreto, la tolerancia no es defecto.`,
  { label: 'implementer-grade', phase: 'Cruzado', schema: HALLAZGOS, effort: 'high' }
)

// --- consolidacion: solo sobrevive lo que la verificacion sostiene por AMBOS lados ---
const filas = []
for (const r of porAngulo.filter(Boolean)) {
  if (r.sin_hallazgos) continue
  const claims = r.claims ?? []
  const verdicts = r.verdicts ?? []
  claims.forEach((c, i) => {
    // por indice; el titulo del verificador es informativo, no la clave
    const v = verdicts.find((x) => x.index === i + 1) ?? verdicts[i] ?? null
    filas.push({
      angulo: r.angulo,
      ...c,
      cita_ok: v?.snippet_matches_file ?? null,
      inferencia_ok: v?.inference_holds ?? null,
      verificador: v?.why ?? 'sin veredicto',
      estado: v == null ? 'SIN_VERIFICAR'
        : (v.snippet_matches_file && v.inference_holds) ? 'CONFIRMADO'
        : v.snippet_matches_file ? 'CITA_OK_INFERENCIA_NO'
        : 'CITA_NO_CUADRA',
    })
  })
}

const total = filas.length
const confirmados = filas.filter((f) => f.estado === 'CONFIRMADO')
const sinVerificar = filas.filter((f) => f.estado === 'SIN_VERIFICAR').length
const refutados = total - confirmados.length - sinVerificar
log(`${total} afirmaciones de ${ANGULOS.length} angulos; ${confirmados.length} confirmadas, ${refutados} refutadas, ${sinVerificar} sin verificar`)
if (total > 0 && refutados === 0) {
  log('AVISO: 0 refutaciones. Motivo para desconfiar del verificador, no para confiar en el lote.')
}
if (sinVerificar > 0) {
  log(`AVISO: ${sinVerificar} afirmaciones sin veredicto: NO se cuentan como refutadas.`)
}

return {
  tasa_refutacion: (total - sinVerificar) ? +(refutados / (total - sinVerificar)).toFixed(2) : null,
  confirmados: confirmados.sort((a, b) => a.severity.localeCompare(b.severity)),
  descartados: filas.filter((f) => f.estado !== 'CONFIRMADO'),
  cruzado: cruzado?.findings ?? [],
}
