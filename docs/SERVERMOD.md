# Cargar el addon solo en el servidor con `-serverMod=`

> Estado: guía redactada a partir del código del addon. **No verificada en una
> ejecución real todavía**: la confirmación con el RPT del cliente queda en la
> cola de Experto MCP. No la cites como hecho probado hasta esa confirmación.

## Qué clases viven en cada lado

El PBO del addon contiene clases de los dos lados:

| Lado | Clase / misión | Qué hace |
|---|---|---|
| Solo servidor | `MissionServer` + `MCPBridge` (`addon/scripts/5_Mission/MissionServer.c`, `MCPBridge.c`) | Bridge del servidor: comandos del servidor, estado del mundo, godmode por misión y demás verbos server-side |
| Solo cliente | `MCPClientBridge` dentro de `MissionGameplay` (`addon/scripts/5_Mission/MissionGameplay.c`, `MCPClientBridge.c`) | Bridge del cliente: verbos que solo existen en el cliente (cámara, UI, input, vista del jugador, …) |

Las dos misiones se enganchan por `modded class`: si el PBO no está cargado en
ese proceso, la clase modded no existe y su bridge nunca arranca.

## Qué pasa si el PBO se carga solo con `-serverMod=`

`-serverMod=` carga el PBO únicamente en el proceso servidor. El cliente no
recibe ese PBO, así que en el cliente:

- no existe `MCPClientBridge` ni el hook de `MissionGameplay` que lo arranca;
- no existen las tools/verbos de cliente (los de `MCPClientBridge`), aunque el
  bridge de servidor responda con normalidad;
- el servidor sí tiene `MCPBridge` y sus verbos de servidor funcionan.

Usa `-serverMod=` solo cuando quieras el bridge de servidor sin tocar el
cliente. Para la sesión completa (servidor + cliente) el PBO debe llegar
también al cliente, con el mecanismo de carga de mods del cliente (`-mod=` o
el que use el launcher), no solo al servidor.

## Síntoma para diagnosticarlo

En una carga correcta del cliente, su RPT muestra el arranque del bridge de
cliente, empezando por la línea `[DayZ-MCP] client init pending/…` (emitida
por `MCPClientBridge` al inicializar). Si el PBO se cargó solo con
`-serverMod=`:

- el RPT del **cliente** no contiene `[DayZ-MCP] client init pending/…` ni
  ninguna otra línea `[DayZ-MCP]` del bridge de cliente;
- el RPT del **servidor** sí muestra actividad `[DayZ-MCP]` de `MCPBridge`.

Esa asimetría (servidor con bridge, cliente sin ninguna línea `[DayZ-MCP]`)
es la firma de «PBO solo en el servidor», no de un bridge de cliente roto.

## Verificación pendiente

Pendiente de confirmar por Experto MCP en una ejecución real: lanzar una vez
solo con `-serverMod=` y comprobar que falta `[DayZ-MCP] client init
pending/…` en el RPT del cliente mientras el servidor responde. Hasta ese log,
esta guía es explicación del código, no un hecho medido.
