$ErrorActionPreference = 'Stop'
function Get-PboScriptEntryCount {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    # PBO headers are NUL-terminated names followed by five little-endian DWORDs.
    # File bodies follow the complete header table, not their individual entries.
    function Read-PboString([IO.BinaryReader]$Reader) {
        $text = New-Object Text.StringBuilder
        while ($true) {
            if ($Reader.BaseStream.Position -ge $Reader.BaseStream.Length) {
                throw 'Truncated PBO string.'
            }
            $value = $Reader.ReadByte()
            if ($value -eq 0) { return $text.ToString() }
            [void]$text.Append([char]$value)
        }
    }

    $stream = $null
    $reader = $null
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $reader = New-Object IO.BinaryReader($stream)
        [long]$payloadBytes = 0
        [long]$count = 0
        $first = $true
        while ($true) {
            $name = Read-PboString $reader
            if (($stream.Length - $stream.Position) -lt 20) {
                throw 'Truncated PBO entry.'
            }
            $method = $reader.ReadUInt32()
            $originalSize = $reader.ReadUInt32()
            $reserved = $reader.ReadUInt32()
            $timestamp = $reader.ReadUInt32()
            $dataSize = $reader.ReadUInt32()

            if ($name.Length -eq 0) {
                if ($method -eq 0x56657273 -and $first) {
                    # Optional Vers entry, then key/value strings and an empty key.
                    if ($originalSize -ne 0 -or $reserved -ne 0 -or
                        $timestamp -ne 0 -or $dataSize -ne 0) {
                        throw 'Invalid PBO Vers entry.'
                    }
                    while ($true) {
                        $key = Read-PboString $reader
                        if ($key.Length -eq 0) { break }
                        $null = Read-PboString $reader
                    }
                    $first = $false
                    continue
                }
                if ($method -ne 0 -or $originalSize -ne 0 -or $reserved -ne 0 -or
                    $timestamp -ne 0 -or $dataSize -ne 0) {
                    throw 'Invalid PBO header terminator.'
                }
                break
            }
            if ($method -eq 0x56657273) { throw 'Invalid named PBO Vers entry.' }
            $first = $false
            # Bound each addition before summing, including on very large files.
            if ([long]$dataSize -gt ($stream.Length - $payloadBytes)) {
                throw 'PBO payload exceeds file bounds.'
            }
            $payloadBytes += [long]$dataSize
            if ($name.EndsWith('.c', [StringComparison]::OrdinalIgnoreCase)) { $count++ }
        }
        if ($payloadBytes -gt ($stream.Length - $stream.Position)) {
            throw 'Truncated PBO payload.'
        }
        # Skip stored data, including compressed data. A checksum trailer may follow.
        [void]$stream.Seek($payloadBytes, [IO.SeekOrigin]::Current)
        return $count
    }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
    }
}

$count = Get-PboScriptEntryCount -Path (Join-Path $PSScriptRoot 'fixtures\DayZ_MCP_fence_DCC8730F.pbo')
if ($count -ne 9) { throw 'Unexpected real PBO count' }
Write-Output "R2_REAL_PBO_COUNT=$count"
