---
date: 2026-07-14
project: DayZ_MCP
topic: agent-session-coordination
status: approved
implementation_status: not-started
---

# Diseño — Coordinación segura de sesiones de agentes

> Diseño completo aprobado por el usuario el 2026-07-14. Fija comportamiento y criterios de aceptación. No implementa código, configuración, skills ni launchers.

## 1. Problema

DayZ MCP admite un daemon compartido y varios clientes proxy, pero hoy solo serializa comandos individuales. No existe una propiedad temporal que abarque una secuencia de acciones, una cola visible de agentes, identidad verificable del propietario ni ownership seguro de procesos DayZ.

El resultado observado es que dos agentes pueden interferirse, una sesión puede quedar retenida más tiempo del necesario, un agente puede no saber cuándo la caja queda libre y launchers existentes pueden terminar procesos iniciados por otro agente.

Evidencia principal:

- El proxy solo aplica un lock local por cliente y envía `cmd`, `args` y `peer`: `tools/dayz_mcp/server.py:206-243`, `tools/dayz_mcp/server.py:321-324`.
- El criterio F4 actual promete serialización first-come, no leases de agente: `product-spec.md:86-100`.
- Claude usa `--client`, pero la configuración efectiva de Codex no: `C:/Users/guill/.claude.json:1350-1363`, `C:/Users/guill/.codex/config.toml:207-217`.
- Las reglas y launchers existentes contienen rutas de cierre incompatibles con ownership seguro: `C:/Users/guill/.claude/skills/dayz-mcp-verify/SKILL.md:252-259`, `C:/Users/guill/.claude/skills/dayz-test-ingame/templates/dayz-test.ps1:103-130`.

## 2. Objetivos

1. Permitir varias sesiones Claude/Codex conectadas a la vez a un único daemon.
2. Permitir lecturas puras concurrentes cuando no alteren la caja DayZ.
3. Dar exclusividad FIFO a secuencias que mutan mundo, control, cámara, vehículo o procesos.
4. Recuperar automáticamente sesiones abandonadas tras 120 s sin actividad relevante.
5. Impedir que las rutas oficiales terminen procesos o liberen leases de otro agente.
6. Hacer observable y auditable acquire, espera, uso, release, expiración y administración.
7. Convertir el cierre disciplinado y su comprobación en una regla persistente para Claude y Codex.

## 3. No objetivos

- No aislar procesos a nivel de kernel ni impedir que un usuario con shell completo ejecute manualmente `Stop-Process`.
- No crear un segundo coordinador, servicio Windows aislado o sistema distribuido.
- No garantizar estabilidad visual de una lectura concurrente mientras otro agente muta la escena; quien necesite una escena estable debe adquirir el lease.
- No matar DayZ ni el daemon al liberar una sesión.
- No inferir ownership a partir de `@Mod`, profiles, misión o nombre del ejecutable.
- No implementar en esta fase. La implementación requerirá un plan separado tras revisar esta spec.

## 4. Decisión arquitectónica

### 4.1 Autoridad única

El coordinador será el daemon existente:

`[DESIGN]` Diagrama lógico:

```text
Claude --client ─┐
Claude --client ─┤
Codex  --client ─┼──> dayz_mcp --daemon ───> bridge DayZ / procesos registrados
Codex  --client ─┘
```

- Todas las sesiones interactivas normales de Claude y Codex usarán `--client`.
- Solo habrá un listener/daemon autoritativo por caja DayZ.
- El modo embedded quedará reservado a CI/offline/backcompat explícita; nunca será fallback automático cuando el daemon no responda.
- Si no hay coordinador disponible, las mutaciones y operaciones de lifecycle fallarán cerradas.

### 4.2 Recurso exclusivo

El daemon administrará un recurso lógico único denominado `[DESIGN] dayz-box`.

Requieren lease activo:

