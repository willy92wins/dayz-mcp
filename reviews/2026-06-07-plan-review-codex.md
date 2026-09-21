### Resumen ejecutivo
- Veredicto: reject as-is.
- El plan respeta la direccion general del research consolidado: B3 se trata como probe y no como PASS de producto, P2-4 evita `DestroyRestApi`, y el predicado de asiento usa `DayZPlayerConstants.VEHICLESEAT_DRIVER`.
- No empezaria implementacion todavia: R22-001 deja un comando mutador (`world_spawn`) sin validacion fail-closed de `args.type/pos/flags` antes de `CreateObjectEx`.
- Ademas, R22-003 puede contaminar el dato del probe B3: un coche sin ruedas/radiador/fluidos completos puede devolver `speedo=0` por fixture roto, no por client-auth.
- Los WARN restantes son acotados: cerrar backpressure real en el bridge, sincronizar product-spec con la realidad B2/B3, y desambiguar citas `path:line`.

### Matriz de hallazgos
| ID | §plan | Severidad | Resumen | Resolucion sugerida |
|---|---|---|---|---|
| R22-001 | Paso 0.1 / 1a / Criterios R26 | FAIL | `world_spawn` ejecuta `CreateObjectEx(args.type, pos, flags, rotation)` sin que el plan obligue a validar `args`, tipo, pos de 3 floats, flags permitidos o config existente antes del side-effect. | Antes de cualquier `CreateObjectEx`, exigir helper fail-closed: `args != null`, `type` no vacio y `CfgVehicles <type>` existente, `pos != null && Count()==3`, coords finitas/rango permitido, flags dentro de allowlist/mask, rotation saneada; fixture negativo para cada rechazo. |
| R22-002 | Paso 0.3 | WARN | El backpressure limita el dispatch por tick y la cola Python, pero no limita `m_Pending` ni evita seguir polleando mientras hay atraso local. | Definir `MAX_PENDING`, drenaje antes de `StartPoll`, politica de no iniciar otro `/poll` si `m_Pending` no esta por debajo de umbral, y test de productor saturado que pruebe que no crece sin limite. |
| R22-003 | 1b.2 / B3 probe | WARN | El fixture del probe prepara fuel + bateria/bujia, pero no fija ruedas/radiador/fluidos completos ni una clase final; `speedo=0` puede ser dato contaminado del fixture. | Pinear una clase de coche verificada y preparar el coche como `OnDebugSpawn`: partes universales, radiador si vital, ruedas, fuel/coolant/oil; o llamar un helper equivalente verificado. El probe debe loggear readiness mecanica antes de interpretar `speedo=0`. |
| R22-004 | DPF grupo B / 1b.1 / 1b.2 | WARN | El plan corrige B2 y degrada B3 a probe, pero `product-spec.md` sigue diciendo `GetVehicleSeat()==0` y que B3 mueve el vehiculo; falta changelog de alcance/criterio. | Actualizar product-spec antes de implementar: B2 usa constante nombrada; B3 fase 1b es spike/probe de decision, no acceptance PASS. Mantener B3 producto como pendiente hasta decision post-probe. |
| R22-005 | Contexto verificado / citas EXACT | NIT | Varias citas usan solo basename (`game.c:702`, `game.c:929`, `game.c:947`) aunque existe mas de un `game.c`; R2 pide path:line no ambiguo. | Cambiar citas load-bearing a path relativo completo, por ejemplo `scripts/3_game/global/game.c:702`, `:929`, `:947`. |

### Hallazgos detallados

#### R22-001 - Paso 0.1 / 1a / Criterios R26 - FAIL

Cita exacta del plan:

