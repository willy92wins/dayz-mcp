# Artefacto integro del worker GX10 (Qwen3.8-Flash-Next), sin editar

Primera pasada del triaje. Los veredictos que esta revision corrigio estan en README.md.

```
T01 | fb-20260920-150408-4f66 | OPEN | - | follow-up to T02; stale pinned worker sha256; no commit names 4f66
T02 | fb-20260919-003752-8620 | OPEN | - | closure-manifest older than edited worker files; no 8620 in history
T03 | fb-20260918-214716-2669 | OPEN | - | oldest of invalid_native_launcher_bundle family; T01 and T02 follow
T04 | fb-20260918-203530-db93 | OPEN | - | MCP reconnects with 18 tools not 46; host connector state
T05 | fb-20260918-203528-9556 | NOT_MCP | - | Claude Code PowerShell guard hook false positive
T06 | fb-20260918-203525-facc | NOT_MCP | - | host Bash tool dumped env instead of running cmd
T07 | fb-20260918-165857-744a | FIXED | fix/744a-stop-cleanup-gone | branch names this short id
T08 | fb-20260918-140351-632e | FIXED | fix/632e-stop-adoptable | branch names this short id
T09 | fb-20260918-134756-c0e5 | OPEN | - | pre-run desktop unlock plus capture probe gate requested
T10 | fb-20260918-134756-e007 | FIXED | 482e30d | documents stdio plan B for vsock McpStartupError
T11 | fb-20260918-134756-05a0 | LIKELY_FIXED | e62da64 | pre-run desktop unlock and brightness gate
T12 | fb-20260918-023443-98f3 | OPEN | - | two night loops deployed PBO from a stale base line
T13 | fb-20260918-023433-d70d | NOT_MCP | - | host Bash heredoc mangles backslashes to python
T14 | fb-20260918-015158-51f6 | NOT_MCP | - | host stop hook leaks into delegated grok worker
T15 | fb-20260918-015151-5ccc | NOT_MCP | - | codex exec hangs on inherited stdin
T16 | fb-20260917-151533-e0f0 | NOT_MCP | - | codex sandbox blocks any dir named dot-git
T17 | fb-20260917-100554-d0e0 | OPEN | - | bare lease_invalid after silent expiry; wrong axis
T18 | fb-20260917-100525-f18d | OPEN | - | no install path exposed; two codes for one cause
T19 | fb-20260917-100452-e5cb | FIXED | d6ac586 | reports sent 0 when notify_players has no clients
T20 | fb-20260917-100436-b286 | FIXED | 3025315 | echo invalid object_inspect type in validation errors
T21 | fb-20260917-100411-5edf | OPEN | - | stale pre-launch peer reported after fresh launch
T22 | fb-20260917-095653-26ac | FIXED | ec55b3b | adds dayz_relevant flag on session_status foreign_ports
T23 | fb-20260917-095637-f8e0 | OPEN | - | stale reason omits the age threshold
T24 | fb-20260917-095637-8011 | OPEN | - | find error tells a prepare step that cannot succeed
T25 | fb-20260917-092919-1bcb | FIXED | 3215660 | adds next_step on ok mutation and session results
T26 | fb-20260917-092908-e951 | OPEN | - | gauntlet report aggregating other filed symptoms
T27 | fb-20260917-092908-baf9 | FIXED | c66db23 | uniform game_not_ready envelope when ready is false
T28 | fb-20260917-092908-2ad1 | FIXED | willy92wins/cursor/progressive-disclosure-2ad1-68db | branch in merge subject names this short id
T29 | fb-20260917-092908-0505 | OPEN | - | can_prepare false yet prepare callable
T30 | fb-20260917-092908-2e3f | DUPLICATE | T22 fb-20260917-095653-26ac | oldest of the foreign_ports noise family
T31 | fb-20260917-092908-5bde | DUPLICATE | T39 fb-20260917-092726-b139 | oldest of the lifecycle_status dead-end family
T32 | fb-20260917-092908-1765 | DUPLICATE | T40 fb-20260917-092711-3485 | oldest of the two-vocabulary not_ready family
T33 | fb-20260917-092908-2492 | FIXED | d28ef93 | put ready first in the bridge_status envelope
T34 | fb-20260917-092858-69d9 | DUPLICATE | T27 fb-20260917-092908-baf9 | oldest of the not_ready envelope family
T35 | fb-20260917-092843-51c9 | DUPLICATE | T28 fb-20260917-092908-2ad1 | oldest of the 56KB catalog disclosure family
T36 | fb-20260917-092825-6a72 | OPEN | - | no knowledge read verb in the exposed registry
T37 | fb-20260917-092811-4ef3 | DUPLICATE | T29 fb-20260917-092908-0505 | oldest of the can_prepare false gate family
T38 | fb-20260917-092743-1432 | DUPLICATE | T22 fb-20260917-095653-26ac | oldest of the foreign_ports noise family
T39 | fb-20260917-092726-b139 | OPEN | - | error points at lifecycle_status which is not exposed
T40 | fb-20260917-092711-3485 | OPEN | - | two vocabularies for one outage; not unified
T41 | fb-20260917-092655-ec73 | DUPLICATE | T33 fb-20260917-092908-2492 | oldest of the buried ready field family
T42 | fb-20260915-151528-6ed1 | OPEN | - | 9 tracked files hold CRLF working copies
T43 | fb-20260915-143332-00bb | FIXED | 31276e7 | skips teleport telemetry precheck without a client peer
T44 | fb-20260915-133831-c586 | NOT_MCP | - | Claude Code background subagent output file empty
T45 | fb-20260915-133820-7b41 | NOT_MCP | - | agy CLI quota 429 and status ERROR reporting
T46 | fb-20260915-133817-dfca | NOT_MCP | - | Cursor Ultra usage limits block two lanes
T47 | fb-20260915-132202-2143 | OPEN | - | player drifts back to teleport point; get-in not_seated
T48 | fb-20260915-113100-160c | NOT_MCP | - | probe says host Bash and PowerShell tools do not steal focus
T49 | fb-20260915-111038-75e7 | OPEN | - | DayZ steals focus on every launch incl background
T50 | fb-20260915-105311-fade | OPEN | - | probe confirms DayZ grabs focus 1 to 2.5 s at launch
T51 | fb-20260915-104637-1004 | DUPLICATE | T60 fb-20260910-102449-f47b | oldest ticket of the frozen/black render family
T52 | fb-20260915-103604-63c9 | FIXED | 9623c11 | packs the git-tracked addon through a verified stage
T53 | fb-20260915-014753-ba11 | OPEN | - | canary: intruder same Steam account stuck in menu
T54 | fb-20260915-014724-b0d9 | OPEN | - | repro follow-up to T60; says it does not close it
T55 | fb-20260915-010502-2084 | OPEN | - | follow-up to T56; restored storage loaded after a gap
T56 | fb-20260914-231736-52c3 | OPEN | - | DayZDiag rejects orderly-saved storage_1 after a gap
T57 | fb-20260914-194728-e4be | OPEN | - | backup prune deletes by path; junction swap hazard
T58 | fb-20260913-143454-8bc6 | OPEN | - | persisted spawned car survives run; object_delete misses it
T59 | fb-20260910-105607-1025 | OPEN | - | bundle rollout windows where every session fails
T60 | fb-20260910-102449-f47b | OPEN | - | oldest report of the frozen-render family; 5872/b0d9 not it
T61 | fb-20260909-110445-1f21 | OPEN | - | Steam remediation reads key not serving state
T62 | fb-20260907-184749-3fc1 | OPEN | - | action_use never completes continuous progress-bar actions
T63 | fb-20260907-133851-f298 | LIKELY_FIXED | d6ddf95 | launch DayZ without taking the foreground
T64 | fb-20260904-200821-dae1 | LIKELY_FIXED | fe86293 | reconcile UNRECONCILED run; names R9 fichas d60f
TOTAL=64
```
