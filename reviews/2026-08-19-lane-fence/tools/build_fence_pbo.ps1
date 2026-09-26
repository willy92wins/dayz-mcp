# Stage and pack the fencing PBO WITHOUT touching the tree or deploying.
#
# The staging is a merge on purpose: the addon comes from the CURRENT tree (another
# session edited mcp_dialog.layout at 01:02 tonight) and only the three Enforce
# sources are overlaid from the fencing copy. Packing straight from the fencing
# copy would ship a two-day-old layout and silently revert that session's work.

$ErrorActionPreference = "Stop"

$treeAddon = "C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP"
$fenceSrc  = Join-Path $PSScriptRoot "..\enforce-fencing"
$stage     = Join-Path $env:TEMP ("mcp-pbo-fence-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$stageSrc  = Join-Path $stage "DayZ_MCP"
$outDir    = Join-Path $stage "out"
$builder   = "C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools\Bin\AddonBuilder\AddonBuilder.exe"

if (-not (Test-Path $builder)) { throw "AddonBuilder not found: $builder" }
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -Confirm:$false }
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

& robocopy.exe $treeAddon $stageSrc /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed: $LASTEXITCODE" }

$enforce = @("MCPBridge.c", "MCPClientBridge.c", "MCPMessages.c")
"--- overlay de Enforce (copia del fencing -> staging) ---"
foreach ($f in $enforce) {
    $src = Join-Path $fenceSrc $f
    $dst = Join-Path $stageSrc "scripts\5_Mission\$f"
    $before = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $after = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash
    "{0,-20} arbol={1} -> fencing={2}" -f $f, $before.Substring(0, 12), $after.Substring(0, 12)
}

# The layout must stay the tree's version, not the copy's.
$lay = Join-Path $stageSrc "gui\layouts\mcp_dialog.layout"
$layTree = Join-Path $treeAddon "gui\layouts\mcp_dialog.layout"
if ((Get-FileHash -LiteralPath $lay -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $layTree -Algorithm SHA256).Hash) {
    throw "staged layout does not match the tree -- refusing to pack"
}
"layout             : identico al arbol ($((Get-Item $lay).Length) B) OK"

# inst= must actually be in the packed sources, or the PBO is pointless.
foreach ($f in @("MCPBridge.c", "MCPClientBridge.c")) {
    $hits = (Select-String -LiteralPath (Join-Path $stageSrc "scripts\5_Mission\$f") -Pattern '&inst=' -AllMatches).Matches.Count
    "inst= en $f      : $hits ocurrencia(s)"
    if ($hits -lt 1) { throw "$f does not send inst= -- wrong source staged" }
}
$ver = Select-String -LiteralPath (Join-Path $stageSrc "scripts\5_Mission\MCPMessages.c") -Pattern 'MCP_BRIDGE_VERSION\s*=\s*"(\d+)"'
"MCP_BRIDGE_VERSION : $($ver.Matches[0].Groups[1].Value)  (debe ser 8)"

"--- pack ---"
# No 2>&1 here: AddonBuilder writes its breakpad banner to stderr, and under
# PS 5.1 redirecting a native exe's stderr raises NativeCommandError even on a
# clean exit. The PBO on disk is the verdict, not the stream.
& $builder $stageSrc $outDir -packonly -clear | Out-Null
"AddonBuilder exit  : $LASTEXITCODE"
$pbo = Join-Path $outDir "DayZ_MCP.pbo"
if (-not (Test-Path $pbo)) { throw "AddonBuilder did not write the PBO" }

$h = (Get-FileHash -LiteralPath $pbo -Algorithm SHA256).Hash
$deployed = "P:\Mods\@DayZ_MCP\Addons\DayZ_MCP.pbo"
"pbo      : $pbo"
"bytes    : $((Get-Item $pbo).Length)"
"sha256   : $($h.Substring(0,16))"
if (Test-Path $deployed) {
    "desplegado hoy: $((Get-Item $deployed).Length) B  sha=$((Get-FileHash -LiteralPath $deployed -Algorithm SHA256).Hash.Substring(0,16))"
}
"NO DESPLEGADO: el fichero se queda en $outDir"