```text
plans/2026-06-07-fase1-control.md:23-38
0.1 - DTO con args ... MCPArgs { string type; ref array<float> pos; int flags; ... }
JsonSerializer mapea {}->MCPArgs con defaults; campos ausentes quedan default.

plans/2026-06-07-fase1-control.md:63-68
1. Leer args.type, args.pos (vector), args.flags||ECE_PLACE_ON_SURFACE...
2. Object o = GetGame().CreateObjectEx(args.type, pos, flags, rotation);
```

Problema: el plan pasa datos controlados por el cliente a un side-effect de mundo sin gate fail-closed explicito. `mcp_server.py` solo comprueba que `args` sea `dict` (`tools/mcp_server.py:119-122`); no valida `type`, no valida `pos`, no limita `flags`, y tampoco hay validacion equivalente en el handler antes de `CreateObjectEx`. La API existe y muta mundo (`scripts/3_game/global/game.c:702`). Tambien existe una forma directa de comprobar config (`scripts/3_game/global/game.c:611`). Para convertir `[x,y,z]` a `vector`, `vector.ArrayToVec` indexa `arr[0..2]` sin guard (`scripts/1_core/proto/enconvert.c:515-517`), asi que una lista ausente/corta puede producir exception o datos malos antes de fallar cerrado. Ademas, `ECE_NONE=0` y `ECE_PLACE_ON_SURFACE=1060` (`scripts/3_game/ce/centraleconomy.c:7`, `:37`): usar "0 => default" hace indistinguible "campo ausente" de "usuario pidio flags 0".

Impacto: side-effect sin validacion previa. Esto viola R6 para un comando mutador y puede producir spawn de clases arbitrarias, posicion invalida, flags no deseados, o exception/degradation en el bridge antes de devolver un error de negocio.

Resolucion sugerida: el plan debe exigir un helper de validacion antes del side-effect y fixtures negativos. Minimo:
- `command.args != null`.
- `args.type != ""` y `GetGame().ConfigIsExisting("CfgVehicles " + args.type)`.
- `args.pos != null && args.pos.Count() == 3` y conversion controlada a `vector`; no usar `ArrayToVec` sin guard.
- `flags` default solo si el campo se declara ausente por contrato, o bien no aceptar `ECE_NONE`; en cualquier caso usar allowlist/mask explicita.
- `CreateObjectEx` debe devolver objeto no-null antes de crear job.
- Tests R26: `type` inexistente, `pos` ausente, `pos` corta, `args` no objeto, flags fuera de allowlist -> error sin spawn.

#### R22-002 - Paso 0.3 - WARN

Cita exacta del plan:

```text
plans/2026-06-07-fase1-control.md:43-47
Bridge: cap de dispatch por tick ... MAX_DISPATCH_PER_TICK (=4); el resto a una cola interna m_Pending drenada en OnTick.
Server: cap de cola ... MAX_QUEUE (=64) -> 429.
```

Problema: esto evita "1000 spawns en un tick", pero no cierra el backpressure end-to-end. Hoy `/poll` devuelve toda la cola y la limpia (`tools/mcp_server.py:150-153`). El bridge actual despacha todo el batch en un callback (`DayZ_MCP/scripts/5_Mission/MCPBridge.c:193-199`) y `OnTick` vuelve a pollear por intervalo cuando no hay request en vuelo (`MCPBridge.c:45-69`). Si la nueva implementacion solo mete el excedente en `m_Pending`, el backlog queda del lado Enforce. Sin `MAX_PENDING` ni politica de "no pollear si hay atraso", un productor puede alternar polls de 64 comandos con drenaje local y hacer crecer memoria/trabajo pendiente.

Impacto: degradation y posible log/cola creciente bajo carga. P2-3 queda parcialmente cubierto, no cerrado.

Resolucion sugerida: anadir al plan el invariante de capacidad local. Drenar `m_Pending` antes de considerar `StartPoll`; no iniciar otro poll mientras `m_Pending.Count()` supere un umbral bajo; rechazar o pausar intake cuando `m_Pending` alcance `MAX_PENDING`; fixture de saturacion que encole mas de `MAX_QUEUE` sostenido y demuestre queue bounded + no mas de `MAX_DISPATCH_PER_TICK` side-effects/tick.

