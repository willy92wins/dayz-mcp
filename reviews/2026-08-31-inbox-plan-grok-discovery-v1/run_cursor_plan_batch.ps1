param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('g2')]
    [string]$Group,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^attempt-[1-9][0-9]*$')]
    [string]$Attempt
)

$ErrorActionPreference = 'Stop'
$repo = 'P:\DayZ_MCP_dev'
$base = Join-Path $repo 'reviews\2026-08-31-inbox-plan-grok-discovery-v1'
$dest = Join-Path $base "runs\$Group\$Attempt"
if (Test-Path -LiteralPath $dest) {
    throw "destination already exists: $dest"
}
New-Item -ItemType Directory -Path $dest -Force | Out-Null

$common = Get-Content -Raw -LiteralPath (Join-Path $base 'requests\common.md')
$groupPrompt = Get-Content -Raw -LiteralPath (Join-Path $base "requests\group-$Group.md")
$prompt = "$common`r`n`r`n$groupPrompt`r`n"
[System.IO.File]::WriteAllText((Join-Path $dest 'prompt.md'), $prompt, [System.Text.UTF8Encoding]::new($false))

$blindWorkspace = Join-Path $env:TEMP ("cursor-grok-plan-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $blindWorkspace | Out-Null
$cursor = Join-Path $env:LOCALAPPDATA 'cursor-agent\cursor-agent.ps1'
$stdout = Join-Path $dest 'stream.ndjson'
$stderr = Join-Path $dest 'stderr.txt'

& $cursor -p $prompt --mode ask -f --trust --workspace $blindWorkspace --add-dir $repo `
    --output-format stream-json --model cursor-grok-4.6-medium 1> $stdout 2> $stderr
$rc = $LASTEXITCODE

$env:PYTHONDONTWRITEBYTECODE = '1'
python (Join-Path $base 'parse_cursor_plan_run.py') --group $Group --attempt $Attempt `
    --group-prompt (Join-Path $base "requests\group-$Group.md") --prompt (Join-Path $dest 'prompt.md') `
    --stream $stdout --stderr $stderr --process-rc $rc --repo $repo --dest $dest
$parseRc = $LASTEXITCODE
Write-Output "group=$Group attempt=$Attempt cursor_rc=$rc parse_rc=$parseRc dest=$dest"
if ($rc -ne 0 -or $parseRc -ne 0) { exit 2 }
