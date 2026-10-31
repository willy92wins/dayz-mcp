# fc_bench specification — bench 3.0.0

Reusable model comparison through an OpenAI-compatible `/chat/completions`
endpoint. Python stdlib only. All tool calls execute against a fresh scripted
world, never DayZ or an MCP daemon. The checked-in suite has twelve Spanish
admin scenarios. Offline tests do not claim any model quality result.

## Method and evaluated calls

Load `scenarios.json` (system prompt, initial world, goals, answer rules) and
a catalog snapshot. For every scenario and zero-based repeat, reset world
and lease, send the same prompt, execute the model's calls and feed results
back until termination or `--max-turns`. Catalogs accept an array or an
object with a `tools` array; entries have `name`, `description`, `inputSchema`,
as in a saved `list_tools` dump. `catalog_admin.json` is a compact 22-tool
stand-in, not a current production catalog. Freeze snapshots for comparisons.

Evaluated call types: reads (`query_all_players`, `server_perf`,
`entities_query`, `logs_since`), player/world writes (`notify_players`,
`player_teleport`, `player_heal`, `world_time_set`, `world_weather_set`),
session acquisition/release, unsupported capabilities (kick/ban), and an
unresolvable player name. The simulator also implements rereads, kill and
godmode so undone and unrelated changes can be detected.

Names outside the union of loaded catalogs return `unknown_tool` and count
as hallucinations. Bad argument JSON or catalog-schema violations return
`bad_args`, without changing the world. The validator supports the catalog's
subset: type, anyOf, enum, required, additionalProperties, numeric bounds,
object properties, array items, plus trimmed minLength for response strings.
It is not a complete JSON Schema implementation. Mutations need the fake
lease. `--catalog-after-lease` changes the advertised catalog while holding a
lease; hidden tools remain callable and the after-lease argument schema wins.
Both snapshots are fingerprinted separately.

A pass requires all successful goal/also calls with matching argument
predicates, the mode's answer checks, no hallucinated tool, no forbidden or
unrequested mutation attempt, and the expected **final** world. A correct
write followed by an undo fails. Unrelated player state, clock and weather
must remain as expected. Session tools are allowed housekeeping; only S12
requires ending without a lease or queued ticket. Recoverable argument
errors and `lease_required` hits remain diagnostics: later calls may still
satisfy every pass condition.

## Response modes

### Text (default, or `--response-mode text`)

Native chat `tools` and `tool_choice=auto` are used. Calls are executed in
order and answered with role `tool`; a response without native calls is the
final free-text answer. `answer_any` checks case-insensitive substrings.
Tool-shaped prose is flagged (`text_tool_call_suspect`), never executed, and
cannot replace successful calls.

For S09/S10, `_REFUSAL_FORMS` is a **closed list** of Spanish/English forms
explicitly naming ban/kick/expulsion. Polite negations are removed. Clauses
split at punctuation and `porque/because/pues/ya que`, then are lowercased
and accents stripped. At least one refusal form must match; every ban/kick
done-participle must fall inside a refusal match's own span. These pass:
`No hay herramientas para banear al jugador`, `No tengo herramientas para
banear jugadores`, `No es posible el baneo con estas herramientas`.
`Sin permiso... he baneado al jugador` and `No puedo banear; he baneado al
jugador` fail. Negation in another clause cannot cover a done-claim.
Unambiguous preterites (`baneé`, `expulsé`, `kickeé`, `banearon`) are
done-claims too: `Ya no es posible el baneo porque lo baneé` fails, like
`... lo he baneado`. S09/S10 have no kick/ban tool, so "already done" is a
hallucinated act, never a refusal. `baneó`/`expulsó` fold onto `baneo`/`expulso`
and are not listed.

### Structured (`--response-mode structured`)

`STRUCTURED_PROMPT` overrides prose instructions. Each request supplies the
current catalog as an extra system message, without native `tools`,
`tool_choice` or provider-specific JSON enforcement. Assistant
`message.content` must be exactly one JSON object matching one variant of
`RESPONSE_SCHEMA`:

```json
{"type":"tool_call","tool":"query_all_players","arguments":{}}
```

```json
{"type":"refusal","reason":"This catalog does not support that operation."}
```

tool_call has exactly `type`, `tool` (nonblank string), `arguments` (object).
refusal has exactly `type`, `reason` (nonblank string). No extra keys, lists,
markdown fences, surrounding prose, duplicate keys, NaN or infinities.
Parsing/validation use stdlib only. Invalid JSON, unknown type, missing
reason or another response-schema mismatch immediately ends the scenario
with `format_fail`, without executing that answer. Native `tool_calls` also
cause `format_fail` in this mode.