#### R22-003 - 1b.2 / B3 probe - WARN

Cita exacta del plan:

```text
plans/2026-06-07-fase1-control.md:89-94
Spawn de un coche [verify clase en config] (candidato OffroadHatchback/Hatchback_02);
veh.Fill(CarFluid.FUEL, veh.GetFluidCapacity(CarFluid.FUEL)); atachar battery+sparkplug...
Salida del probe: {engine_on_server, speedo_max, pos_delta, net_strategy}.
```

Problema: el probe decide arquitectura con datos runtime, pero el fixture no garantiza que el coche sea mecanicamente capaz de moverse. Vanilla `OnDebugSpawn()` para `Hatchback_02` llama `SpawnUniversalParts()`, `FillUpCarFluids()`, y crea cuatro ruedas antes de usarlo (`scripts/4_world/entities/vehicles/inheritedcars/hatchback_02.c:389-397`). `OffroadHatchback` hace el mismo patron con `HatchbackWheel` (`.../offroadhatchback.c:459-467`). `SpawnUniversalParts()` mete bateria/radiador/bujia/glowplug segun vitalidad (`scripts/4_world/entities/vehicles/carscript.c:3121-3151`) y `FillUpCarFluids()` llena fuel, coolant y oil (`carscript.c:3191-3196`). El plan solo llena fuel y menciona bateria/bujia; no fija ruedas, radiador/coolant/oil ni una clase final. Las clases candidatas existen (`DZ/data/config.cpp:2970`, `:2982`; `DZ/vehicles/wheeled/config.cpp:1242`), pero el fixture no esta cerrado.

Impacto: `engine_on_server=false` sigue siendo dato util por el gate client-auth, pero `speedo_max≈0` o `pos_delta=0` puede ser falso por coche sin ruedas/partes, no por `NetworkMoveStrategy.PHYSICS`. Eso contamina la decision B3 server/client/diferir.

Resolucion sugerida: pinnear un fixture unico y prepararlo completo. Opciones aceptables para el plan: invocar un helper equivalente a `OnDebugSpawn()` si es legal en este contexto, o crear explicitamente partes universales + ruedas + fluidos completos con APIs verificadas (`GameInventory.CreateInInventory`, `Car.Fill`, etc.). El probe debe registrar una precondicion `vehicle_fixture_ready` antes de interpretar movimiento.

#### R22-004 - DPF grupo B / 1b.1 / 1b.2 - WARN

Cita exacta del plan:

```text
plans/2026-06-07-fase1-control.md:82
B2: GetCommand_Vehicle()!=null && !IsGettingIn() && GetVehicleSeat()==VEHICLESEAT_DRIVER

plans/2026-06-07-fase1-control.md:84-95
vehicle_drive PROBE ... decision data, no es la tool final.
```

Problema: el plan esta alineado con el research, pero el contrato DPF aun no lo esta. El product-spec B2 dice `GetVehicleSeat()==0` (`DayZ_MCP_dev/product-spec.md:53-55`), mientras el research resolvio que debe usarse `DayZPlayerConstants.VEHICLESEAT_DRIVER` porque el enum no vale 0 (`AI/10_Projects/DayZ_MCP/research/2026-06-07-fase1-control.md:81-84`; `scripts/3_game/dayzplayer.c:669-674`). El product-spec B3 sigue diciendo "`vehicle_drive` mueve el vehiculo" (`product-spec.md:55`), pero el plan declara que fase 1b solo recolecta probe y que el PASS de producto queda post-decision (`plans/...:84-95`, `:106-117`). El propio DPF obliga a bajar cambios al changelog cuando el criterio choca con el Intent o revela un criterio faltante (`product-spec.md:20-27`, `:117-125`).

