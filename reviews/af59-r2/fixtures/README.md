# Real PBO fixture (read-only input)

Byte-identical copy of ../2026-08-19-lane-fence/DayZ_MCP_fence_DCC8730F.pbo
(relative to reviews). SHA-256:
`dcc8730feb98ff2a4f7c203075d799428c53a669360e3499311aa3b4d31afed3`.
205008 bytes, 13 entries, 9 scripts, 705 header bytes, 204282 payload bytes,
21-byte trailer. SHA-1 of all bytes preceding the trailer matches the trailer.
The fixture gate verifies its SHA-256 before handing these exact bytes to the stub.

Provenance read in this lane:
- PROJECT-MAP.md:17 names this as the last built PBO.
- reviews/2026-08-19-lane-fence/MANIFEST.md:22 records bytes and hash prefix.
- reviews/2026-08-19-lane-fence/TANDA-INGAME.md:22-23 records 13 entries.
- reviews/2026-08-19-lane-fence/tools/build_fence_pbo.ps1:15 and :56 name
  AddonBuilder.exe and invoke it with -packonly -clear.
- reviews/2026-08-19-lane-fence/tools/verify_pbo.py:19-44 reads its directory.

This lane did NOT run AddonBuilder. The provenance is the existing recorded build,
not a new build. This is a real packonly PBO, not proof of coverage for a binarized
asset PBO. Frozen entry inventory: real-pbo-inventory.json. S14 asserts exactly
9/9 using the complete fixture, including Vers properties and SHA-1 trailer.
Source stubs only provide a count threshold; S14 does not assert payload identity
against those stubs or validate their scripts in the engine.
