# Run unittest with a fixed interpreter, import root and working directory.
# This keeps discovery and individual modules consistent across caller locations.
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidatePattern('^(tests\.)?test_[A-Za-z0-9_]+$')]
    [string]$Module
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv-mcp\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    [Console]::Error.WriteLine("Test interpreter not found: $python")
    Write-Output 'Tests run: 0'
    Write-Output 'Exit code: 1'
    exit 1
}

$testArgs = @('discover', '-s', 'tests', '-t', '.', '-v')
if ($Module) {
    if (-not $Module.StartsWith('tests.')) { $Module = "tests.$Module" }
    $testArgs = @($Module, '-v')
}

# Read the count from TestResult instead of parsing unittest's console output.
$runTests = @'
import sys
import unittest

program = unittest.main(module=None, argv=['unittest', *sys.argv[1:]], exit=False)
result = program.result
if not result.wasSuccessful():
    exit_code = 1
elif result.testsRun == 0 and not result.skipped:
    exit_code = 5
else:
    exit_code = 0
print(f'Tests run: {result.testsRun}', flush=True)
sys.exit(exit_code)
'@

$previousPythonPath = $env:PYTHONPATH
$exitCode = 1
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:PYTHONPATH = Split-Path -Parent $PSScriptRoot
    & $python -B -u -c $runTests @testArgs
    $exitCode = $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
Write-Output "Exit code: $exitCode"
exit $exitCode
