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

El logger del cliente es `MCPClientBridge.Log`, que emite el prefijo
`[MCP-CLIENT]`; el prefijo `[DayZ-MCP]` es el del bridge de servidor
(`MCPBridge`). Hay que distinguir dos resultados en el RPT del cliente:

- **Arranque correcto:** `[MCP-CLIENT] client config loaded path=…`. Una
  inicialización correcta emite directamente esa línea, sin pasar por
  `pending`.
- **Inicialización pendiente o fallida:** `[MCP-CLIENT] client init
  pending: <motivo>`. Esa línea solo sale del manejo de fallos
  (`LogInitFailure`: RestApi o config no disponible, URL/clave ausente,
  RestContext no disponible, …). Indica que el PBO de cliente sí cargó,
  pero su inicialización no se completó. Su ausencia no demuestra que
  falte el addon: un arranque correcto tampoco la emite.

Si el PBO se cargó solo con `-serverMod=`:

- el RPT del **cliente** no contiene ninguna línea `[MCP-CLIENT]`, ni la
  de arranque correcto ni la de inicialización pendiente;
- el RPT del **servidor** sí muestra actividad `[DayZ-MCP]` de `MCPBridge`.

Esa asimetría (servidor con bridge, cliente sin ninguna línea
`[MCP-CLIENT]`) es un indicio compatible con «PBO solo en el servidor»,
no su causa exclusiva ni una prueba por sí sola: un RPT de otra
ejecución o perfil, un cliente que aún no llegó a `MissionGameplay` o un
log incompleto producirían la misma ausencia. Confírmalo con la lista de
PBO/mods efectivamente cargados por el cliente antes de concluirlo.

## Verificación pendiente

Pendiente de confirmar por Experto MCP en una ejecución real: lanzar una vez
solo con `-serverMod=` y comprobar que no hay ninguna línea `[MCP-CLIENT]`
en el RPT del cliente mientras el servidor muestra `[DayZ-MCP]` y responde,
y que una carga completa del cliente muestra `[MCP-CLIENT] client config
loaded path=…`. Hasta ese log, esta guía es explicación del código, no un
hecho medido.