- mutaciones del mundo, tiempo o clima;
- control o movimiento de jugador/vehículo;
- mutaciones de cámara;
- secuencias de QA cuya evidencia exige escena estable;
- lanzamiento, adopción, reinicio, reemplazo o terminación de procesos DayZ;
- herramientas nuevas o no clasificadas, por defecto.

Pueden ejecutarse concurrentemente sin lease:

- health y bridge status;
- consultas de jugador/entidades;
- raycast y telemetría;
- diagnósticos y lectura de cámara;
- captura de evidencia que no requiera escena estable.

Aquí «concurrente» significa que la lectura se admite sin esperar el lease de otro agente. El transporte de un único juego puede seguir serializando comandos individuales internamente. Una lectura pura no promete un snapshot estable frente a mutaciones concurrentes; el agente que necesite esa garantía la ejecutará con su lease explícito.

## 5. Identidad y autorización

Cada proxy construirá una identidad `[DESIGN] client_id` con:

- plataforma (`claude` o `codex`);
- PID y PPID del proxy;
- instante real de inicio;
- UUID de sesión generado;
- etiqueta humana opcional de tarea.

Todas las peticiones transportarán esa identidad. Una petición exclusiva transportará además un `lease_token` opaco.

El daemon validará que:

1. el token existe y pertenece a la generación actual del daemon;
2. el token está ACTIVE;
3. la identidad coincide con su propietario;
4. el lease no ha expirado;
5. la herramienta solicitada está permitida en ese estado.

Tickets y leases quedan ligados a esa identidad. Otro proxy no puede esperar, renovar, liberar ni usar los identificadores de un agente aunque llegue a conocerlos. La identidad sirve para autorización y auditoría, no como secreto. El token sí deberá redactarse de logs y errores.

## 6. Cola y máquina de estados

### 6.1 Estados

`[DESIGN]` Máquina de estados:

```text
FREE -> QUEUED -> ACTIVE -> RELEASING -> FREE
           |         |
        CANCELLED  EXPIRED
```

- `FREE`: no hay propietario.
- `QUEUED`: existe un ticket FIFO pendiente.
- `ACTIVE`: el propietario puede realizar operaciones exclusivas.
- `RELEASING`: el coordinador ejecuta cleanup acotado.
- `EXPIRED`: el lease perdió vigencia; ya no autoriza mutaciones.
- `CANCELLED`: ticket abandonado o cancelado antes de ser propietario.

### 6.2 Superficie MCP propuesta

Todas estas firmas son `[DESIGN]`; el plan deberá verificarlas y concretar schemas antes de implementarlas.

- `[DESIGN] session_acquire(purpose) -> {status, lease_token? , ticket?}`
- `[DESIGN] session_wait(ticket, timeout_s <= 30) -> {status, position?, lease_token?}`
- `[DESIGN] session_heartbeat(lease_token) -> {expires_at}`
- `[DESIGN] session_release(lease_token) -> {released, cleanup}`
- `[DESIGN] session_status() -> {daemon_generation, owner?, queue, self}`

`session_status` nunca devuelve tokens ni datos sensibles de otros clientes; propietario y cola se presentan con identidad y propósito redactados, conservando posición, edad y estado útiles para coordinación.

No existirá un `force_release` como tool MCP normal. La administración excepcional será un helper local separado, con confirmación humana explícita en consola, motivo obligatorio y evento de auditoría. Su modo normal rechazará invocación no interactiva; esto reduce accidentes, pero no pretende crear aislamiento de SO frente a un usuario con shell completo.

### 6.3 FIFO y expiración

