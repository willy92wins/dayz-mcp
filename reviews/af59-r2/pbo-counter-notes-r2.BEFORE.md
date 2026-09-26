# PBO script-entry counter

- Integrate `ws/pbo-counter.txt`; call `Get-PboScriptEntryCount -Path $pbo`.
- Returns an Int64 count. Counts only entry names ending in `.c`, case-insensitive.
- Reads the full header table and the optional first Vers property block.
- Validates NUL strings, complete metadata, zero terminator, and total stored payload bounds.
- Uses stored data sizes, not original sizes; does not scan or decompress payload.
- Allows trailing checksum bytes. Does not validate checksums or packing methods.
- Throws on invalid input; the caller must treat exceptions as build rejection.
- ASCII and PowerShell 5.1 syntax. Runtime validation was not performed here.
- Existing seven-case harness writes arbitrary non-PBO bytes. It must use real PBO fixtures before testing this counter.