Impacto: riesgo de acceptance artifact/corruption del contrato: alguien puede marcar B2/B3 con criterios viejos o interpretar el probe como cumplimiento de B3.

Resolucion sugerida: antes de implementar, actualizar `product-spec.md` con changelog: B2 usa constante nombrada, no literal 0; B3 fase 1b es probe de decision y no PASS. Mantener B3 producto pendiente hasta que el usuario adjudique server/client/diferir tras el dato.

#### R22-005 - Contexto verificado / citas EXACT - NIT

Cita exacta del plan:

```text
plans/2026-06-07-fase1-control.md:10-17
CreateObjectEx ... game.c:702
GetObjectsAtPosition3D ... game.c:929
GetGame().GetPlayers ... game.c:947
```

Problema: hay mas de un `game.c` bajo el source local. Las firmas load-bearing estan en `scripts/3_game/global/game.c:702`, `:929`, `:947`; el otro `scripts/3_game/game.c` no contiene esas firmas en esas lineas. Como R2 exige `path:line`, el basename queda ambiguo aunque la cita sea recuperable por contexto.

Resolucion sugerida: expandir las citas del plan a paths relativos completos para las APIs duplicadas o frecuentes (`scripts/3_game/global/game.c`, `scripts/3_game/vehicles/car.c`, etc.).

### Cobertura

- P2-3 backpressure: parcialmente cubierto. El plan cubre `MAX_DISPATCH_PER_TICK` y `MAX_QUEUE`, pero falta `MAX_PENDING`/poll gating local, asi que no lo marco cerrado.
- P2-4 RestApi/DestroyRestApi: cubierto. Research dice no usar `DestroyRestApi` y mantener get-or-create (`research/...:47`); memoria durable lo confirma (`verified-apis.md:8-11`, `decisions/decision-log.md:8-9`). El plan no introduce `DestroyRestApi`.
- Criterios product-spec: B1 trazado; B2 trazado pero DPF debe corregir literal `==0`; B3 trazado solo como probe, no como criterio de producto cumplido.
- APIs verificadas en esta review: `CreateObjectEx` (`scripts/3_game/global/game.c:702`), `GetObjectsAtPosition3D` (`:929`), `GetPlayers` (`:947`), `ConfigIsExisting` (`:611`), `StartCommand_Vehicle`/`GetCommand_Vehicle` (`scripts/3_game/human.c:1492`, `:1494`), `HumanCommandVehicle` (`human.c:689-734`), `CrewPositionIndex`/`GetSeatAnimationType`/`GetAnimInstance` (`scripts/3_game/vehicles/transport.c:116`, `:475`, `:465`), `DayZPlayerConstants.VEHICLESEAT_DRIVER` (`scripts/3_game/dayzplayer.c:674`), car setters/engine/speed/fluid (`scripts/3_game/vehicles/car.c:113`, `:196`, `:202`, `:220`, `:241`, `:244`, `:271`, `:359`, `:376`), `CheckOperationalRequirements` (`scripts/4_world/entities/vehicles/carscript.c:1980-2016`), PHYSICS client-auth gate (`actionstartengine.c:51-58`), `JsonSerializer.ReadFromString` (`MCPBridge.c:168-170`; `scripts/3_game/gameplay.c:70-100`), DTO actual (`MCPMessages.c:8-17`), whitelist/queue (`tools/mcp_server.py:14`, `:119-128`, `:150-153`).
- APIs/contratos no cerrados por el plan: semantics exactas de `JsonSerializer` para `ref MCPArgs args` con `{}` y campos ausentes; acceso concreto a `PluginDeveloper.SpawnEntityInInventory` desde el bridge; fixture final de coche y preparacion completa para B3.

### Proximo paso

Reject as-is. Claude deberia aplicar R22-001 obligatoriamente, y R22-002/R22-003/R22-004 antes de reenviar el plan. R22-005 puede corregirse junto con esos cambios.