- La cola será FIFO estricta por instante de ticket.
- Esperar no permite adelantar, reemplazar ni evictar al propietario.
- Cada identidad puede tener como máximo un lease o ticket vivo. Repetir `session_acquire` es idempotente y devuelve el estado existente; no crea posiciones duplicadas.
- Un ticket se cancela si su cliente no ejecuta `session_wait` durante 120 s.
- Un lease expira tras 120 s sin renovación válida.
- Cada mutación autorizada renueva automáticamente el lease.
- Un `session_heartbeat` explícito lo renueva.
- Una lectura solo lo renueva si incluye explícitamente el token; las lecturas de otros agentes nunca renuevan al propietario.
- `session_wait` será una espera acotada de hasta 30 s, repetible. Esto evita depender de que todos los clientes muestren notificaciones push.
- Una mutación ya aceptada mantiene el lease únicamente hasta el hard deadline propio de esa operación. Ninguna llamada colgada puede fijar el lease sin límite.
- El protocolo solo permite heartbeat mientras exista trabajo exclusivo activo; mantenerlo durante espera humana o trabajo que puede hacerse sin la caja es una infracción auditable.

El contrato de frontera es exacto: antes de 120 s sigue vigente; al alcanzar 120 s sin renovación pasa a expirado. Los tests cubrirán 119/120/121 s con reloj inyectable.

## 7. Liberación y recuperación

### 7.1 Release normal

Al recibir `session_release` del propietario, el daemon:

1. marca el lease `RELEASING` y rechaza nuevas mutaciones del propietario;
2. cancela comandos del propietario todavía no entregados al juego;
3. intenta un `vehicle_release` acotado si el estado indica control activo;
4. registra resultado y degradaciones de cleanup;
5. invalida el token;
6. concede el recurso al siguiente ticket FIFO válido.

La liberación no mata DayZ ni el daemon. El run registrado queda en `RUNNING_IDLE` salvo que el propietario haya solicitado previamente una operación de lifecycle autorizada.

### 7.2 Expiración

La expiración ejecuta el mismo cleanup acotado. Un fallo de cleanup no puede bloquear indefinidamente la cola:

- se registra como cierre degradado;
- el token queda inválido de inmediato;
- se cancela todo lo todavía cancelable;
- se intenta `vehicle_release`;
- se avanza la cola al terminar el presupuesto de cleanup.

El deadman del lado Enforce sigue siendo la última defensa para una acción ya entregada que el daemon no pueda cancelar.

### 7.3 Caída o reinicio del daemon

- Cada arranque crea una nueva `daemon_generation`.
- Tickets y leases de generaciones anteriores son inválidos.
- El arranque registra la invalidación como expiración por restart.
- El manifiesto de procesos persiste para permitir reconciliación segura.
- Ningún cliente cambia automáticamente a embedded.

## 8. Lifecycle y ownership de procesos

### 8.1 Manifiesto persistente

Cada proceso lanzado por una ruta oficial se registra con:

- PID;
- creation time real del proceso;
- executable fingerprint;
- command-line fingerprint con secretos redactados;
- mod, profiles y mission como metadatos, no como prueba de ownership;
- `[DESIGN] run_id`, lease que lo creó e identidad del agente;
- estado (`STARTING`, `RUNNING`, `RUNNING_IDLE`, `STOPPING`, `EXITED`, `UNRECONCILED`).

### 8.2 Regla para detener

Antes de detener un proceso, el helper central revalida PID + creation time + executable fingerprint + command-line fingerprint contra el manifiesto.

- Coincidencia completa y lease autorizado: la operación puede continuar.
- PID reutilizado, proceso no registrado o fingerprint distinto: fail-closed.
- Proceso legacy/no registrado: requiere reconciliación administrativa explícita.
- Coincidir en `@Mod` nunca concede propiedad.

Ningún launcher oficial usará `Get-Process ... | Stop-Process` directamente. Todos consultarán el mismo guard del coordinador.

`[DESIGN]` Los launchers invocarán un helper de lifecycle respaldado por el daemon y presentarán el contexto del lease activo. El token no viajará en argumentos de command line ni se imprimirá en logs; el plan de implementación deberá verificar un transporte local seguro y compatible con Claude/Codex antes de cerrar su firma.

### 8.3 Adopción y reemplazo

Cuando un nuevo agente adquiere el lease puede:

