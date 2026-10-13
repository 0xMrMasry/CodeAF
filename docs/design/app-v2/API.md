# codeaf v2 app API — the local API the binary exposes

Status: A1 transport, A2 key handoff, A3 event stream, A4a agent lifecycle, A4b turn core, A5 tasks/quick tasks, A6 processes, A9 Inbox, A10a crews/members, A10b roles/sub-crews/reports/items/log, A11a settings/setup, A11b spending/memory, A12 search/history/Activity, A14 factories/power/hand-off, A15 shared rules/TUI seat rename and A16 published TypeScript contracts built, 2026-10-13; factory deltas F-1–F-7 built on Contract 2; remaining app lanes have reserved Go contracts and authenticated 501 stubs, not behaviour. Branch `app/v2`.

Design source: codeaf-desk `main`, `src/design/source/text/` (S0, S1, S2, X1–X5, U1–U7). When this file and a board disagree, the board wins, and this file changes in the same PR.

Code references:
- `v2:` is `origin/app/v2` (e417c1275).
- `wb:` is `origin/work/8f3c2a9d`, the desktop bridge that another session owns. We read it and port from it. We never push to it.

Status words:
- **EXISTS** means `app/v2` already serves the route or call over HTTP.
- **PORT** means the code is in `wb:internal/desktopbridge`. We copy it into `app/v2` and adapt it.
- **BUILT** records a lane's shared code or policy; route enforcement is stated separately.
- **NEW** means nobody has written it yet. Where a feature is "wire-backed", the engine already has the behaviour behind an `internal/remote` wire method and only needs an HTTP face.

---

## 0. The rule this API follows

The binary owns three things: all state, every AI call and every numbered rule. The app owns the UI and the state that only the UI needs:
- windows, tabs, panes, tab groups, the closed-tab stack and saved views
- Inbox **Later** and **Done today**, notifications, the folding of banners, and the Dock badge
- UI-only settings: theme, tint, text size, open at login, banners, sound, badge, density, auto-update
- undo for tidy actions until the app quits, and the app's own record of tidy actions

The app never asks a model for anything except through the **AI door** (§2.20). The binary routes that call through its own model routing and spend ledger, and the app keeps its own prompt text.

This shifts some objects that S2 lists under `app.db`. Agents, tasks, quick tasks, processes, standing orders, crews, members, roles, memory, the spending ledger and non-secret engine settings live in the **binary's home**, not in the app's SQLite. Inbox items split: the binary derives the waiting set, and the app keeps each item's Later/Done state for each person.

The API speaks the S0 glossary: agent, task, process, standing order, crew, member, runner, Activity, rounds, approval. Go internals keep their names, and the codec is the one place that maps between the two. This is the same rule the factory follows in `v2:docs/design/factory/ARCHITECTURE.md` §2.

---

## 1. Transport and process model

### 1.1 One engine process per user

```
 codeaf.app (Tauri)                         codeaf (bundled binary)
 ┌──────────────────────┐   spawn, stdin    ┌──────────────────────────────────────────┐
 │ Rust core            │ ────────────────► │ codeaf app serve                         │
 │  keyring (Keychain / │ ◄──── stdout ──── │  /app/v1/*   engine API (this file)      │
 │  Secret Service)     │   one hello line  │  /v1/*       solo factory (in-process)   │
 │ WebView ── fetch/SSE ┼──── 127.0.0.1 ───►│  /app/v1/events  one typed SSE stream    │
 └──────────────────────┘   bearer token    │  worker loop → solo or team factory      │
                                            │  child: codeaf engine (session host)     │
                                            └──────────────────────────────────────────┘
```

- **Command.** The app runs `codeaf app serve --home <dir> --listen 127.0.0.1:0 --parent-pid <pid>`. **EXISTS (A1)**: `cmd/codeaf/app_serve.go`, ported from `wb:cmd/codeaf/desktop_bridge.go` (the `desktop-bridge` command).
- **Home.** The app and TUI share the binary's state root: `~/.codeaf`, respecting `CODEAF_HOME`. An explicit `--home` overrides it for a disposable or named home. **EXISTS:** `cmd/codeaf/app_serve.go` uses `internal/home.Dir`, resolves an absolute path and sets `CODEAF_HOME` before any engine writer starts.
  - Agents, history, crews and non-secret settings are shared. The app's UI-only `app.db` remains in its platform app-data folder; the binary does not use `<app-data>/engine`.
  - The solo factory's JSON store remains `<home>/v3/factory`. Existing document flocks, conversation locks and presence coordinate concurrent app and TUI access.
  - One process holds `<home>/v3/factory/worker.lock` until all its job slots stop. A second process uses the same factory and saved `worker.json` credentials without registering more slots; it waits and takes over after the owner exits. Credential updates use a shared file lock and private atomic replacement. `CODEAF_FACTORY_BENCHES` bounds the home's jobs, rather than multiplying capacity per window.
- **Listener.**
  - The listener is a loopback TCP port, picked by the OS. It is not a unix socket, because WKWebView/WebKitGTK `fetch` and `EventSource` cannot dial a socket. A socket would force a Rust proxy for every byte of SSE.
  - The bridge refuses non-loopback addresses (`wb:cmd/codeaf/desktop_bridge.go:47-54`). We keep that.
- **Hello.**
  - The binary writes exactly one JSON line to stdout: `{"url":"http://127.0.0.1:53021","token":"<64 hex>","pid":1234,"codeaf_version":"…","contract":1,"home":"…","epoch":"<64 hex>"}`. Then stdout is silent. Logs go to stderr and into `<home>/logs/app-serve.log`. The epoch is fresh and independent of the token.
  - This shape comes from `wb:desktop_bridge.go:230`, which prints `{url,token,model}`.
- **Token.**
  - The token is 32 random bytes, freshly made at each launch and held only in memory. It is never written to disk.
  - Every route needs `Authorization: Bearer <token>`, compared in constant time. **EXISTS (A1)**, ported from `wb:internal/desktopbridge/bridge.go:228,601`.
  - An allowed-origin `OPTIONS` preflight needs no token and invokes no resource handler. Actual requests always need the bearer header; A1 accepts no query token.
  - `EventSource` cannot set headers. The Rust core therefore opens the SSE stream and forwards events over a Tauri channel. The alternative is `?access=<token>` on `/app/v1/events` only, as a one-route exception (§3).
- **Origin and Host guard.** **EXISTS (A1)**, ported from `wb:origin.go:29-37`:
  - A foreign `Origin` gets 403. A non-loopback `Host` gets 421, which defeats DNS rebinding.
  - Allowed origins are `tauri://localhost`, `http://tauri.localhost`, `http://localhost:1420` and `http://127.0.0.1:1420`. A request without Origin (the Rust core) still needs the token. Empty or repeated Origin headers are refused. Host must be a loopback IP or `localhost` with a numeric port from 1 through 65535.
- **Loopback is not trust.** On Linux, other local users can reach 127.0.0.1. The in-process solo factory therefore runs with `Access: "token"` and accepts the same per-launch token as its host token. Today the factory treats loopback as the owner (`v2:internal/factory/serve/access.go`). That is a factory delta (§2.22, F-1).
- **Life.**
  - The binary exits when stdin reaches EOF or when `--parent-pid` dies. A crashed app therefore never leaves an engine running.
  - Running processes stop on exit (X1: processes stop when the app quits). That is why the app asks first on Quit, using `GET /app/v1/processes?state=running`.
  - Before it quits, the app calls `POST /app/v1/shutdown {reason}`. That stops processes, writes a receipt for each one and flushes the ledger.
  - **A1 status:** stdin EOF, parent death (checked at launch and every 1 s), SIGINT, SIGTERM and authenticated shutdown end the HTTP server. `--parent-pid 0` disables parent checking, never stdin life. Shutdown flushes `{ok:true}` before stopping, allows up to 3 s for HTTP requests to finish, then closes remaining connections. Process receipts and ledger flushing arrive with their owning lanes; A1 starts no processes or agents. A2 parses the first stdin JSON line into memory and drains the remaining lifetime pipe without logging. An invalid or oversized handoff ends the launch without echoing its contents.

### 1.2 Key handoff (never disk)

The app reads the secrets from the Keychain (macOS) or Secret Service (Linux), using the `keyring` crate the desk already uses (`codeaf-desk:src-tauri/src/attach.rs`). It hands them over in one of two ways:

1. **At launch, on stdin.** Right after the spawn, the app writes the first stdin line: `{"keys":{"openrouter":"sk-or-…","anthropic":"…","github":"ghp_…"},"factory_sessions":{"https://factory.acme.internal":"<session token>"}}`. Then the stdin pipe stays open as the life line (§1.1).
2. **Later, by an authenticated call.**
   - `PUT /app/v1/keys/{provider}` with `{key}` returns `{ok, models[], error?}`. It runs the "one check call" from J01 step 2 and verifies the key there.
   - `DELETE /app/v1/keys/{provider}` removes a key.
   - The app writes the Keychain first, then calls.