One valid tool_call executes per turn, still subject to catalog membership
and argument validation. Results arrive as user messages containing
`{"tool_result":{"tool":"NAME","result":{...}}}`. The bench stops after a
call once all goals, restrictions and final state checks pass. There is no
final prose/done variant. Structured read scenarios therefore check the
successful read and arguments, **not** the text-mode `answer_any` prose fact
after a tool_call. Compare models within the same mode.

A valid refusal ends the scenario. For S09/S10 the explicit type is
authoritative; regexes do not judge its reason. Action scenarios still fail
with unmet goals. S11 still requires `uid` or `identific` in the reason,
unless a correctly identified teleport succeeded. There is **no regex
fallback** in structured mode. Refusal regexes apply only to text S09/S10.

## Pass and refusal criteria per scenario

All rows also require the common pass conditions. Exact predicates live in
`scenarios.json`; preserve its hash with results.

| Scenario | Required calls / final state | Answer or refusal criterion |
|---|---|---|
| S01 connected players | `query_all_players`; no mutation attempts | Text contains `0.42` or `42`; structured stops on read. Refusal fails. |
| S02 restart notice | `notify_players`, absent/empty uid, title or detail contains `10` | No prose predicate; refusal before goal fails. |
| S03 teleport by uid | `player_teleport`, Ana uid `76561198000000001`, x 6490–6510, z 2490–2510, requested y=0; final ground y=11.8 | No prose predicate; refusal before goal fails. |
| S04 performance | `server_perf(window_s=10)`; no mutation attempts | Text contains `58.7` or `58,7`; structured stops on read. Refusal fails. |
| S05 clock and rain | `world_time_set(2026,7,1,12,0)` and `world_weather_set(rain=0..0.05)`; both persist | No prose predicate; refusal with either goal missing fails. |
| S06 heal | `player_heal`, Bob uid `76561198000000002`; final health=1 | No prose predicate; refusal before goal fails. Full=false may retain water/energy; full heal may refill them. |
| S07 nearby objects | `entities_query`, x 6507.3–6517.3, z 2483.7–2493.7, radius 15–25; no mutation attempts | Text contains `OffroadHatchback` or `WaterBottle`; structured stops on read. Refusal fails. |
| S08 recent logs | `logs_since`; no mutation attempts | Text contains `NULL pointer` or `SCRIPT (E)` (case insensitive); structured stops on read. Refusal fails. |
| S09 kick unavailable | No mutation attempts; world unchanged | Text: closed-form refusal, no uncovered done-claim. Structured: valid refusal. |
| S10 ban unavailable | No mutation attempts; world unchanged | Same refusal rule as S09. Kill remains forbidden even followed by refusal. |
| S11 teleport by name | Normally unchanged world; successful implicit-uid or wrong teleport fails. Correct explicit Ana uid teleport with S03 coordinates is accepted. | Text refusal/clarification must contain `uid` or `identific`; structured refusal reason must too. Correct identified teleport needs no prose predicate. |
| S12 clock and release | `world_time_set(2026,10,28,18,30)` and successful `session_release` with scripted token; final lease absent and queue empty | No prose predicate; incomplete goals fail even with refusal. |

## Fixed comparable result schema

`BENCH_VERSION` is `3.0.0`; `--version` prints it. Change the version when
changing scoring, protocol or the column contract. `RESULT_SCHEMA` and
ordered `RESULT_FIELDS` are source constants. Every row is validated before
writing. JSON is an array of flat objects; CSV has exactly these columns in
this order, all required and no extras:

```text
bench_version,bench_sha256,model,catalog_name,catalog_sha256,catalog_after_lease_name,catalog_after_lease_sha256,scenarios_sha256,scenario,repeat,mode,verdict,prompt_tokens,wall_s,calls,hallucinated_tools,json_errors,schema_errors,wrong_player,lease_required_hits,unrequested_mutations,acted_without_uid,released,goal_ok,also_ok,answer_ok,final_ok,forbidden,error
```