- adoptar un run `RUNNING_IDLE` compatible;
- solicitar reemplazo;
- dejarlo intacto y ejecutar solo mutaciones compatibles.

El reemplazo solo puede terminar PIDs que superen la revalidación completa del manifiesto. Ante duda, se detiene la operación, no el proceso.

## 9. Persistencia, auditoría y observabilidad

El runtime se almacenará fuera de OneDrive en `[DESIGN] %LOCALAPPDATA%\DayZ_MCP\`:

- manifiesto de procesos persistente;
- generación y estado recuperable mínimo;
- JSONL de auditoría rotado;
- diagnóstico/doctor.

Solo el daemon escribe el log autoritativo. Cada evento incluirá, como mínimo:

- timestamp UTC y event type;
- daemon generation;
- client identity redactada;
- ticket/lease/run IDs no secretos;
- herramienta/clase de operación;
- decisión (`allowed`, `rejected`, `expired`, `cancelled`, `released`);
- motivo y duración;
- resultado de cleanup cuando aplique.

No se guardarán keyfiles, claves, tokens de lease, passwords ni command lines completas sin redacción. La auditoría runtime no se volcará rutinariamente en `HANDOFF.md`.

## 10. Protocolo durable para agentes

La fuente canónica será `[DESIGN] C:\Users\guill\ObsidianVault\AI\20_Runbooks\dayz-mcp-agent-session-protocol.md`.

Las instrucciones de alto nivel contendrán solo cuatro reglas duras y un enlace al runbook:

1. adquirir antes de mutar o gestionar procesos;
2. liberar en cuanto termine la secuencia exclusiva;
3. no matar procesos DayZ directamente;
4. ejecutar `session_status` antes del handoff y declarar cualquier cierre degradado.

Los punteros se incorporarán, mediante una fase de implementación posterior, a:

- workflow global;
- instrucciones globales de Claude;
- instrucciones globales de Codex;
- `DayZ_MCP_dev/CLAUDE.md` y un `DayZ_MCP_dev/AGENTS.md` nuevo;
- skills `dayz-mcp-verify` y `dayz-test-ingame`.

El instalador deberá configurar y verificar tanto Claude como Codex en modo `--client`. Un doctor deberá detectar:

- registro embedded no autorizado;
- más de un listener/daemon;
- leases o tickets abandonados;
- procesos DayZ no registrados;
- launchers con blind kill o bypass del guard central.

## 11. Checklist de cierre y handoff

Antes de cerrar una tarea que haya usado DayZ MCP, el agente comprueba:

- `self.own_lease = none`;
- `self.own_ticket = none`;
- `self.pending_commands = 0`;
- control de vehículo liberado o deadman confirmado;
- estado del run declarado (`RUNNING_IDLE`, `EXITED` u otro estado explícito);
- ausencia de cleanup degradado no documentado.

Solo se modifica `HANDOFF.md` si existe un cambio durable de estado, un incidente o una degradación relevante. La operación normal queda en el JSONL de auditoría.

## 12. Manejo de errores

- Tool exclusiva sin lease: rechazo antes de entrar en la cola del juego.
- Token ajeno, expirado o de otra generación: rechazo sin side effects.
- Daemon ausente: mutaciones y lifecycle fallan cerrados; lecturas solo si el contrato demuestra que no abren un runtime embedded.
- Cola llena: error explícito con capacidad y retry guidance; nunca eviction silenciosa.
- Cleanup excede presupuesto: se marca degradado, se invalida el lease y se avanza.
- Revalidación de proceso falla: no se mata nada y se crea incidente auditable.
- Escritura de auditoría/manifiesto falla: operaciones de lifecycle fallan cerradas; el plan decidirá qué lecturas o mutaciones pueden continuar sin perder trazabilidad.

## 13. Criterios de aceptación

Se añadirá un grupo de coordinación multiagente al `product-spec.md`. La numeración definitiva se decidirá al planificar para no colisionar con cambios concurrentes.

### 13.1 Topología

- Dos sesiones Claude y dos Codex efectivas usan `--client`.
- Las cuatro observan exactamente un daemon/listener autoritativo.
- No aparece un runtime embedded al detener o perder el daemon.

### 13.2 Exclusión y lecturas

- Con A como propietario, B puede realizar una lectura pura permitida.
- Una mutación de B se rechaza antes de entrar en la cola del juego.
- Una secuencia de varias mutaciones de A no se intercala con mutaciones de otro agente.
- Una tool nueva/no clasificada requiere lease por defecto.

### 13.3 FIFO y expiración

- Con A activo y B/C en cola, la concesión es A -> B -> C.
- Dos `session_acquire` de la misma identidad devuelven el mismo lease/ticket y no duplican su posición.
- Usar desde otra identidad un ticket o token observado se rechaza sin side effects.
- Si B abandona su ticket, se cancela a los 120 s y C avanza.
- Tests con reloj inyectable demuestran los estados correctos a 119, 120 y 121 s.
- Una operación exclusiva larga conserva el lease hasta su deadline acotado; al vencerlo, no puede bloquear indefinidamente la cola.
- Matar el proceso stdio del propietario no mata daemon, DayZ ni otros clientes; el siguiente recibe el lease después de la expiración.

### 13.4 Cleanup y comandos

- Release y expiry cancelan comandos del propietario todavía no entregados.
- Se intenta `vehicle_release` de forma acotada.
- Los comandos cancelados no reaparecen al reconectar el juego.
- Un fallo de cleanup no deja la cola bloqueada.

### 13.5 Procesos

- Dos agentes sobre el mismo mod no confunden ownership.
- Solo se detiene un proceso registrado cuyo PID, creation time y fingerprints coinciden.
- PID reutilizado, proceso extranjero o proceso legacy quedan intactos.
- Reiniciar el daemon invalida leases/tickets antiguos, conserva el manifiesto y permite reconciliar el run.

### 13.6 Auditoría y disciplina

- El JSONL reconstruye acquire -> wait -> grant -> use -> release/expiry con motivo y agente.
- Pruebas negativas demuestran que no aparecen keyfiles, claves ni tokens.
- No existe force-release como tool MCP; el helper admin exige confirmación y motivo.
- El handoff final prueba `own_lease = none`, `own_ticket = none` y `pending_commands = 0`.

### 13.7 Gate real combinado

En una sola caja y un solo juego:

1. conectar dos Claude y dos Codex;
2. ejecutar lecturas concurrentes;
3. encolar mutaciones de tres agentes y comprobar FIFO;
4. comprobar release normal y expiración por caída del propietario;
5. adoptar o reemplazar de forma segura un run registrado;
6. recoger JSONL y estado final sin recursos propios pendientes.

Una suite offline verde no sustituye este gate real.

## 14. Superficies previstas para una implementación posterior

La implementación probablemente afectará, como mínimo:

- broker/daemon/loopback y proxy MCP;
- schemas y tests de coordinación;
- registro y guard de lifecycle;
- instalador y doctor;
- product spec y decisión durable;
- runbook e instrucciones de agentes;
- skills y launchers heredados inventariados.

Esta lista delimita áreas; no autoriza edits hasta que exista y se apruebe un plan de implementación con archivos exactos, contratos y viability tests.

## 15. Riesgo residual aceptado

La arquitectura protege las rutas oficiales y hace fail-closed la coordinación. No puede impedir que un agente con shell completo bajo el mismo usuario Windows mate manualmente un proceso. Obtener esa garantía exigiría aislamiento por identidad/ACL de servicio, una arquitectura deliberadamente fuera de alcance en esta fase.

El protocolo lo prohíbe, el doctor detectará bypasses conocidos y la auditoría permitirá atribuir operaciones oficiales. Este riesgo residual debe permanecer explícito en el plan y en el handoff de implementación.