Rules (**EXISTS, A2**, in `internal/config` and `internal/appapi`):
- The binary holds keys in a process-memory credential source. This source outranks `OPENROUTER_API_KEY` and the profile `api_key` (today's order is in `v2:internal/config/apikey.go:25,62`).
- App mode is established by `app serve` even without `--home`; child commands inherit `CODEAF_APP_MODE=1` (policy only). Any key update through the profile/settings/provider writers is refused with "keys are kept by the app", including `codeaf connect`, `codeaf disconnect`, GitHub token writes and automatic environment-key persistence. Unrelated settings still save and preserve existing terminal credentials in the shared profile.
- Child engine processes get one credential snapshot through an inherited pipe (`--keys-fd 3`, or the next free inherited descriptor), never through argv or the environment. `config.PrepareAppCredentialPipe` wires local child launches; `enginehost.Spawn` uses it. The engine consumes that line before resolving any session credentials. App children use their own session pipe rather than attaching to a terminal host with a different credential lifetime.
- `GET /app/v1/keys` returns only `{provider, present, last4, models[]}`, as in U1-03 ("sk-or-v1-••••a91f").
- Keys never appear in logs. Both `redact` and `trace` register exact provider and factory-session values, including opaque tokens, before any provider response can be recorded. GET shows only memory-source presence, a four-character tail (empty for keys of four characters or fewer), and model ids from successful PUT checks; stdin ingestion makes no network call and initially has no model listing.
- PUT makes exactly one authenticated listing call with a 10 s ceiling. OpenRouter uses `/models/user`, because `/models` is public; GitHub checks `/user` and returns no model ids; Anthropic uses its native `/v1/models` authentication. Other known model services use their resolved `/models` endpoint. Unknown providers and Codex browser credentials are refused here; browser sign-in is A11a. A failed check is HTTP 422 `{ok:false, models:[], error, reason:"bad_input", request_id}`, quotes the provider's message and status with secrets removed, and leaves the previous key intact. No answer also leaves it intact. Redirects are not followed. DELETE installs an empty memory entry so shell/profile fallback cannot reconnect the removed key.
- Launch and child handoffs accept at most 1 MiB, one JSON object on one line with `keys` and `factory_sessions`; remaining stdin bytes only hold the launch alive. Factory-session tokens remain memory-only for the factory lane to consume.

### 1.3 Restart and reattach

- Agents survive an engine restart because their transcripts are on disk. Connection ids last for one engine lifetime. The stable id is the agent `id` (§2.1), not the connection.
- After a restart the app re-reads the `hello`, calls `GET /app/v1/agents`, and reconnects the stream with `Last-Event-ID`. An unknown cursor answers with `reset` (§3).
- Agents that were running when the engine died come back `idle` with a receipt "Stopped · codeaf restarted". Processes come back under Ended with state `stopped`.
- The design's "Mac asleep" banner (U2, U3) needs power events from the app. `POST /app/v1/power {state: "sleeping"|"awake"}` (A14) does three things:
  - It stops processes and writes the "stopped while this Mac slept · Rerun" notices.
  - It marks this Mac's runner `asleep` in the factory.
  - Standing-trigger accounting belongs to the standing-order resource; this route forwards runner presence and records process sleep receipts.
- On Linux the app learns of sleep from logind's `PrepareForSleep`. On macOS it uses `NSWorkspaceWillSleepNotification`.

### 1.4 Factories

- **Solo (S1 "runs inside the app").** `app serve` creates the factory in-process through `appFactoryHost.solo`, using the same store, seam, rule loader and GitHub policy doors as the TUI; `ServedBy` is `"app"`. It mounts that factory's `serve.Handler` at `/v1/*` on the same listener. Its worker loop attaches over the in-process loopback as today (`ARCHITECTURE.md` §6).
  - If no factory is set up, `/v1/*` answers 404 `{reason:"no_factory"}`.
  - `POST /app/v1/factories/solo` creates the factory: it writes `serve.json` and starts the service. This is the J01 / V46 "Start a factory on this Mac" step.
- **Team (attach over HTTPS).**
  - The app keeps the factory session token in the Keychain, as the desk does now, and hands it over in `factory_sessions` (§1.2).
  - The binary writes `attach.json` (connection URLs and modes only, no token) through `POST /app/v1/factories {url}` and runs its worker loop against that URL with the person's keys.
  - The app reads team pages (Jobs, Repos, People, Activity) **directly** from the team `/v1` over HTTPS, as `codeaf-desk` does today. The binary also subscribes to each factory's `/v1/events` with the person's session, so it can merge the person's factory needs-you items into the one Inbox (§2.13).
- `GET /app/v1/factories` returns `[{id, url, name, mode: "solo"|"team", operator, state: "ok"|"unreachable"|"signed_out", last_seen}]`. It feeds the rail rows (S1: "several factories may be connected; each is a rail row").
- `DELETE /app/v1/factories/{id}` is Leave factory….

---

## 2. Resources

Conventions:
- JSON, snake_case. These match `/v1`.
- Errors are `{error, reason?, request_id}`. `reason` is one of `not_found`, `conflict`, `stale`, `bad_input`, `not_allowed`, `at_cap` or `no_factory`.
- Every POST accepts `Idempotency-Key` (reuse `v2:internal/factory/serve/idempotency.go`).
- Every mutating verb accepts `expect: {state?, version?}`. A mismatch is 409 `stale`, with the sentence. This is the factory rule.
- Ids are opaque strings. Agent ids are stable across restarts: the transcript's uuid, not the bridge connection id.
- Money is `{usd: "4.20"}`, a string with two decimals.
- Every list route accepts `?q=` with the token grammar of X3/X4 (`state:` `kind:` `since:` …). Matching is plain text and fields, with no AI.

### Contract reservations and schemas (A4b–A16)

Every app route below is registered. A NEW or PORT resource route whose lane has
not landed returns HTTP 501 `{"error":"not built yet","route":"METHOD /app/v1/…"}`.
`route` names the method and canonical path template, including `{id}` placeholders.
Authentication and the Origin/Host guard run before that response; a wrong method
returns 405 with `Allow`. Existing transport, key and agent lifecycle handlers keep
serving their current responses. A4b serves the full `AgentSnapshot` shape with app-coded entries, pending
messages, live asks, task summaries and usage; reserved files remain separate.

`internal/appapi/*_wire.go` owns the JSON types, one file per resource.
`*_handlers.go` owns that resource's route table and one handler entry point.
A resource lane replaces its handler and clears `reserved` in that file; the
contract gate then stops expecting 501 for its built routes. It does not edit
`server.go` or `contract.go`. A10a owns crew/member handlers; A10b owns role/crew_work handlers.
A4b owns turn/file handlers. The idle close handler is reserved separately.

Run `go generate ./internal/appapi` to regenerate `schema/*.json` from these
Go types. Each file is a JSON Schema 2020-12 document with named `$defs`,
`x-routes` request/response references and `x-events` payload references.
The schemas reserve JSON shapes, including responses not yet spelled out in
the route tables; they do not implement clocks, validation, mutations or AI calls.
Unspecified action replies use `{ok, receipt?}`. Settings remain a dotted-key
JSON map; each value's validation belongs to A11a. IDs, file cursors and timestamps
remain opaque to the app. `process.output`, `ai.delta` and `ai.done` are separate
follow-response event contracts, not main-stream publishers. No app role is enabled.
The route law reads all explicit app method/path pairs in this document and
compares them with the actual registered handlers; the schema gate also checks
fresh generation and populated JSON round-trips for every registered wire type.

**EXISTS (A16):** the same command publishes `docs/design/app-v2/wire.ts` for
the app, with every registered wire type, `APP_CONTRACT`, `AppRoutes` request/
response mappings, and `AppEvents` payloads (including separate follow streams).
`AppEvent` is a discriminated union. Money stays a string, timestamps and ids
remain opaque strings, and optional fields and required nullable Go collections
retain their JSON meaning. Route types include reservations; they do not claim
those routes are built. Schema and TypeScript freshness run in `make test-quick`
through the contract laws. [Contract publication](CONTRACT.md) explains pinning
the app's copy and the optional strict TypeScript compiler acceptance fixture.

### 2.1 Agent

S2 fields: `id, name, model, effort, budget, state, crew_id?, role_id?, rules[], pinned, created_at, created_by, last_message_at, closed_at?, archived_at?`, plus the derived `glyph` (needs_you › failed › running › queued › done/idle) and `spent`. `context[]` holds the chips (path, kind, exists).

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/agents?q=&state=` | → `{agents[]}` (rail, All crews and agents) | — | `wb:worldrows.go`, `world.go` (rows), `wb:history.go:243` (closed/archived) | EXISTS (A4a; search expansion A12) |
| `POST /app/v1/agents` | `{text?, model?, effort?, budget?, approvals?, context[]?, start: "send"\|"empty"}` → `Agent` (name from text, ≤40 chars) | `agent.created`, then `turn.*` | `wb:bridge.go:644-765` (POST /sessions), remote `Session.New` (`v2:internal/remote/wire.go:642`) | EXISTS (A4a) |
| `GET /app/v1/agents/{id}` | → `Agent` + `snapshot` (entries tail, queue, open asks, tasks summary, usage) | — | `wb:bridge.go:72-96,779`, `snapshotview.go` | EXISTS (A4b full snapshot) |
| `PATCH /app/v1/agents/{id}` | `{name?, model?, effort?, budget?, pinned?}` | `agent.changed` | wire `SetModel`, `SetEffort`, `SetSpendRail` (`v2:wire.go:491,492,600`). Rename and pin are NEW | EXISTS (A4a) |
| `POST /app/v1/agents/{id}/close` · `POST /app/v1/agents/{id}/archive` · `POST /app/v1/agents/{id}/reopen` | `{expect?}`. Close refuses 409 `{running:{processes:n,tasks:n}}` unless `{stop_running:true}` | `agent.changed` | remote `Places.Archive` (`v2:wire_places.go:56`), `wb:history.go:251-259` | EXISTS (A4a) |
| `POST /app/v1/agents/close-idle` | `{ids[]}` (the app picked them with `?q=idle:true`) → `{closed[]}` | `agent.changed`×n | NEW (idle = no message 7 days and nothing running; S2) | NEW |
| `POST /app/v1/agents/{id}/turn` | `{text, mode: "send"\|"steer"\|"queue", context[]?, files[]?}` → `{accepted, turn_id}`. 409 if running and mode is `send` | `turn.*` | `wb:bridge.go:877-952`, wire `Submit`, `Steer`; durable pending rows (`v2:wire.go:449-464`) | EXISTS (A4b) |
| `POST /app/v1/agents/{id}/queue/{qid}` · `DELETE /app/v1/agents/{id}/queue/{qid}` | `{text}` edit · remove | `queue.changed` | `wb:queue.go` port adapted to binary-owned pending rows | EXISTS (A4b) |
| `POST /app/v1/agents/{id}/stop` | → receipt "Stopped by you" | `turn.ended` | `wb:bridge.go:959`, wire `StopWork` (`v2:wire.go:486`) | EXISTS (A4b) |
| `POST /app/v1/agents/{id}/retry` · `POST /app/v1/agents/{id}/switch-model` | after terminal provider failure (fixed ×3 ceiling remains a gap) | `agent.changed` | canonical terminal stream error and model setter | EXISTS (A4b) |
| `POST /app/v1/agents/{id}/move-reply-to-task` | → `{task_id}` (running long, 3:00) | `task.created`, receipt | canonical `Interrupt` and `StartTask` | EXISTS (A4b) |
| `GET /app/v1/agents/{id}/entries?before=&limit=` | earlier transcript | — | wire `Transcript`, `EarlierHistory` (`v2:wire.go:556-557`); `wb:` `?since=` tail | EXISTS (A4b) |
| `GET /app/v1/agents/{id}/tools/{call_id}` | → `{output, full}` (1 MiB) | — | `wb:bridge.go:793,1096` | EXISTS (A4b) |
| `GET /app/v1/agents/{id}/files/text` · `GET /app/v1/agents/{id}/files/stat` · `GET /app/v1/agents/{id}/files/find` · `GET /app/v1/agents/{id}/files/changes` · `GET /app/v1/agents/{id}/files/diff` | `text`, `stat`, `find`, `changes`, `diff` (chips, "Not found on this Mac", @mention, file chips) | — | `wb:routes.go:10-52`, `files.go`, `fileslist.go`, `workview.go` | PORT |

A4a implementation: `internal/appapi/agents.go` and `agents_routes.go` port the
bridge's attached-session and saved-history split without its places or model pin.
`cmd/codeaf/app_agents.go` opens the canonical remote engine through an app-owned
child pipe, or joins a saved conversation already held by the TUI host. New app
children receive memory credentials through A2's inherited `--keys-fd` pipe and
do not start detached hosts. App shutdown releases those child pipes;
joining a TUI host does not make that host belong to the app.

- The registry is `<home>/v3/app/agents.json`, with private atomic replacement and
  `agents.lock` across launches. Records name existing transcript ids and paths;
  the canonical home walk also exposes TUI conversations. No agent is deleted.
- Create requires `start`. `send` needs nonblank `text`; `empty` refuses text and
  remains unnamed until the first message or a rename. Defaults are effort
  `medium` and budget `{usd:"5.00"}`; the engine resolves an omitted model.
  Approvals are `risky`, `everything` or `none` and map to the engine's own posture.
  Context chips take an absolute `path` and kind `file`, `folder` or `repo`; their
  existence is derived. Initial send carries these references, never a place.
- Names fold whitespace and keep at most 40 Unicode characters. Explicit rename
  wins over an outstanding naming errand and is journaled through `Rename` on
  the canonical wire. App sessions set `LaunchShape.DeterministicNames`, leaving
  the TUI's AI title behaviour available for TUI sessions.
- Reads derive `needs_you` before `running`, and preserve `failed`, `closed` and
  `archived`. A persisted running/waiting claim with no live connection or fresh
  presence reads `idle`, with `Stopped · codeaf restarted`. Failed state is a
  final engine turn error here; A4b owns provider retry/recovery semantics.
- Close refuses active replies, tasks or processes without `stop_running:true`,
  returning 409 `conflict` and `running:{processes,tasks}`. Crew members must leave
  their crew first. Archive requires Closed; reopen accepts Closed or Archived
  and clears lifecycle timestamps, keeping the transcript, name and settings.

Mutation bodies are strict JSON objects, limited to 1 MiB. Unknown fields, null
values, invalid effort/names/budgets and trailing data return 422 `bad_input`.
`expect.state` and `expect.version` mismatch return 409 `stale` before action.
POST idempotency responses are persisted with the mutation, scoped by bearer,
route and key, retained for 24 hours up to 4096 responses. Replays emit no second
event. Every mutation publishes `agent.created` or `agent.changed` after saving.
Initial send publishes `turn.started`; stream completion publishes `turn.ended`.

Lists include open, closed and archived records, newest last-message first, with
stable id ties. `state=` and `q` support plain name words, `state:` (including
`open`), `kind:agent`, `model:`, `crew:`, `pinned:true|false` and `since:YYYY-MM-DD`.
Unsupported tokens return 422; A12 adds quoted phrases, transcript `said:` and actual-spend `cost:` search through the shared history grammar.
The seven-day idle selection and `close-idle` remain with their owning rule lane.
A4b supplies the full turn snapshot, queue and tool routes; file browsing remains reserved. Budget writes bind the daily agent allowance through `SetAppBudget`; A11b
checks root-attributed receipts and maintains budget questions.

### A4b turn core implementation

`turn_handlers.go` serves all nine turn routes through the same registry and
transport guard. `GET /agents/{id}` returns `AgentResponse`: the last 100 app
entries, pending queue, open engine asks, task summaries and usage. Saved agents
retain their transcript and app receipts; live asks/tasks/usage come from the
engine interfaces, not another resource's store. Earlier compacted history is
included by the engine's `EarlierHistory` door. `/entries` pages backwards with
an opaque `before` cursor, default 100 and limit 1–500. Unknown cursors return 422.

Pending messages are saved with the agent and admitted by the binary, one at a
time after normal completion. The canonical wire has no queue-edit method:
keeping unsubmitted rows in the registry lets edits/removals and admission share
one lock. Removing or editing a row already admitted returns 409
`that message has already been sent`. Queueing while idle starts immediately.
Failure or interruption holds pending rows; Stop clears them. Restart preserves
pending rows without starting work on its own. Another surface's active reply
is refused rather than submitted into implicitly.

`send` refuses a running reply with 409; `steer` requires a running reply and
uses the engine's `Steer` subscription without publishing duplicate deltas.
Context remains explicit references. Attachments require absolute regular-file
paths and plain names, with an 8 MiB combined ceiling; the canonical
`SubmitFiles` door receives bytes. Queued attachments are read when admitted.
Files cannot accompany a steer. Strict JSON, null/unknown fields and expectations
are checked before admission. POST idempotency uses the launch-local bounded
response cache (32 KiB keyed bodies); replay makes no second call or event.

Stop uses `StopWork`, records `Stopped by you`, cancels the long timer and
invalidates the stream generation so late text/errors cannot alter a fresh turn.
Provider retries remain inside the canonical engine; a terminal error sets
Failed. Retry resends the last accepted request, including steer/context/files;
Switch model changes the failed agent's model for Retry. These handlers do not
change the engine's existing provider patience into a fixed three-call ceiling.

A reply emits `turn.long` once after 180 seconds, using an injectable timer for
proof. Move-to-task explicitly interrupts the reply, passes its request and up
to 64 KiB of transcript context to canonical `StartTask`, then records
`Moved to a task · the chat is free again`, a linked notice and `task.created`.
Existing tasks are kept. Failed admission records the interrupted reply as
Failed and returns 409; it never claims a task id. Nothing moves automatically.
Tool output is selected only by recorded call id, capped at 1 MiB with a UTF-8
boundary. Full stubs must resolve inside that transcript's `logs/stubs` directory
with the canonical filename; arbitrary paths and symlink escapes are refused.

### 2.2 Task

S2 fields: `id, agent_id, parent_id?, title, brief, state (proposed·held·waiting·running·paused·your_call·done·stopped·failed·declined·handed_off), waits_on[], clock, starts_at?, model, cap, step, files[], notes[{text, read_at_step?}], origin, handed_to?`, plus the derived `spent` and `elapsed`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/agents/{id}/tasks` | → tree `{groups[], tasks[], progress:{done,total}, spent, steps}` | — | wire `PlanTasks` (`v2:wire.go:574`), `wb:` snapshot `tasks` | EXISTS (A5) |
| `GET /app/v1/tasks/{tid}` | → `Task` + result, files, work log, notes, brief | — | wire `PlanTaskPage` (`v2:wire.go:575`), `wb:bridge.go:802` | EXISTS (A5) |
| `POST /app/v1/tasks/{tid}/note` · `POST /app/v1/tasks/{tid}/brief` | `{text}` → note with `read_at_step` filled later | `task.changed` | wire `PlanNote`, `PlanAmend`; `wb:routes.go:78` | EXISTS (A5) |
| `POST /app/v1/tasks/{tid}/pause` · `POST /app/v1/tasks/{tid}/resume` · `POST /app/v1/tasks/{tid}/stop` | — | `task.changed` | wire `PlanPause`, `PlanResume`, `PlanCancel` (`v2:wire.go:583-585`) | EXISTS (A5) |
| `POST /app/v1/tasks/{tid}/start` · `POST /app/v1/tasks/{tid}/hold` · `POST /app/v1/tasks/{tid}/decline` · `POST /app/v1/tasks/{tid}/model` | the proposal card: Start it · Hold · No · Change model | `task.changed`, `inbox.*` | wire `Task.Start`, `Task.Hold`, `Task.Model` (`v2:wire_task.go:17-42`) | EXISTS (A5) |
| `POST /app/v1/tasks/{tid}/typing` | holds the clock while the person types in the card | `task.changed {clock:"held"}` | `App.TaskAction` → per-task `HoldTask` | EXISTS (A5) |
| `POST /app/v1/tasks/{tid}/decide` | `{verb: "accept"\|"not_right"\|"tell"\|"recheck"\|"let_it_decide", words?}` (your call) | `task.changed`, `inbox.resolved` | wire `Task.Resolve`, `Task.Settle` (`v2:wire_task.go:38,52`) | EXISTS (A5) |
| `POST /app/v1/tasks/{tid}/hand-off` | see §2.22 Hand to factory | | | NEW |

### 2.3 Quick task

S2 fields: `id, agent_id, title, files[{path, before_hash, after_hash}], added, removed, state (running·done·undone·promoted)`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/agents/{id}/quick-tasks` | → list | — | tool `quick_task` (`v2:internal/session/task_quick.go:48-66`) | EXISTS (A5) |
| `POST /app/v1/quick-tasks/{qid}/undo` | → `{undone[]}` or 409 `{changed_since[]}` ("Can't undo cleanly: 2 files changed since") | `quick_task.changed` | session quick-file captures and private checkpoint | EXISTS (A5) |
| `POST /app/v1/quick-tasks/{qid}/promote` · `POST /app/v1/quick-tasks/{qid}/cancel` | answers "This is bigger than a quick task · 31 files" | `quick_task.changed` (Inbox resolution A9) | session promotion and stop | EXISTS (A5) |

A5 implementation: `internal/appapi/task_resource.go` and
`quick_task_resource.go` reach the canonical session through additive `App.*`
remote methods. Task ids encode the agent id and the engine's existing local
id, avoiding cross-conversation aliases. Both resource rows add `version` for
`expect.version`. All reserved A5 routes now serve behaviour; factory hand-off
remains A14. A5 publishes task and quick-task changes on the shared event hub;
A9 derives Inbox entries and owns its ids and resolution events.

- Proposal deadlines use the engine's saved setting (default 12 s). Hold and
  typing affect one proposal. `irreversible:true` proposals and quick-task
  promotions have no clock. No records or hashes are supplied by the app.
- Reads combine the graph and plan roads. Notes on graph tasks expose
  `read_at_step` only once the canonical steering record enters a model request.
  Plan notes retain their native behaviour and omit an unknown read step.
  Pause holds the next request/action; the current one finishes. Change the
  brief steers the existing task and keeps its original contract on record.
- Your-call verbs use the existing accept, continuation, recheck and model
  decision doors. Graph worker requests stop at the saved per-task cap; the
  default is $2, shared with the existing guarded plan engine.
- Quick write/edit calls serialize first-byte capture, execution and after-byte
  capture. Original bytes, existence, mode and latest bytes survive in the
  engine checkpoint, now written with mode 0600. HTTP exposes only hashes and
  line-change counts. Undo validates every file before restoring any; mismatches
  return 409 with `changed_since` and the exact refusal sentence.
- A quick task asks before its 31st distinct file, all shell calls (including
  concealed tests), and media creation. The blocked action cannot run in the
  quick worker. Promotion admits an ordinary task behind it, in the same
  folder, carrying the edits and blocked action. Cancel keeps the edits.
  Explicit Promote also works before the limit. POST responses use the shared
  bounded launch-local idempotency door; replays repeat no engine action.

### 2.4 Process

S2 fields: `id, agent_id, name, command, url?, state (running·done·failed·stopped), started_at, ended_at?, exit_code?, output_bytes`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/processes?agent=&state=` | → list (agent panel, and the Quit prompt across agents) | — | `wb:jobs.go:105,124`; `v2:internal/session/jobs.go:475` (`jobRegistry`) | EXISTS (A6) |
| `GET /app/v1/processes/{pid}/output?tail=&follow=1` | → `{text, truncated}`. `follow=1` is SSE | `process.output` (follow only) | `wb:jobs.go` log route; `terminal_routes.go:58` (stream pattern) | EXISTS (A6) |
| `POST /app/v1/processes/{pid}/stop` · `POST /app/v1/processes/{pid}/rerun` | | `process.changed`, notice | `wb:jobs.go:137`. Rerun uses the retained command | EXISTS (A6) |
| `POST /app/v1/agents/{id}/processes/stop-all` | | `process.changed`×n | `internal/appapi/processes.go` | EXISTS (A6) |

**A6 behaviour:** processes start through the agent's existing background-command
tool, rather than a second HTTP command runner. Opaque process ids include the
agent id and its local process number; a bare OS pid is never a stop address.
`rerun` executes only an ended shell command retained by that agent, with a new
process id. Running commands, watches, renders, closed agents and missing commands
cannot be rerun. Stop is harmless after an end. Stop-all compares `expect` with
the agent; stop/rerun compare its state with the process and its version with the
agent. POSTs use the launch's idempotency cache.

Lists include live engine shelves and saved Ended rows without opening idle
agents. Saved running rows become stopped after restart. `q` supports plain
words and `state:`, `agent:`, `kind:process`, and `since:` (date or RFC3339).
`state=ended` groups done/failed/stopped. Names, commands, exit codes and the first
local server URL are retained beside the transcript in `processes.json`.

Output reads are confined to the engine-listed log under that agent's session,
including symlinks. `tail` is a non-negative byte count, defaults to 64 KiB and
caps at 10 MiB; text is valid UTF-8. `follow=1` sends `process.output` SSE frames
for the retained tail and newly appended output, then finishes after the final
output. It sends 20-second keepalives, uses a 10-second write deadline, and stops
on disconnect or shutdown. Process state changes use a separate 250 ms shelf
poll; output follows at 100 ms without taking the task subscription.

App-created process output uses one file capped at 10 MiB. Compaction drops oldest
complete lines in bounded batches; an overlong line falls back to a byte cut.
A truncation flag survives the ending. Completed managed output expires after
seven days; file leases protect active writers. Records and end notices remain.
The terminal's 8 MiB chunk spool and directory budget remain; the app uses the
seven-day lifetime rather than the terminal's completed-log count/byte budget.
Close and shutdown stop running processes and publish their end notice before
detaching. `AgentRegistry.StopAllProcesses` is the stop door for A14's sleep route;
this lane does not mark that reserved route as built.

Terminals (`wb:terminal*.go`) are dropped. The v2 design has no terminal surface.

### 2.5 Standing order and reminder

S2 fields: `id, agent_id, title, trigger{kind: event\|schedule\|once, source, filter}, does, wont, scope (this_chat·this_crew·everywhere·not_here), trust (asks_first·earning·trusted), approved_runs, budget_per_run, state (proposed·active·paused·stopped·declined·fired), last_run_at?, skipped`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/agents/{id}/orders` | → `{waiting[], active[], paused[], reminders[]}` | — | wire `Standing.Items` (`v2:wire.go:651`), `v2:internal/standing/standing.go:512` | EXISTS (A7) |
| `POST /app/v1/orders/{oid}/ratify` · `POST /app/v1/orders/{oid}/change` · `POST /app/v1/orders/{oid}/not-now` | the card. `change` takes fields | `order.changed`, receipt | wire `ResolveStanding`, `Standing.Save` (`v2:wire.go:498,652`) | EXISTS (A7) |
| `POST /app/v1/orders/{oid}/pause` · `POST /app/v1/orders/{oid}/resume` · `POST /app/v1/orders/{oid}/stop` | | `order.changed` | `Status` active/paused/retired (`standing.go:198-202`) | EXISTS (A7) |
| `POST /app/v1/agents/{id}/orders/pause-all` | | | native store | EXISTS (A7) |
| `POST /app/v1/orders/{oid}/runs/{rid}` | `{verb: "go"\|"skip"\|"pause"\|"not_right"\|"run_now"}` | `order.fired`, `inbox.*` | trust rule `TrustAfter = 5` (`v2:internal/standing/words.go:140`) | EXISTS (A7) |
| `POST /app/v1/reminders/{oid}/change` · `POST /app/v1/reminders/{oid}/cancel` | `{at}`. A past time is refused with 422 "That time has passed. Pick another." | | `WhenAt`, `CardReminder` (`standing.go:209,239`) | EXISTS (A7) |

A7 persists dormant proposals before the model waits; ratification and edits
use the native per-item flock and optional order state/version expectations.
Reminder notices are saved directly without a trust card. One-shot task work
still needs ratification and run approval; reminder-only routes refuse it.
App trust counts five explicit
approvals; Skip is neutral, Pause keeps the count, and Not right resets it.
Changing the trigger/action/limits consumes a pending run and resets trust.
The order response adds `version` and `runs[{id,at,state,line}]`; evidence stays
in the engine. Paused triggers retain skipped lines. Stop/decline remove rows
from the live list and preserve their records. Pause-all leaves reminders alone.

`app serve` runs the native credential-bearing ticker with every chat closed,
and samples child-engine/OS writes for `order.changed`, `order.fired` and Inbox
events. Notifications are durable engine entries without recipient model calls.
`AgentRegistry.OrderAsks` provides proposals, waiting runs, budget holds and
fired reminders to Inbox through typed `Ask` values. The standing producer
uses A9’s five-second hold and forwards decisions to its own source routes.
Scope selects current chat
recipients, including closed chats, and always retains an engine Inbox copy.
A budget stop holds later triggers until `/change` raises `budget_per_run`.
Schema 5 fences app approval metadata from older tickers; ordinary schema 4
recurrence and saved budget values stay readable.

### 2.6 Permission, always-allow rule, question clock

A permission is `{id, agent_id, task_id?, command, class?, irreversible, why, before[2], batch?[{id, command}]}`. A rule is `{id, match: "exact"\|"class", pattern, class_label, who: "agent"\|"all_agents", agent_id?, made_at}`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `POST /app/v1/permissions/{id}` | `{verb: "allow_once"\|"deny"\|"always", rule?:{match, who}, items?[{id, verb}]}` (batch: Allow all · Deny all · One by one) | `ask.resolved`, receipt, `rule.created` | wire `ResolveConsent`, `ResolveConsentRemember` (`v2:wire.go:496-497`); `wb:bridge.go:826-876` (`/answer`) | PORT |
| `GET /app/v1/rules` · `DELETE /app/v1/rules/{id}` · `POST /app/v1/rules/{id}/restore` | Settings → Permissions, remove with undo | `rule.changed` | `v2:internal/config/approvalmemory.go` (`tools.approval` map) | NEW (HTTP; ids are new) |
| `GET /app/v1/rules/{id}/why` | → `{rule, matched}` (the "Why?" on a receipt) | — | NEW | NEW |
| Settings keys `permissions.mode`, `permissions.always_ask[]`, `permissions.question_clock_min` | see §2.18 | | wire `ResolvedApproval`, `SetApproval` (`v2:wire.go:619-620`), `v2:internal/session/approvalposture.go:56,69` | PORT (`wb:settings_permissions.go`) + NEW clock |

### 2.7 Clarification and question (agent side)

One `Ask` object covers every engine kind: `{id, kind: "question"\|"clarification"\|"permission"\|"your_call"\|"proposal"\|"budget"\|"order_ask"\|"reminder", source:{kind, id}, path[], title, options[], suggested?, irreversible, touches_prod?, clock?:{starts_at? \| pauses_at?}, before[2], state: "waiting"\|"answered"\|"withdrawn", receipt?}`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `POST /app/v1/asks/{id}/answer` | `{choice?, words?, verb?: "later"\|"you_decide", expect?}`. A second answer gets 409 `{by, at}` and the sentence "@jm answered this 10s ago. Your answer wasn't sent." | `ask.resolved` | wire `ResolveQuestion`, `Question.Hold` (`v2:wire.go:510,522`); `wb:bridge.go:826-876`, `routes.go:136` | PORT |
| `GET /app/v1/asks/{id}` | → `Ask` with `before[2]` (the two turns before it) | — | wire `Held.Questions` (`v2:wire.go:733`); `v2:internal/session/question.go:713` | PORT |

"Later" on an ask is the engine-side hold ("folds into the header count"). The Inbox's Later is app state (§2.13). These are two different things, and the app sends both.

### 2.8 Receipt and notice

Receipts and notices are transcript entries, not resources. Each is a `DisplayEntry` whose `kind` is `receipt` or `notice`, with `{text, by, at, link?:{kind, id}}`.
- They arrive as `turn.entry` events.
- They are written by the engine for every decision, including automatic ones and answers given in the Inbox (X1 Receipts).
- Backed by `wb:bridge.go:504-580` (event kinds `notice`, `jobUpdate`, `questionAnswered`).
- Status: **PORT** for notices. Receipts made uniform for every decision path are **NEW**.

### 2.9 Crew

S2 fields: `id, name, tint (iris·sage·sand·tide·rose·graphite), goal, manager_id, parent_id?, depth, budget, share, ends_at?, state (active·wrapping_up·report_ready·closed·archived), report?, created_from, closed_at?, archived_at?`, plus the derived `state_line` ("2 running 1 needs you") and `spent`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/crews?q=` · `GET /app/v1/crews/{cid}` | → crew, team tree, plan (read-only), items | — | wire `Teams.Read` (`v2:internal/remote/wire_teams.go:35`), `v2:internal/teams/teams.go:55` | EXISTS (A10a) |
| `POST /app/v1/agents/{id}/bring-in-helpers` | → proposal card `{name, tint, helpers[{role, model, budget}], budget, ends_at}` (the agent proposes it) | `ask.created` | source agent `Submit`, routed and billed by its engine | EXISTS (A10a) |
| `POST /app/v1/crews` | `{from: "proposal", proposal_id, edits?}` or `{from: "agents", agent_ids[], name, goal, manager: "new"\|agent_id, members[]}` | `crew.created`, `agent.changed` | `teams.Update`; short handover notes become a native manager directive, and the manager writes the summary without merging chats | EXISTS (A10a) |
| `PATCH /app/v1/crews/{cid}` | `{name?, tint?, goal?, budget?, ends_at?}` | `crew.changed` | `teams.Update`; six tints map to native hue slots | EXISTS (A10a) |
| `POST /app/v1/crews/{cid}/replace-manager` | `{member_id \| role}` | `crew.changed` | `teams.SetManager`, retaining the former manager as a member | EXISTS (A10a) |
| `POST /app/v1/crews/{cid}/wrap-up` · `POST /app/v1/crews/{cid}/keep-going` · `POST /app/v1/crews/{cid}/finish` · `POST /app/v1/crews/{cid}/reopen` · `POST /app/v1/crews/{cid}/archive` | `finish` refuses 409 `{open_items:n}` ("2 items aren't done") | `crew.changed`, `report.ready` | native wrap directive, `teams.UpdateClosing`, manager engine | EXISTS (A10b) |
| `GET /app/v1/crews/{cid}/log?before=` | members talking, newest first | `crew.log` | native `teams.ReadTraffic` | EXISTS (A10b) |
| `POST /app/v1/crews/{cid}/items` · `DELETE /app/v1/crews/{cid}/items/{ref}` | `{ref \| url}`: link a job, issue or PR | `crew.changed` | preserved native document extension | EXISTS (A10b) |
| `GET /app/v1/crews/{cid}/summary-since?at=` | "While you were away · 3h" | — | deterministic native log digest after ≥1 h away | EXISTS (A10b) |

### 2.10 Member

S2 fields: `crew_id, agent_id, role_id, is_manager, reports_to, cap?, state (running·idle·needs_you·paused·stopped)`, plus `also_in[]` (copies in other crews) and `spent`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `POST /app/v1/crews/{cid}/members` | `{role_id \| new_role{name, instructions, model, effort}, agent_id?, cap?, reports_to}` → member. An agent already in this crew gets 409 "already in Flaky tests" | `crew.changed` | `teams.Member` (`teams.go:~20-50`) | EXISTS (A10a) |
| `PATCH /app/v1/crews/{cid}/members/{mid}` | `{cap?, reports_to?, instructions?}` | `crew.changed`, receipt | `Teams.Spend`, `teamsettings.go` | EXISTS (A10a) |
| `POST /app/v1/crews/{cid}/members/{mid}/pause` · `POST /app/v1/crews/{cid}/members/{mid}/resume` · `POST /app/v1/crews/{cid}/members/{mid}/move` · `POST /app/v1/crews/{cid}/members/{mid}/remove` | the manager has no `remove`: 409 → replace-manager | `crew.changed`, `agent.changed` | native team store + agent registry | EXISTS (A10a) |
| Messaging a member | `POST /app/v1/agents/{member.agent_id}/turn` (§2.1) | | each member is a conversation | EXISTS — A4b turn route with A10a admission/log adapter |

A10a stores app-only crew/member fields in the native `teams.json` document's
preserved `app_v2` extension. Native name, manager, parent and membership remain
canonical; the existing cross-process team lock protects mutations. Agent ids
remain transcript ids, and member paths use `agent_id` for `{mid}`. A member
already live in another crew is copied into a new conversation with the same
role id. Duplicate membership in the same crew returns 409. One crew has at
most 64 members. Crew names have 1–40 characters; goal has at most 8000 bytes;
role instructions have at most 16000 bytes. Budget/caps are positive cents;
caps cannot exceed the crew budget. The default crew budget is $20.

Unmanaged terminal groups and the optional global-manager group are not app crews.
Single-crew GET returns numeric `ETag` from the complete native document; mutations accept `expect.state` and
`expect.version`. Helper proposals are private, durable, expire after 24 h,
and accept once across launches. Proposal creation has a 2-minute ceiling;
it refuses a busy, closed, archived or already-member source. Accepting stale
source state returns 409. POST retries with the same launch's Idempotency-Key
reuse the reply; changed input with that key returns 409.

The budget monitor samples once per second and on app events. Spend starts at
membership, using each agent's engine/transcript usage; at a member cap it stops
that member, and at the crew budget it stops all members. It saves one budget
Ask before emitting `ask.created`. Raising the relevant cap through PATCH clears
that Ask. `CrewBudgetAsks` exposes the waiting set to A8/A9. `CrewAllowsMessage`
and `RecordCrewMessage` are the guarded A4b turn route's admission/log hooks; `CrewQuestionPath` is
A8/A10b's read-only visible path. Native question packets already climb managers;
permission decisions remain the person's. Ask-answer and Inbox behavior belongs
to A8/A9. Crew GET reads linked items and the manager's task snapshot; native
roles saved inline remain readable alongside A10b's durable role store.

### 2.11 Role

`{id, name, instructions, model, effort, made_by, copies[{crew_id, member_id}]}`.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/roles` · `POST /app/v1/roles` · `PATCH /app/v1/roles/{rid}` | an edit updates every copy's instructions at its next step | `role.changed` | private `<home>/v3/app/roles.json`; native directives apply instructions at the next boundary | EXISTS (A10b) |

### 2.12 Sub-crew and report

- A sub-crew is a crew with `parent_id`. `POST /app/v1/crews/{cid}/bring-in-helpers` accepts `{member_id, expect?}` and returns an editable proposal without changing membership. Accept it through `POST /app/v1/crews` with `from:"proposal"`; that same non-manager member becomes the child manager and keeps its conversation.
  - At depth 3 the route answers 409 "Crews nest 3 deep at most."
  - Defaults come from `DefaultTeamsDepthLimit = 3` and `DefaultTeamsSubSharePct = 50` (`v2:internal/config/teamdefaults.go:44-45`).
  - Status: **EXISTS (A10b)**. Acceptance rechecks parent revision, source membership, depth and budget. Each child budget is at most 50% of its parent, including edits; parent spend includes descendants with conversations counted once.
- A report is `GET /app/v1/crews/{cid}/report` → `{shipped, decisions[], files[], left_open[{text, hand_off?}], cost, markdown}`.
  - **EXISTS (A10b)**. Native closing packets supply shipped/files/left open; decided person packets supply decisions. Cost comes from actual crew usage. Markdown contains the same five parts. Report publication is saved once before `report.ready`.
- Questions climb through `Teams.Raise`, `Teams.Decide` and `Teams.Escalate` (`wire_teams.go:43-47`). They surface as one `Ask` with `path[]` (§2.7). `CrewQuestionPath` supplies the visible path with the original source; A8 owns its Ask codec.


A10b details: role names have 1–40 characters, instructions 1–16000 bytes,
models 1–256 bytes, effort Low/Medium/High (Medium if omitted). Role PATCH
updates every live copy's definition/model/effort and sends its instructions
through native member directives; work and memory stay per conversation.
Role POST/PATCH replies include a numeric ETag; PATCH accepts that version.
Moving a member also refuses a duplicate role in the destination crew.

Wrap up records the native fifteen-minute clock once and asks the manager to
finish. Keep going clears the pending report and end date. Finish refuses
409 `open_items` while tasks, unresolved report work or linked work remain.
With no report yet, Finish asks the idle manager to write one through its
normal billed engine (two-minute ceiling), then rechecks the crew before
closing. `teams.UpdateClosing` serializes report acceptance with lifecycle
writes. Finish stops only members without another open crew; linked jobs
are untouched. Reopen restores the roster and report, including descendants.
Archive requires a finished crew; neither state deletes history. Retired-member
spend stays in the lifetime pool; closing freezes spend and reopening excludes
work done during the closed interval.

Items accept exactly one ref or HTTP(S) URL without credentials, at most 256
links. URL-only links receive an opaque ref used to unlink them. Solo factory
state is read through its store interface using the canonical job state;
`Options.CrewItems` supplies a read-only source for other resources/factories.
An unknown or unavailable link stays unfinished; unlink removes only the
reference. Log returns up to 100 entries newest first with `before` as the
next page's cursor. `summary-since?at=<RFC3339>` returns a deterministic log
digest only after at least an hour away with new entries; otherwise text is
empty. An elapsed end date saves one question exposed through `CrewWorkAsks`;
it never closes a crew by itself.

### 2.13 Inbox (derived)

`GET /app/v1/inbox` returns `{items[]}`, the **waiting** set only, oldest first. An item is `{id, kind, source:{kind, id, factory?}, title, path[], created_at, context:{before[2]}, verbs[], who_can_answer}`.

| Kind | Source | Backed by | Status |
|---|---|---|---|
| `question`, `clarification` | agent, member, task, crew (climbed) | canonical `OpenQuestions`, typed `AskInboxSource` | BUILT (A9 projection) |
| `permission` | agent, task (batch) | canonical questions; permission resolver receives rule/batch fields | BUILT (A9 projection/forwarding) |
| `your_call` | task | canonical landing questions; typed source resolver | BUILT (A9 projection) |
| `approval_plan`, `approval_proof`, `approval_start` | job | factory Contract 2 `approve_plan`, `approve_proof`, `approve_start` | BUILT (A9 merge; native factory hold) |
| `budget` | agent, member, crew, job, day cap | canonical fuel questions, `CrewBudgetAsks`, typed asks, factory `budget` | BUILT (A9 projection) |
| `out_of_rounds`, `failed` | job; failed agent | factory Contract 2 needs-you; agent recovery routes | BUILT (A9 projection/forwarding) |
| `changes_requested`, `conflict` | job; task conflict | Contract 2 needs-you; canonical conflict question | BUILT (A9 projection) |
| `publish_failed`, `runner_offline` | job | Contract 2 needs-you and per-person `can` | BUILT (A9 merge) |
| `order_ask`, `reminder` | standing order | typed `AskInboxSource` producer/resolver | BUILT (A9 adapter; A7 owns production) |

Answer-once:
- The Inbox never owns an answer. `POST /app/v1/inbox/{id}/answer {…}` forwards to the source verb (`asks`, `permissions`, `tasks/decide`, `orders/runs`, or the factory `/v1/jobs/{id}/approve|answer`).
- Every surface that showed the item receives `inbox.resolved {id, how: "here"\|"in_place"\|"someone_else"\|"no_longer_needed", by, at}` within one second (U3-07).
- Approve and answer hold for 5 s, as **Approving… Undo**:
  - The binary keeps the pending decision with `undo_until`.
  - `POST /app/v1/inbox/{id}/undo` within 5 s cancels it, and nothing is sent.
  - This hold is server-side so that the Inbox, chat and job show one hold. For agent asks A9 owns the shared hold. For factory approvals the factory lane owns it (`undo_until`, F-2).
- Later, Done today, mark-done (`e`), notifications and the Dock badge count are **app state**. The app keys them by item `id`. Item ids are stable and derived from the source, e.g. `ask:<id>`, `job:<factory>:<id>:<kind>:<since>`.
- Status: **BUILT (A9)**. The routes, source adapters, resolution events and hold coordinator are implemented in `internal/appapi/inbox*.go`. `SetInboxSource` installs typed producers; factory attachment/solo lifecycle remains A14, and A5/A7/A8 keep their own state and resolver policy. Engine questions and saved crew budget asks are sampled automatically.
- Engine ids include the transcript, question lane and token (`ask:<agent>:<lane>:<token>`), so two agents asking question 1 cannot collide. Factory ids include the factory, opaque job id, kind and waiting-since timestamp. Display words never affect identity.
- Source ask/permission POSTs enter the same hold as Inbox. `AnswerInbox` / `UndoInbox` are the shared binary doors for other source surfaces. `ResolveInbox` reports in-place answers, another person's answer or withdrawal. Canonical question subscriptions track terminal answers even outside a live app turn.
- Source snapshots refresh every 500 ms. A failed source read keeps its last rows; detaching withdraws them. Factory-owned approval holds are recovered on reattachment, including Undo. Factory sources also follow `/v1/events` with the person's bearer and replay cursor, relaying only Inbox-related typed facts. Pending engine answers are cancelled on shutdown; they are never sent after a restart.

### 2.14 Memory

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/memory?q=&shelf=` | → `{shelves[{id, name: "Yours"\|crew}], items[{id, text, shelf, made_by, at, state, version}]}` | — | native `graph.db`, crew membership through `teams.Snapshot` | EXISTS (A11b) |
| `PATCH /app/v1/memory/{id}` · `POST /app/v1/memory/{id}/forget` · `POST /app/v1/memory/{id}/restore` | Forget has a 5 s undo: forget, then restore | `memory.changed` | transactional `Store.EditMemory` | EXISTS (A11b) |
| setting `memory.agents_may_add` | | | profile key read by the session's tool belt and extraction | EXISTS (A11b enforcement) |

The native adapter reads canonical `graph.db`, including Yours, This machine,
project owners and active crews' shelves (including crews with no memories).
Crew owners are `crew:<id>`; retrieval grants that owner only to a member of
that native crew. Child tasks inherit their admitted crew scope. PATCH edits
wording, moves a memory to a valid owner shelf, or does both atomically. Unknown,
closed or root crews cannot receive a shelf transfer. Crew execution/resource
adapters may also supply `MemoryBackend` through `WithMemoryBackend`.

Reads and edits expose `state` and `version` on every memory item.
`expect.state`/`expect.version` compare status/journal sequence inside the same
SQLite write transaction as the action, including against terminal writers.
Forget tombstones the row immediately and stores its undo deadline and tombstone
version in `graph.db`'s `memory_undo` table in that transaction. Restore succeeds
strictly before five seconds, including after restart and from another window.
An intervening terminal restore/forget invalidates the old undo. Keyed retries
never extend its deadline. At expiry the response is 409 `conflict` with
`the five-second memory undo window has ended`.

With `memory.agents_may_add:false`, the session leaves the `remember` tool off
its belt and stops automatic post-turn extraction. Existing memory reads and
explicit person saves remain available; an already-issued tool call rechecks
the setting before writing.

### 2.15 Spending ledger and caps

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/spending?when=&by=` | → `{today:{spent, cap}, rows[{at, who:{kind,id}, what, cost, model}], by_agent[]}` | `spend.changed` (throttled 1/s) | native `v3/usage.jsonl` | EXISTS (A11b) |
| `GET /app/v1/spending/today` | → `{spent, cap}` | | shared `config.AppCapAt` | EXISTS (A11b) |
| `POST /app/v1/caps/raise` | `{scope: "day"\|"agent"\|"member"\|"crew"\|"task", id?, to, for: "today"\|"for_good"}` | actual budget `inbox.resolved` | profile cap store, native agent binder, task guard | EXISTS (A11b day/agent/task; member/crew via owner backend) |

Costs are measured provider receipts. Sub-cent receipts are summed before money
is formatted to two decimals. `when` is today (default), week (today and the
previous six local calendar days) or all; future receipts are excluded. `by`
is agent (default), crew, job or model and orders known attribution; the adapter
never invents a crew/job id. Child calls belong to their root agent. `by_agent`
totals the selected range, and `today.spent` always totals today.

Defaults are $20/day across the home, $5/agent/day (including child tasks), and
$2/task across its lifetime. App children and joined TUI hosts use the additive
`SetAppBudget` protocol method; this is distinct from `SetSpendRail`, which
remains a lifetime conversation/crew-member limit. Agent budget PATCH and cap
raises bind the daily allowance. The native session checks measured receipts
before admitting a turn and before each model call. Task guards read changed
caps before every call without resetting reservations or measured spend.

Permanent day raises update the native profile default; permanent agent raises
update the saved agent and its execution owner. Day, agent and task temporary
raises live beside their base in the locked profile and expire at the next local
midnight, including a DST transition or engine restart. They never overwrite
permanent settings. The comparison and save share the profile's cross-process
lock; disabled caps and non-raises refuse. Agent expectations use the saved
agent's state/version. Day/task expectations must use the owning settings/task
route; they are refused here instead of silently ignored.

Task numbers are local to an agent. Cap raises accept the opaque HTTP task id
returned by A5. `<agent-id>:<task-number>` and an unambiguous bare task number
remain accepted by the cap adapter. The private profile key keeps the agent
with its local task number.
`TaskCapID` exposes the spelling to task/AI resource adapters. Member and crew
caps remain their execution owner's `SpendingBackend` seam; A11b does not edit
those resources' stores. The backend owns conditional writes and expiry.

The home observer refreshes native receipts once per second and publishes
`spend.changed` at most once per second. `RefreshSpending` is its synchronous
producer/test door. Reached day, agent and task caps create one restart-safe
budget question. `SpendingBudgetAsks` supplies that typed waiting set to asks and
Inbox through a registered `InboxSource`. Inbox Raise accepts `choice:"raise"`
and a two-decimal USD amount in `words`; its five-second Undo precedes a today
raise. Permanent raises use the caps route. A real raise or midnight reset
resolves its actual saved item; a raise
with no waiting question emits no invented resolution. A refused app turn stays
`needs_you`, and leaves its text unsent until the person raises the cap and sends
it again. Already admitted provider calls finish; no further call starts once
measured costs have reached a cap. Existing SpendGuard reservations remain in
force for priced task calls, with the existing cross-process in-flight limitation.

The app's "Run 3 · up to $9" cost check reads job budgets from `/v1` and today's
spend from `/app/v1/spending/today`. No price estimate is presented to the person.

### 2.16 Models, effort, providers

| Route | Body → Response | Backed by | Status |
|---|---|---|---|
| `GET /app/v1/models` | → `{models[{id, name, provider, available, efforts[]}], default, pinned[3]}` | `internal/appapi/models.go`, ported from `wb:modelroles.go` | EXISTS (A11a) |
| `PUT /app/v1/models/pinned` · `PUT /app/v1/models/default` | `{models:[id,id,id]}` · `{model, effort?}`; empty pinned slots are `""` | `internal/appapi/models.go`, ported from `wb:modelpinned.go` | EXISTS (A11a) |
| `GET /app/v1/models/roles` · `PUT /app/v1/models/roles/{role}` | → `{roles[{id,model,effort}]}` · `{model, effort}`. Only `checks` and `planner`; empty model follows the agent | `internal/appapi/models.go`, `internal/config/appsettings.go` | EXISTS (A11a) |
| `GET /app/v1/keys` · `PUT /app/v1/keys/{provider}` · `DELETE /app/v1/keys/{provider}` | §1.2 | `internal/appapi/keys.go`, `internal/config/appcredentials.go` | EXISTS (A2) |
| `POST /app/v1/providers/{p}/sign-in` | `{}` → `{verification_uri}`, then `keys.changed`. OpenRouter browser sign-in, no browser wait timeout | `internal/appapi/providers.go`, `internal/openrouterauth` | EXISTS (A11a) |
| `GET /app/v1/providers/{p}/sign-in` · `DELETE /app/v1/providers/{p}/sign-in` | → `{state,error}` (`waiting`, `signed_in`, `failed`, `canceled`; empty state before a first attempt) · cancel `{ok:true}` | `internal/appapi/providers.go` | EXISTS (A11a) |
| `POST /app/v1/providers/{p}/sign-in/claim` | `{}` → `{provider,key}`, one logical claim by the authenticated native core for Keychain / Secret Service | `internal/appapi/providers.go` | EXISTS (A11a) |

Effort on the wire is `low|medium|high`. The binary's `xhigh` and `max` (`v2:internal/effort/effort.go:27`) stay for the TUI and are never shown to the app. Higher saved TUI rungs read as `high`; auto or an unknown rung reads as `medium`. App writes refuse other rungs.

A successful key check's ordered model listing is reused for 15 minutes. Stdin keys discover models lazily on the first model read with the same 10 s authenticated listing door. Provider order starts with the most recently connected provider, then provider id; a provider's model order remains exactly what it returned. Non-default connections qualify ids with their configured connection name, just as the engine does. Saved default and pinned models from a removed provider stay visible as `available:false`, and cannot be newly selected. A checked provider without a resolved engine transport (including native Anthropic) also lists its models as unavailable; key validation alone does not make a model executable. No model is ranked or selected by AI.

Browser sign-in is backed only for OpenRouter; other providers return 422 `bad_input` with "this provider has no app browser sign-in". A concurrent browser flow returns 409. GET status never returns a credential. The native core listens for `keys.changed`, claims the returned key once, saves it under service `ai.codeaf.app`, and sends it back through the normal memory handoff on restart. The claim response is an authenticated, `no-store` secret transfer, not a model-facing GET. Reusing its Idempotency-Key retries the same logical claim; a fresh second claim returns 409. Cancel, shutdown and launch cancellation release the callback listener. A key PUT/DELETE made while the browser is waiting wins over its late completion.

### 2.17 Search (⌘K) and History

| Route | Body → Response | Backed by | Status |
|---|---|---|---|
| `GET /app/v1/search?q=&kinds=&include_closed=` | → `{groups:[{kind: "agents_crews"\|"jobs"\|"files", rows[≤4]}]}`. Order: exact prefix › word prefix › substring, ties broken by last used. Open tabs and Commands are app-side | wire `Places.Search` (`wire_places.go:53`) for said:, `wb:history.go:247`, solo `/v1/queue` jobs, `wb:files` find | EXISTS (A12) |
| `GET /app/v1/history?q=` | All crews and agents: tokens `state: said: since: kind: crew: model: cost:`. Meter: Open · Pinned · Closed · Archived | `wb:history.go:243-271` | EXISTS (A12) |

Team factory jobs in ⌘K come from the app's own `/v1` cache. The binary searches only what it owns.

A12 reads agents and crews through their typed resource handlers, solo jobs
through the mounted `/v1/queue` with the caller's access headers, and files
through context chips plus the agent file-finding interface when built. Missing
factory or reserved file interfaces contribute no extra rows; other resource
read failures return 503 rather than pretending there are no matches.
`kinds` accepts comma-separated `agents_crews`, `jobs`, `files`, or individual
`agent`/`agents` and `crew`/`crews` (singular job/file also work). Group order is
fixed and each group has at most four rows. Closed and archived agents/crews
require `include_closed=true`; Done jobs remain searchable. Stable kind/id ties
follow latest known usage. There are no AI calls or model attachments.

History and agent-list queries share an AND grammar: plain name words, quoted
phrases, `state:` (including `open` and `pinned`), `kind:`, `crew:` (id or name),
`model:`, `pinned:true|false`, `said:`, `since:` and `cost:`. `said:` includes
user/assistant text, earlier compacted history and crew member/report text,
never tool output or receipts. Crew model filtering uses the manager's model.
`since:` takes a UTC date or positive day count such as `7d`; `cost:` compares
actual spend in exact cents with `=`, `>`, `>=`, `<`, `<=`. Unknown spend cannot
match. Unsupported filters, unfinished quotes and invalid values return 422.

### 2.18 Settings that belong to the binary

**EXISTS (A11a):** `GET /app/v1/settings` → a flat `{key: value}` object containing only binary-owned, non-secret settings. `PATCH /app/v1/settings` with `{key: value, expect?:{version}}` validates the whole patch and returns 422 with the reason; nothing is partly saved. Money values are `{usd:"20.00"}`; zero means off for the per-conversation ceiling. Unknown keys, UI-only keys, credential keys and removal of a mandatory always-ask action are refused.

GET settings, model and setup reads return an opaque profile version in `ETag` (exposed to app origins). Every PUT/PATCH and setup POST accepts `expect:{version}`; a mismatch returns 409 `stale`. The comparison and write hold the same cross-process profile lock as terminal settings. Unsupported `expect.state` is refused rather than ignored. Successful writes publish `settings.changed {keys[]}` after one atomic private-file replacement. Existing terminal credentials and unrelated profile keys are preserved.

Model default/effort, day cap, per-task cap, per-conversation ceiling, proposal seconds and crew depth/share/wake use the engine's native keys. Checks and Planner write their grouped native `models.roles` pins atomically; GET derives those rows from the native pins, so terminal edits remain visible. App agent launchers use `config.AppRoleSource` around their routing source so unpinned Checks/Planner follow each agent rather than the terminal tier defaults (A4b turn-engine integration). Agent creation reads the saved default model, app effort, per-agent budget and approval mode; explicit creation fields win, and existing agents keep their own choices. New app keys use `app.*` in the same profile. Their storage/validation is implemented here; the owning agent, permissions, memory and factory lanes still supply their live enforcement. `permissions.mode` is stored under `app.permissions.mode` because the current terminal's prompt/allow/deny posture has different semantics.

| Key | Default (design) | Today in v2 | Status |
|---|---|---|---|
| `models.default`, `models.effort`, `models.pinned[3]`, `models.roles.{checks,planner}` | first model from setup · medium | profile `config.json` | EXISTS (A11a key) |
| `spending.day_cap` | $20.00 (written at J01) | `DefaultDailyBudgetUSD = 500` (`v2:internal/config/config.go:128`) | EXISTS (A11a key) |
| `spending.per_agent` | $5.00 | none (standing per-run $5, `standing.go:406`) | EXISTS (A11a key) |
| `spending.per_task` | $2.00 | `CrewTaskCapDefault = 5.0` (`config/crew.go:384`) | EXISTS (A11a key) |
| `spending.per_conversation_ceiling` | off | `SetSpendRail` | EXISTS (A11a key) |
| `memory.agents_may_add` | on | — | EXISTS (A11a key) |
| `permissions.mode` | ask_risky | approval posture | EXISTS (A11a key) |
| `permissions.always_ask[]` | acting in your name, deleting outside a task, payments | `guard`, `paymentrefusal` | EXISTS (A11a key) |
| `permissions.question_clock_min` | 15 | none | EXISTS (A11a key) |
| `accounts.{github,google,slack,mcp:*}` | ask_first | `internal/connect` | EXISTS (A11a key) |
| `factory.polling_s` | 60 | `Config.PollingEvery` is unset by launchers | EXISTS (A11a key) |
| `advanced.context_tokens` | 160k | ctxbudget | EXISTS (A11a key) |
| `advanced.work_folder` | `~/codeaf/work` | workspace flag | EXISTS (A11a key) |
| `advanced.proposal_s` | 12 (0–60, 0 = never) | `DefaultTaskAutoApprove = 15` (`config/settings.go:1454`) | EXISTS (A11a key) |
| `advanced.idle_days` | 7 | — | EXISTS (A11a key) |
| `crews.depth`, `crews.share_pct`, `crews.wake` | 3 · 50 · on | `teamdefaults.go:42-48` | EXISTS (A11a key) |

UI-only keys stay in the app: theme, tint, text size, open at login, banners, sound, dock badge, density, auto-update, and "Later 09:00".

### 2.19 First run

- `GET /app/v1/setup` → `{step, has_key, default_model?, day_cap, complete}`. Steps 1–4 are J01; step 5 means complete. The binary persists `app.setup.step` so a quit mid-way resumes at the same step, separately from the terminal's welcome marker. A GitHub key alone does not satisfy `has_key`.
- `POST /app/v1/setup` with `{step, default_model?, day_cap?, expect?:{version}}` advances one step or moves backward before completion. Defaults may be changed only while on step 3. Continue to step 3 requires a model key with a successful nonempty listing; the first returned model seeds an unset default. Continue to step 4 writes the chosen default model and day cap (fresh app default `$20.00`) to the same settings keys. Existing shared-home choices are preserved. Step 4 to 5 completes the welcome; the chosen factory creation/attachment is a separate A14 call. Completed setup cannot be restarted through this route.
- Step 2 is `PUT /app/v1/keys/{p}`. The one check call has a 10 s timeout and quotes the provider's words verbatim ("Key didn't work · the provider said: invalid API key (401)").
- Status: **EXISTS (A11a)**. Setup and browser POSTs accept Idempotency-Key, replay the original response within this launch (last 128 requests), and refuse changed input with the same key. Bodies must be one JSON object of at most 32 KiB; unknown fields and trailing data are refused.

### 2.20 The AI door

```
POST /app/v1/ai/call
{ "role": "app.summary" | "app.title" | "app.<purpose>",   // registered app roles only
  "tier": "reflex" | "low" | "worker",                      // optional; default from the role
  "messages": [{ "role": "system"|"user"|"assistant", "content": "…" }],
  "max_tokens": 800, "json_schema": { … }?, "stream": false,
  "budget": { "usd": "0.05" } }
→ { "text": "…", "json": { … }?, "model": "deepseek/…", "usage": {in, out}, "cost": {"usd":"0.0012"}, "ledger_id": "…" }
```

- The binary picks the model through its own routing: `roles.Role` and `lane` (`v2:internal/roles/roles.go:35,294`, `v2:internal/lane/`). The app never names a model.
- Every call goes through the one token funnel. It writes a ledger row as `who:{kind:"app", purpose}` and counts against the day cap. At the cap it answers 402 `{reason:"at_cap"}`.
- With `stream: true` the call returns SSE `ai.delta` / `ai.done` on its own response, not on the main stream.
- The registry of app roles lives in the binary (`internal/roles`), so a structural test can list every token path.
- Status: **NEW**.
- S0 forbids the app's own logic from asking a model to "decide, rank, summarise or suggest". The door exists for features the owner adds later, and the design itself names none. See Q2.

### 2.21 Activity (app side) and History

- The engine's own Activity (agent closed, crew finished, rule removed) appears as `activity` events, and is listed by `GET /app/v1/activity?since=`.
- Tidy-undo bookkeeping is app state. The engine supplies the inverse verbs (`reopen`, `restore`).
- Status: **EXISTS (A12)**. Successful agent lifecycle/pin changes and supported
  crew finish/archive/reopen, rule removal/restoration and memory
  forget/restoration produce one durable line and an `activity` event. Failed
  actions, unchanged requests and idempotency replays produce none. Other
  resource lanes still own their verbs; a reserved action remains 501.
- `<home>/v3/app/activity.jsonl` is private (0600), appended and flushed under a
  shared file lock across windows and launches. The lock covers before/after
  typed resource reads and the action, so two windows cannot record the same
  transition twice. The transport installs this A12 observer after authentication.
  `since` accepts RFC3339 timestamps or UTC dates, exclusively; lists are newest
  first. An unavailable journal refuses before mutation. A write failure after
  mutation returns 503 explicitly stating that the action completed; journal
  and resource storage are separate, so a crash between them can omit a line.

### 2.22 Factory

The factory API is `/v1` as specified in `v2:docs/design/factory/ARCHITECTURE.md` §4. The app uses it as is: from the in-process solo factory on the same listener, and from team factories over HTTPS.

The engine adds only the following:
- `POST /app/v1/factories/solo` and `POST /app/v1/factories {url}` (§1.4).
- Hand to factory: `POST /app/v1/agents/{id}/hand-off` → a card `{title, brief, repo, pipeline, issue: bool, start: "now"\|"backlog", assignee}`, then `POST /app/v1/agents/{id}/hand-off/{card}/send` → `{factory, job}`.
  - It stops the originating task with "Handed to the factory" and writes the notice. The job's `ask.source` links back to the chat.
  - It is backed by `POST /v1/jobs` plus a NEW `from_ref`.

Deltas the factory lanes must add (t-v2-factory-jobs, t-v2-factory-people). Most are already in their briefs:

| # | Delta | Lane | Status |
|---|---|---|---|
| F-1 | `Config{ServedBy:"app", AccessToken:<launch token>}` enforces bearer-only person routes, including about/access; loopback never grants ownership; `served_by:"app"`, `access:"token"` | deltas | EXISTS |
| F-2 | Server-side approval hold: `undo_until` = decided + 5 s, plus `POST /v1/jobs/{id}/undo`; restart-safe and caller-only | jobs | EXISTS |
| F-3 | Needs-you kinds `approve_start`, `failed`, `changes_requested`, `conflict`, `runner_offline`; Contract 2 renames `out_of_attempts`→`out_of_rounds`, `spending_limit`→`budget` | deltas | EXISTS |
| F-4 | `POST /v1/jobs` accepts `source:"hand-off"`, `from_ref:{kind:"agent",id,title}`, `assignee`, `start:"now"\|"queued"\|"backlog"`; persists `ask.from_ref`, applies pipeline budget on start | deltas | EXISTS |
| F-5 | `POST /v1/runners/presence {asleep}` with runner credential sets persistent sleep state; awake resumes pulls | people | EXISTS |
| F-6 | Typed SSE `job.state {job,state,reason}` on every projected state or reason change; typed `runner` and `activity` already supplied | deltas + people | EXISTS |
| F-7 | Per-person dismissal and `is:dismissed`; `priority {value,set_by}`; `POST /v1/jobs/{id}/nudge` enters Activity | jobs | EXISTS |

The deltas lane reuses jobs' F-2/F-7 and people's F-5 plus runner/Activity
producers. It adds the missing app authentication, needs-you codec, hand-off
metadata and `job.state` producer. Numeric SSE job ids and persisted question
kinds retain their binary protocol bytes. Assign to me refuses an already
assigned job; only the current assignee can Change assignee or Unassign, and
Solo-host GitHub writes succeed before the stored person changes; team serve persists the decision without writing to GitHub (§2.22.1). A14 supplies the
app factory mount, factory connection routes, hand-off card and power forwarding.

Hand-off validates source, agent ref, start choice and assignee before creation.
`now` queues with precedence, `queued` uses the ordinary queue, and `backlog`
starts no work. An explicitly empty assignee leaves Backlog unassigned; a team
job cannot start with an explicitly empty assignee. The legacy `autostart`
field remains accepted but cannot be combined with `start`. New metadata
requires a store-backed factory; a missing store refuses before creating a job.


A14 status: **BUILT**. `/v1/*` shares the app listener and launch bearer. Worker pull,
lease and presence routes use factory-issued worker credentials after the same
Origin/Host checks. A discovered TUI solo service is reused behind the mount,
preserving factory-issued runner credentials. The mount follows service election
when that host closes; a surviving app can take over the same solo store.
An app host writes only its address, PID and app marker in `serve.json`; another
process retrieves the launch bearer through a private `.solo` runtime socket.
The app's launch bearer is never persisted.
`serve.lock` elects the solo host; `worker.lock` elects one worker process for the
shared home. Its private runtime socket under `/tmp/codeaf-runners-<uid>/` accepts app factory sessions in memory,
so a TUI that owns the worker can execute work for newly attached teams. Each
factory gets an isolated mirror below that worker's PID folder.

`attach.json` retains a first-team `url` for terminal compatibility and a
`factories` list for all rail connections; no app session token is stored there.
A late stdin handoff restarts signed-out factory streams. Connection health is
sampled every 2 seconds; Last seen is retained when a connected factory disappears.
Leaving a team withdraws its Inbox items and stops that connection's worker loop.
A solo factory cannot be left or shared through the team attachment route.

Hand-off cards add `factory` and `factories` for the owner-decided picker;
send accepts `factory` as an edit. The default selects a watched repository's
factory. Cards expire after one hour and are bounded to 256 per launch. A changed
agent returns 409 `stale`; each card is idempotent through the factory's job door.
The source keeps running if creation fails. Task snapshots and final transitions
can be supplied through `FactoryTaskSource`; otherwise the task route and engine
stop door remain authoritative. App task ids are decoded to their engine-local
ids, with graph and plan cancellation kept distinct. A changed task answers 409
`stale` before creating a job. Task hand-off leaves unrelated replies running.
The factory records `ask.from_ref`, and the source receives "Handed to the factory".
The current job creation contract uses the repository pipeline. `issue:true` or
a custom `pipeline` returns 422 `not_allowed`; neither is silently ignored.

### 2.22.1 Team operator claim and GitHub read grant — EXISTS

`codeaf factory serve` prints the public address first. If the team has no persisted
operator, the next line is `operator access link: codeaf://factory/join?operator=true&url=<encoded address>#token=<secret>`.
It uses the same join URL and `/v1/auth/device` → `/v1/auth/device/poll` exchange as
`POST /v1/access/links`. Link metadata carries `operator:true`. It is single use,
expires after 24 hours, and every startup revokes older outstanding operator links
before minting another. A host GitHub token binds the login; with no token the first
successful GitHub sign-in claims it. The server verifies identity with GitHub and
persists `{login,claimed_at}` in private `operator.json` beside the host config.
Changing the host token or the legacy `operator_login` key never changes that identity.

After claim, ordinary startup prints no operator link. `serve --new-operator-link`
prints a recovery link bound to the persisted operator and revokes previous outstanding
operator links. A consumed operator link cannot sign in again; its factory session
retains its own 30-day lifetime, independent of the link's 24-hour expiry. The raw
host `access_token` remains a script credential and is never printed by serve or
embedded in an operator join link. Claim Activity has `kind:operator_claimed`,
`verb:"claimed factory"`, person actor with `operator:true`, and `as operator` detail.

| Route | Body → Response | Access |
|---|---|---|
| `PUT /v1/github/grant` | `{token}` → the `/v1/github` metadata below | Live signed-in persisted operator only |
| `GET /v1/github` | → `{factory,login,via,polling_every?,granted_by?,granted_at?}` | Normal factory read access |

The app obtains a separate GitHub App device-flow token with Issues and Metadata read
permissions. The grant door checks `/user` and refuses a different login. Known
write-capable classic OAuth scopes are refused: classic `repo` is not read-only, and
profile-only OAuth scopes allow public reads. GitHub App permissions are configured
in GitHub; an absent OAuth-scope header does not prove token permissions.
The token is atomically saved in mode-0600 `github-grant.json` beside the host config;
only login and grant time leave the service. `GH_TOKEN` or `GITHUB_TOKEN` overrides the
stored read token without replacing the operator or the grant metadata. Grant changes
wake the repository poll; future reads resolve the current credential. The grant
never reaches runner or publishing token resolvers. Team serve omits GitHub assignment
and branch-write callbacks; local job decisions persist without a server GitHub write.
The server never writes to GitHub, even with an environment override.

### 2.23 Engine health and shutdown

These transport routes are available before any model key or agent exists.

| Route | Body → Response | Events | Backed by | Status |
|---|---|---|---|---|
| `GET /app/v1/health` | → `{status:"ready", codeaf_version, contract, epoch}` | — | `internal/appapi/server.go` | EXISTS (A1) |
| `POST /app/v1/shutdown` | `{reason?}` → `{ok:true}`, flushed before exit | — | `internal/appapi/server.go`, `cmd/codeaf/app_serve.go` | EXISTS (A1 transport; A6 process cleanup) |

Both require the per-launch bearer token and the origin/host guard. Unknown
routes return 404 `not_found` only after authentication. Wrong methods return
405 with `Allow`. Shutdown accepts one JSON object of at most 16 KiB; an empty
body, `null`, unknown fields, a non-string reason or trailing data returns 422
`bad_input` without stopping. Repeated valid shutdowns initiate exit once.

---

## 3. The event stream

`GET /app/v1/events` streams SSE in the factory's style (`v2:internal/factory/serve/events.go:16-103`). **EXISTS (A3):** `internal/appapi/stream.go` and `event.go`; resource producers arrive with their owning lanes.

```
id: e_<epoch>_18422
event: task.changed
data: {"agent":"a_…","task":"t_…","state":"your_call","at":"…"}

```

- **Ids** are `e_<epoch>_<n>`, with a monotonic sequence within one engine **epoch**. The epoch is the launch's independent 64-hex identity from stdout hello and health, never its token. The draft's bare `e_<n>` could collide after a restart, so the opaque cursor includes the epoch. Sequence zero is the live baseline before the first publication.
- **Replay.** `Last-Event-ID` replays strictly later events from a ring of 4096 records, the same size as `wb:worldstream.go:22` (`WorldRing`).
  - An unknown or expired id gets one `reset` event, `{epoch, reason}`, and the client re-reads. Reasons are `unknown`, `expired` and `epoch_changed`. Empty, repeated, malformed, future and bare numeric cursors are unknown. An evicted cursor is expired even if the immediately following event is retained.
  - A first connection starts live. Its `hello` carries the current cursor. A reconnect's `hello` has no `id`, preserving the old cursor until replay succeeds. `reset` assigns the current head and continues live without sending a partial replay.
  - A slow reader that falls outside the retained ring also gets a reset. Readers share the ring and wake-up channel; publishers never wait on a reader or maintain per-window queues. Shutdown wakes idle streams. A blocked write has a 10 s deadline.
  - This mirrors `/v1` (200-record window, `changed *`) and `wb:` (`after=N`, gap → full snapshot).
- **Keepalive** is a `: keepalive` comment every 20 s.
- **Size.** Events carry ids and small facts, not whole objects, except `turn.entry` and `turn.delta`, which carry the text. Clients re-`GET` the full object.
- **One stream for all agents.** Per-agent filtering is `?agent=<id>` (repeatable). Without it, everything except `turn.delta` is included. With it, agent-scoped events and deltas are included only for the named agents; events without an `agent` remain visible. Filtering applies equally to replay and live delivery. Repeated ids are harmless; an empty agent id returns 422 `bad_input`. This keeps a 14-tab window cheap.
- **Publication.** Resource handlers use `Server.Events().Publish(EventType, payload)`. The hub freezes JSON object payloads and accepts only the named main-stream event types below. Publishers own resource payloads and throttling (including `spend.changed`). `hello` and `reset` are transport-owned; `process.output` and AI deltas belong to separate follow responses. No engine producer is started by A3.
- **Authentication.** The Rust core sends the bearer header and forwards the stream. Query credentials remain refused, including on this route; the optional EventSource exception is not enabled.

| Event | Data | Source |
|---|---|---|
| `hello` | `{epoch, codeaf_version, contract}` (first frame) | EXISTS (A3) |
| `reset` | `{epoch, reason}` | EXISTS (A3), ported from `wb:worldstream.go:320-328` |
| `agent.created` · `agent.changed` · `agent.removed` | `{agent, fields[]}` (name, state, glyph, model, budget, closed) | `wb:worldrows.go` `world` rows |
| `turn.started` · `turn.ended` | `{agent, turn, how: "done"\|"stopped"\|"failed", receipt?}` | `wb:` kinds `turnDone`, `assistantDone`, `error` |
| `turn.delta` | `{agent, turn, kind: "text"\|"thinking", text}` | `wb:` `text`, `thinking`, `reasoning` |
| `turn.entry` | `{agent, entry: DisplayEntry}` (tool calls, receipts, notices, cards) | `wb:` `toolBegin…toolFinished`, `notice`, `caption` |
| `turn.long` | `{agent, turn, after_s: 180}` (once per reply) | NEW |
| `queue.changed` | `{agent, queue[]}` | `wb:` `steerAccepted`, `steerConsumed`, `steerFellThrough` |
| `ask.created` · `ask.resolved` · `ask.withdrawn` | `{ask, agent?, kind, how?, by?}` | `wb:` `question`, `consent`, `questionAnswered`, `questionWithdrawn` |
| `task.created` · `task.changed` | `{agent, task, state, clock?, step?}` | `wb:` `taskProposal`, `taskUpdate`, `taskPhase` |
| `quick_task.changed` | `{agent, quick_task, state}` | NEW |
| `process.changed` · `process.output` | `{agent, process, state, exit_code?, url?}` · `{process, text}` (follow streams only) | `wb:` `jobUpdate` |
| `order.changed` · `order.fired` | `{agent, order, state, trust, approved_runs}` · `{order, run, ask?}` | EXISTS (A7 native store sampling) |
| `crew.created` · `crew.changed` · `crew.log` · `report.ready` | `{crew, fields[]}` · `{crew, from, to, text}` · `{crew}` | NEW (`Teams.*`) |
| `role.changed` · `rule.created` · `rule.changed` · `memory.changed` · `settings.changed` · `keys.changed` | `{id}` / `{keys[]}` | NEW |
| `inbox.added` · `inbox.resolved` · `inbox.undo_window` | `{item, kind}` · `{item, how, by, at}` · `{item, until}` | `wb:attention.go` + NEW |
| `spend.changed` | `{today, cap, agent?}` (≤1/s) | NEW |
| `factory.changed` | `{factory, state}`, plus each attached factory's typed events re-emitted as `factory.event` `{factory, type, data}` only for Inbox kinds | NEW |
| `activity` | `{at, actor, verb, object, detail}` | EXISTS (A12) |
| `title.changed` | `{agent, name}` | `wb:` `titleChanged`, wire `Title.Watch` |

---

## 4. Port plan

### 4.1 What to bring from `wb:internal/desktopbridge`

| Bring | Becomes | Note |
|---|---|---|
| `bridge.go` (session core, ring, replay), `routes.go`, `snapshotview.go`, `lifecycle.go`, `attach.go`, `queue.go`, `queue_send.go` | `internal/appapi/agents*.go` | Remove the `OneModel`/`Model` gate (`bridge.go:31,707`): v2 agents pick models. Remove places args |
| `origin.go`, the token parts of `bridge.go`, `cmd/codeaf/desktop_bridge.go` | `internal/appapi/guard.go`, `cmd/codeaf/app_serve.go` | `--parent-pid`, stdin life line, `--keys-fd`, solo factory mount |
| `attention.go`, `world.go`, `worldrows.go`, `worldstream.go`, `worldseen.go` | `internal/appapi/stream.go`, `inbox.go`, `rows.go` | One stream instead of two. Rows become agents and crews |
| `jobs.go` | `internal/appapi/processes.go` | Rename to processes, everywhere on the wire |
| `files.go`, `fileslist.go`, `workview.go` (stat/text/find/changes/diff only) | `internal/appapi/files.go` | Read-only. Keep `files_mutation_absent_test.go` |
| `history.go`, `history_delete.go` (archive/restore only) | `internal/appapi/history.go` | Drop the delete route: S0 says nothing is deleted |
| `modelroles.go`, `modelpinned.go`, `settings.go`, `settings_permissions.go` | `internal/appapi/models.go`, `settings.go` | |
| `plan_routes.go`, the task routes in `routes.go` | `internal/appapi/tasks.go` | |
| Tests: `route_table_test.go`, `security_test.go`, `typed_clients_contract_test.go`, `endpoints_test.go`, `snapshot_flow_test.go`, `snapshotring_test.go`, `lifecycle_flow_test.go` | same | They keep their laws |

### 4.2 What to drop

The v2 design does not need any of these:
- **Places**, about 25 files (`places*.go`, `placehome.go`, `using.go`, `workingfolder.go`, `placechange.go`, `openrequest.go`, `remember_knows.go`, `knows_routes.go`). S0: "The UI never shows a place on an agent", and X1 out of scope: "Agent folders or places".
- **Decisions** (`decisions.go`). Councils (`council_routes.go`) are out too: "Crews talking to each other" is backlog.
- **Workspaces and tab groups** (`workspaces*.go`, `tabgroups.go`). Tabs, panes and windows are app state.
- **Terminals** (`terminal*.go`, `termenv.go`). Editors and favicons (`editors.go`, `favicon.go`, `webfavicon.go`, `workspacefavicon.go`): there is no design surface for them.

Dropping places also removes the dependency on packages that are absent from v2: `placegraph`, `placeorg/*`, `workspacestore`, `council`, `decide`.

The remaining gaps are `session` and `remote` symbols: `PlaceGraph*`, `QuestionOutcome`/`RecentQuestionOutcomes`, `DiffStart`, `TextFile`. Each is either dropped with places or ported as a small change (lane A4b checks the signatures of `ListDir`, `StatPaths`, `FetchedFile`, `JobNotice`, `EventTitleChanged` and `EventTaskPhase`).

### 4.3 Lanes (½–1 day each, in dependency order)

| Id | Scope | Deps | Size | Status |
|---|---|---|---|---|
| A1 | `codeaf app serve`: listener, hello line, token, origin/host guard, parent-pid and stdin life, `--home`, `/app/v1/health`, shutdown, route-table and security tests | — | 1 d | EXISTS |
| A2 | Keys: in-memory credential source, `--keys-fd` to children, `PUT/DELETE/GET /keys` with check call, refuse disk writes in app mode, redact | A1 | ½ d | EXISTS |
| A3 | Stream: the hub, epoch-qualified `e_<epoch>_<n>` ring 4096, `Last-Event-ID`, `reset`, `?agent=` filter, keepalive (port worldstream + bridge ring) | A1 | ½ d | EXISTS |
| A4a | Agents: registry, list/create/get/patch/close/archive/reopen, stable ids, deterministic names (§5) | A3 | 1 d | EXISTS |
| A4b | Turns: send/steer/queue, queue edit/remove, stop, retry/model recovery, task handoff, snapshot, entries, tools (bridge core); file browsing remains reserved | A4a | 1 d | EXISTS (turn core) |
| A5 | Tasks + quick tasks: tree, page, verbs, proposal clock (12 s, typing hold), your-call verbs, quick-task hashes, undo, promote | A4b | 1 d | EXISTS (engine hooks and HTTP) |
| A6 | Processes: rename, list/output/follow/stop/rerun/stop-all, retention (§5), quit and power stops | A4b | ½ d | EXISTS (power stop door built; `/power` calls it in A14) |
| A7 | Standing orders and reminders over `Standing.*`: scope field, run verbs, pause-all | A4b | 1 d | EXISTS |

| A8 | Asks + permissions + rules + question clock: one Ask object, receipts on every path, rule ids, Why? | A4b | 1 d | NEW |
| A9 | Inbox: derived set, stable ids, answer-forwarding, 5 s server hold for engine asks, factory needs-you merge (solo + attached) | A5, A7, A8, F-2/F-3 | 1 d | BUILT — typed producers/resolvers; attachment lifecycle A14 |
| A10a | Crews + members over `Teams.*`: tint, goal, budget, ends_at, members, caps, pause/move/remove, replace manager | A4a | 1 d | EXISTS — resource routes, budget monitor, producer hooks |
| A10b | Roles store + copies; sub-crews; climbing path; wrap-up/report (five parts); items; log | A10a | 1 d | EXISTS |
| A11a | Models/effort/providers/settings keys (port modelroles/pinned/settings) + first-run setup | A2 | ½ d | EXISTS |
| A11b | Spending ledger and caps + memory over `Places.Ledger` / `Memory.*` | A4a | ½ d | EXISTS — engine day/agent/task caps and temporary raises; shelves, atomic memory edit/5 s undo and add switch |
| A12 | Search (⌘K merge) + history (port) + engine Activity | A4a | ½ d | EXISTS |
| A13 | AI door: app role registry, routing, ledger rows, cap, stream; funnel structural test | A11b | ½ d | NEW |
| A14 | Factories in-process: solo mount on the same listener, create solo, attach/detach team, power → runner asleep, hand-off | A1, F-1, F-4, F-5 | 1 d | BUILT |
| A15 | Shared rules in `internal/appapi/rules.go`, `/crew` → `/seats` with alias, idle selection predicate, 12 s/$2/$0.40 defaults. Resource enforcement remains with A5–A8/A11a | — | ½ d | BUILT |
| A16 | Contract reservations, schemas, `docs/design/app-v2/API.md` ↔ route table law and generated `docs/design/app-v2/wire.ts` for the app, from registered Go wire types | A4a | ½ d | EXISTS |

Parallel waves:
1. A1.
2. A2, A3, A15.
3. A4a, A11a.
4. A4b, A10a, A11b, A12, A16.
5. A5, A6, A7, A8, A10b, A13.
6. A9, A14, which wait on the factory deltas.

### 4.4 Collision risks with the running factory lanes

- t-v2-factory-jobs and t-v2-factory-people both edit `internal/factory/**` (`api`, `serve`, `live`, `record`), `cmd/codeaf/factory*.go`, the manual and the change entries. Every A-lane except A14 lives in a NEW package, `internal/appapi`, plus `cmd/codeaf/app_serve.go`, and does not touch those paths.
- A14 and F-1…F-7 touch the same files. Hand F-deltas to the factory lanes as amendments (`plandb task amend`) rather than editing their files. A14 starts after both lanes land.
- Shared hot files: `cmd/codeaf/main.go` (one dispatch line for `app`), `internal/config/settings.go` and `apikey.go` (A2, A15), `internal/session/task*.go` (A5, A15), and `internal/standing/*` (A7). Keep each edit narrow and rebase on `app/v2` before every push.
- The wire names `machine` → `runner` and `audit` → `Activity` are being renamed by the people lane. A3 must consume whichever name has landed. The app stream re-emits factory events under its own names, so it is insulated.
- `docs/changes/unreleased` gets one entry per lane, which can conflict on append. Use separate files.

---

## 5. Engine behaviour changes the design requires

| Rule (board) | Before the v2 rules | Change | Where | A15 status |
|---|---|---|---|---|
| Agent names are deterministic: first words, ≤40 chars, until renamed (S0, S2; AI titles backlog) | AI title through `roles.RoleTitle` (`v2:internal/session/handlepick.go:210-222`) | **EXISTS (A4a)**: in app mode, names come from the first words of the first message. The AI title stays for the TUI. See Q2 | `session/appnames.go`, `session/title.go`, A4a | — |
| Standing-order trust: 5 approved runs; Not right resets to 0; Skip counts neither way; Pause keeps the level | `TrustAfter = 5` (`standing/words.go:140`), counted by `CleanRuns` | **EXISTS (A7)**: five explicit approvals, Not right resets, Skip neutral, Pause preserves | `standing/words.go`, A7 | — |
| Standing scope: this_chat · this_crew · everywhere · not_here | no such field | **NEW** | `standing/standing.go` `Item`, A7 | EXISTS (A7) — persisted scope and current-member delivery |
| Budget per run $0.40 default | `DefaultPerRunUSD = 5.0` (`standing/standing.go:406`) | **CHANGED** | A7 | BUILT — default $0.40, saved rails preserved |
| Quick task: over 30 files or any test run → full task, asks first | no limit (`session/task_quick.go:332,349`) | **NEW**: a fixed rule plus before/after hashes for Undo | `session/task_quick.go`, A5 | EXISTS (A5) — before-write captures, Undo and promotion before file 31 or shell execution |
| Task proposal clock 12 s (0–60, 0 = never); irreversible tasks have no clock; typing holds | `DefaultTaskAutoApprove = 15` (`config/settings.go:1454`) | **CHANGED** to 12; per-task typing hold is **NEW** | A5 | EXISTS (A5) — saved/default 12 s, per-task typing hold and irreversible no-clock |
| Question clock 15 min: no answer → agent pauses | none (`store/agent_questions.go:69` `ExpiresAt` unused for this) | **NEW** | `session/question.go`, A8 | POLICY BUILT — `QuestionClock`; pause enforcement A8 |
| Process output kept 7 days, ≤10 MB each, oldest lines dropped | 64 KB ring + 2×4 MB spool (`session/jobs.go:80-81`); `sweepTTL` 7 d covers droppings only (`session/sweep.go:73`) | **CHANGED**: 10 MB cap, 7-day sweep of process logs | `session/jobs.go`, `app_process_output.go`, `app_processes.go`, A6 | EXISTS — app single-file 10 MiB window and leased seven-day expiry |
| Processes stop when the agent closes, the app quits or the Mac sleeps; one end notice | partial | **CHANGED** | A6 | EXISTS for close/quit; `StopAllProcesses` is the A14 power hook |
| Per-task cap $2.00 | `CrewTaskCapDefault = 5.0` (`config/crew.go:384`) | **CHANGED** | A15 | BUILT — default $2, saved limits preserved |
| Agent budget $5.00 default, asks at it | no per-agent default; per-conversation rail exists (`session/rail.go:38`) | **NEW** default + budget ask | A4a/A11b | BUILT — daily agent accounting and budget questions |
| Day cap: $20 written at first run; at cap everything pauses and asks | `DefaultDailyBudgetUSD = 500` (`config/config.go:128`) | **EXISTS (A11a setup)**: setup writes 20 for a fresh app; existing shared-home choices and the TUI built-in default remain | A11a | BUILT — setup persistence A11a; cap questions A11b |
| Sub-crew depth 3, share 50% | `teamdefaults.go:44-45` | **EXISTS** | — | — |
| Crew budget, caps can't exceed it; member at cap stops and its manager asks | team `CapUSDDay` per day (`teamdefaults.go:43`) | **CHANGED**: a crew budget (not per day) plus member caps | A10a | EXISTS — positive cents, lifetime pool, one saved budget Ask |
| Crew tints: six named | float `Hue` (`teams/teams.go:70`) | **NEW** enum (map to hues) | A10a | EXISTS — six tints mapped to native hues |
| Idle agent: no message 7 days and nothing running; nothing closes on its own | none | **NEW** derived flag | A15 | BUILT — `AgentIsIdle`; list/close routes A4a |
| Provider error ×3 → failed state with Retry · Switch model | retries exist; no failed agent state | **EXISTS (A4b recovery)**: terminal engine failure enables Retry/Switch model; fixed three-call patience remains a gap | A4b | — |
| Running long: one line at 3:00 per reply; Move it to a task | none | **EXISTS (A4b)**: one 180 s event; explicit handoff through StartTask | A4b | — |
| Receipts for every decision, including automatic ones and answers given elsewhere | partial | **CHANGED** | A8 | — |
| Approve/answer 5 s hold, server-side | none (engine); factory 5 s hold with Undo | **EXISTS** for factory F-2 and engine A9 | | — |
| Effort: Low · Medium · High (default Medium) | five rungs (`effort/effort.go:27`) | **EXISTS**; the app shows three | — | — |
| Glossary: TUI `/crew` (a task's model seats) → `/seats` | `CrewCommand = "/crew"` (`config/crew.go:103`) | **CHANGED**: `/seats`, with `/crew` an alias for one release; manual pages | A15 | BUILT — canonical `/seats`, `/crew` alias for one release |
| Glossary: background job → process; teams → crews; managers/traffic → manager/log; machine → runner; audit → Activity; attempts → rounds | Go names | **CHANGED on the wire only**. The codec maps; Go keeps names | A4–A10, factory lanes | — |
| Agents have no place: context arrives as chips | places in `wb:` | **DROP** (do not port) | §4.2 | — |
| Keys never on disk | profile `api_key` in `config.json` (`config/apikey.go`) | **CHANGED** in app mode | A2 | — |

---

## 6. Open questions for the owner

1. **Home sharing — decided.** Owner decision in codeaf-desk `src/design/source/DECISIONS.md` supersedes the previous separate-home assumption: the app engine and TUI use `~/.codeaf` (or `CODEAF_HOME`), share agents, history and settings, and elect one factory runner for that home. The app's Keychain/Secret Service handoff stays in process memory; sharing the home never permits those keys to be saved in a profile. Worker registration credentials are separate factory credentials, shared privately for takeover.
2. **Titles: deterministic or AI?** S0 rules names deterministic and lists the Titles-and-summaries role as backlog. The brief says AI through the binary is allowed. Should v2 ship deterministic names only, with no AI title even through the door? This file assumes yes. The AI door ships with no app role registered until you name one.
3. **Per-agent budget period.** S2 says "budget default $5.00; asks at it", which reads as a lifetime figure. X4 Settings says "Per agent $5.00 · each agent, each day". Which is it?
4. **Factory Contract 2 — decided and built.** Factory responses use Contract 2, including needs-you `approve_start`, `budget`, `out_of_rounds`, and typed runner events. The app maps nothing; persisted question kinds and numeric runner protocol ids remain compatible.
5. **Keychain service name.** S0 says `ai.codeaf.app`; the shipped desk uses `ai.agentfield.codeaf-desk`. Should we migrate the stored entries at first v2 launch, or keep the old name?