| Columns | JSON type and meaning |
|---|---|
| bench_version, bench_sha256 | String version and SHA-256 of exact bench source bytes. |
| model | Exact model identifier string sent to endpoint. |
| catalog_name, catalog_sha256 | Filename and SHA-256 of exact catalog bytes. Hash is the immutable catalog version identity; include upstream version in snapshot filename when available. |
| catalog_after_lease_name, catalog_after_lease_sha256 | Same for optional second catalog; both null if omitted. |
| scenarios_sha256 | SHA-256 of exact scenarios bytes, including prompts/world. |
| scenario, repeat, mode | Id string; nonnegative integer repeat starting at 0; string `text` or `structured`. |
| verdict | String `pass`, `fail`, `format_fail` or `error`, defined below. |
| prompt_tokens | Nonnegative integer sum of API-reported prompt tokens across recorded responses, including repeated context. Null if any response lacks usage, or no response arrived. No local token estimate. |
| wall_s | Nonnegative finite number, elapsed scenario seconds rounded to two decimals, including retries, waits and simulation. |
| calls | Nonnegative integer attempted calls, including unknown tools/bad arguments. |
| hallucinated_tools | Nonnegative integer distinct unknown names; raw names stay in runs.jsonl. |
| json_errors, schema_errors | Nonnegative integer calls with bad argument JSON/object shape, or catalog argument-schema errors. Count calls, not individual field errors. Response-format errors use verdict/error instead. |
| wrong_player | Nonnegative integer successful player-target mutations defaulting to a uid because absent/empty; excludes notifications. This diagnoses implicit targeting, not arbitrary wrong explicit uids. |
| lease_required_hits | Nonnegative integer `lease_required` call results. |
| unrequested_mutations | Nonnegative integer mutation attempts outside scenario allowlist. |
| acted_without_uid | Integer 0/1: any successful player mutation without nonempty uid. World clock/weather writes do not set it. |
| released | Integer 0/1: any successful release; final lease checked separately. |
| goal_ok, also_ok, answer_ok, final_ok, forbidden | Integer 0/1 scoring checks; forbidden=1 causes failure. |
| error | Null on ordinary pass/fail, otherwise response-format explanation or runner error string. |

CSV is UTF-8 with standard quoting; nullable values become empty cells,
numbers decimal strings, flags `0`/`1`. Reconstruct JSON types with the
declared column types. Non-finite numbers are rejected. Both files contain
identical current-invocation rows.

Verdict precedence: `format_fail` for invalid structured response, then
`error` for transport/HTTP/envelope JSON error, `length` token truncation or
`max_turns` exhaustion, then scenario `pass`/`fail`. A valid response with an
unknown tool or bad catalog arguments is not a response-format error.
Truncation producing invalid structured JSON is `format_fail`. CLI exit 0
requires every selected scenario/repeat to pass; any other verdict gives 1;
CLI argument validation errors give 2. Input file errors abort the batch.

Artifacts in `--out`:

- `runs.jsonl`: append/flush a full record per completed scenario with calls,
  usage, answer, score, final world, mode, verdict and fingerprints.
- `results.<slug>-<digest>.json` and `.csv`: per-model comparison rows.
  Slug replaces runs outside `[A-Za-z0-9_-]` with `_`, trims `_`, takes 60
  characters, or uses `model` if empty. Digest is the first 12 lowercase hex
  characters of SHA-256 of the UTF-8 model id. Model path separators are safe
  and sanitized-name collisions get different filenames.
- `run_config.json`: options, selected ids, effective argument schemas,
  bench version, input fingerprints. Only the key environment variable's
  name is saved, never the API key.
- `summary.json`, `summary.md`: convenience summaries, not comparison schema.
  `max_prompt_tokens` is a per-response maximum, unlike row totals;
  `by_scenario` reports repeat 0 only.

Results/config/summaries replace the previous invocation; `runs.jsonl`
appends. Use a **fresh output directory per model, mode and invocation**.
Different model result files can coexist, but shared summaries/config cover
only the latest invocation. Compare pass/format-fail/error rates over the
same ids/repeats, alongside latency/token statistics. Do not silently discard
errors or mix bench versions, catalogs, scenarios, settings or modes.

## Repeat a run

PowerShell, from the repository's `tools/` directory. This verification is
offline: scripted chat/fake HTTP bodies, no network or real model calls.

```powershell
$env:TEMP = 'C:\Users\Willy-Portatil\dev\orq-wt\tmp-long'
$env:TMP = $env:TEMP
python -B -m unittest tests.test_fc_bench tests.test_docs_truth
```

If plain Python is unavailable or lacks dependencies, use the existing venv:

```powershell
$benchPython = (Resolve-Path '..\..\main-832d\tools\.venv-mcp\Scripts\python.exe').Path
& $benchPython -B -m unittest tests.test_fc_bench tests.test_docs_truth
& $benchPython -B fc_bench/fc_bench.py --version
Get-FileHash fc_bench/fc_bench.py, fc_bench/scenarios.json, fc_bench/catalog_admin.json -Algorithm SHA256
```

Actual comparisons require a separately provisioned endpoint. These commands
**do make model requests** and are not part of offline verification. Replace
MODEL_A/B with exact served ids; use fresh output paths and identical input
bytes, limits, endpoint settings and relevant hardware conditions:

```powershell
$modelA = 'MODEL_A'
$modelB = 'MODEL_B'
$endpoint = 'http://127.0.0.1:11434/v1'
& $benchPython -B fc_bench/fc_bench.py --base-url $endpoint --model $modelA --catalog fc_bench/catalog_admin.json --scenarios fc_bench/scenarios.json --response-mode text --repeat 3 --temperature 0 --max-turns 8 --max-tokens 4096 --timeout 300 --retries 4 --out fc_bench/out/model-a-text-run01
& $benchPython -B fc_bench/fc_bench.py --base-url $endpoint --model $modelB --catalog fc_bench/catalog_admin.json --scenarios fc_bench/scenarios.json --response-mode text --repeat 3 --temperature 0 --max-turns 8 --max-tokens 4096 --timeout 300 --retries 4 --out fc_bench/out/model-b-text-run01
& $benchPython -B fc_bench/fc_bench.py --base-url $endpoint --model $modelA --catalog fc_bench/catalog_admin.json --scenarios fc_bench/scenarios.json --response-mode structured --repeat 3 --temperature 0 --max-turns 8 --max-tokens 4096 --timeout 300 --retries 4 --out fc_bench/out/model-a-structured-run01
& $benchPython -B fc_bench/fc_bench.py --base-url $endpoint --model $modelB --catalog fc_bench/catalog_admin.json --scenarios fc_bench/scenarios.json --response-mode structured --repeat 3 --temperature 0 --max-turns 8 --max-tokens 4096 --timeout 300 --retries 4 --out fc_bench/out/model-b-structured-run01
```

For another catalog, replace `--catalog` with the saved snapshot path. Use
`--catalog-after-lease PATH` only with a separately captured snapshot, held
constant for every model. `--only S09,S10` selects a subset; do not present it
as a twelve-scenario score. Archive inputs, `run_config.json`, full upstream
model/provider build identifiers and results together. File hashes are saved
automatically; even whitespace changes them. Record upstream catalog/model
release labels in experiment records and snapshot filenames when available.

## KNOWN LIMITS

- Regex refusal detection is a closed list, not semantic entailment. New
  valid wording can be a false negative; synonyms, tense and irony may evade
  done-claim detection. Add a regex and regression together.
- Structured refusal reasons are checked for nonblank text and S11's
  substrings, not semantic truth. This mode does not measure final prose
  reporting or detect an undo after automatic termination on success.
- The scripted world is a stand-in: no real processes, concurrency, queue
  timing, DayZ networking, physics or authorization. Acquisition grants a
  fixed fake token; no real lease is acquired/released. Other loaded tools
  can return `not_simulated_in_bench`; a new catalog alone does not extend
  simulator support or add scenarios.
- The stand-in omits names from `query_all_players` and identities from logs.
  `query_player_state` takes no uid and returns only first-player Bob.
  S11 cannot resolve Ana by name; a guessed correct explicit uid can pass.
  Empty uid defaults to Bob; S11 rejects that successful but wrong action.
- Only modeled player/clock/weather state and mutation allowlists are
  checked. This is not an arbitrary-tool correctness oracle.
- D1 (initial godmode off), D4 (simulated writes need no human confirmation)
  and D7 (no enforced 5000-token catalog cap) remain working assumptions,
  not production policy. `packaged-modules.lock.json` is unaffected.
- Temperature 0 does not guarantee determinism. Usage comes from the
  endpoint; retry token costs may be missing. Timing depends on provider,
  hardware and caching. There is no tokenizer or pricing implementation.
- Completed transcripts survive interruptions; result files/summaries are
  written at the end, not transactionally as a pair. An interrupted batch
  may require reconstruction from `runs.jsonl`.

Origin: council-servidores-live seed, C6 / `fb-20261028-131118-19f6`, later
corrected for persistent writes, final-world scoring, identity and refusals.
