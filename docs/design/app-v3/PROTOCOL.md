# Engine protocol (UI2 ⇄ codeaf), contract 1 (additive revisions keep 1)

UI2's Rust core (`crates/core`) starts the bundled codeaf binary as a child
process, `codeaf app`, and talks to it over the child's stdin/stdout. No port
is opened. The webview never sees codeaf: React → Tauri command → `Backend`
method → this protocol.

Vocabulary: the app says **agent** (a codeaf session / conversation), **task**
(a node in an agent's task graph), **crew** (a codeaf team, `internal/teams`).
codeaf's own "crew" (`internal/crewroute`, model seats) is not exposed here.

## Framing

- UTF-8, one JSON object per line (`\n`), both directions. No other output on
  stdout; logs go to stderr.
- Requests (app → codeaf): `{"id": <u64>, "method": "<name>", "params": {...}}`.
  `params` may be omitted when empty. Ids are chosen by the app, unique per
  process lifetime.
- Responses (codeaf → app): `{"id": <u64>, "result": <any>}` or
  `{"id": <u64>, "error": {"code": "<word>", "message": "<sentence>"}}`.
  Exactly one per request, in any order.
- Events (codeaf → app): `{"event": "<name>", "seq": <u64>, "data": {...}}`.
  `seq` starts at 1 and increases by 1 per event for the process lifetime.
- Unknown request methods answer error code `unknown_method`. Unknown fields
  are ignored by both sides. Unknown event names are ignored by the app.

Error codes: `unknown_method`, `bad_params`, `not_found`, `busy`, `engine`
(the underlying engine failed; message says how), `closed`.

## Lifetime

1. App spawns `codeaf app [--home <dir>]`.
2. codeaf writes the first line, an event:
   `{"event":"hello","seq":1,"data":{"contract":1,"version":"<codeaf version>","pid":<int>,"home":"<dir>"}}`
   The app refuses a `contract` it does not know and kills the child.
3. Requests and events flow.
4. codeaf exits cleanly (closing every agent engine it started) when its stdin
   reaches EOF, on SIGTERM/SIGINT, or on request `shutdown`. The app closes
   stdin to stop it, then kills after 3 s.

## Types

```
Agent  { id, title, workspace, model?, state, updated_at }
       id: stable string (the conversation's session file id/path key)
       state: "idle" | "working" | "needs_you" | "stopped" | "closed"
       "stopped": its conversation engine failed; the next send reopens it
       updated_at: RFC 3339
Task   { id, agent, title, state, updated_at, parent?, group?, line? }
       parent: the task that spawned it (nesting); group: the agent's grouping label;
       line: the one live status line the TUI shows for it
       state: "queued" | "running" | "done" | "failed" | "unverified"
Crew   { id, name, manager?, members: [{agent, handle?}] }
Entry  { id, agent, turn?, kind, at, ... }   one transcript item, in order
       kind "user"     { text }
       kind "answer"   { text, thinking? }
       kind "tool"     { call, name, ok?, summary? }
       kind "receipt"  { text }          e.g. "Allowed once · you · 14:02"
       kind "notice"   { text, ref? }
       kind "error"    { text, error_kind?, status? }
Model  { id, name, provider }
Settings { default_model, budget_usd, asks_before, day_cap_usd }
Spend  { today_usd, day_cap_usd }
Ask    { id, agent, kind, subkind?, title, detail?, options?: [{id, label}], irreversible? }
       subkind (approval): "permission" | "budget"; options carry the engine's ids,
       e.g. permission: allow_once · deny · always (absent when irreversible)
       kind: "approval" (a tool call waiting for allow/deny) | "question" (the agent asks the person)
       approval options are the engine's own choices (e.g. allow once / always / deny);
       a question may have options, free text, or both
```

## Methods

| method | params | result |
|---|---|---|
| `ping` | – | `{}` |
| `shutdown` | – | `{}` then exit |
| `agents.list` | `{workspace?}` | `{agents: Agent[]}` (recent conversations, newest first) |
| `agents.create` | `{workspace?, model?, budget_usd?, text?}` | `{agent: Agent}`; with `text` the first turn starts at once (`turn.started` follows) and `title` is the first words of `text`, cut to 40 characters with "…". No workspace: the user's home folder. |
| `agents.entries` | `{id, before?, limit?}` | `{entries: Entry[], more: bool}` (oldest first; works for agents not held) |
| `agents.set_model` | `{id, model}` | `{agent: Agent}` |
| `agents.retry` | `{id}` | `{turn}` (re-run the last failed turn) |
| `agents.open` | `{id}` | `{agent: Agent}` (attach an engine to a saved conversation) |
| `agents.send` | `{id, text}` | `{turn: <u64>}`; output arrives as events |
| `agents.stop` | `{id}` | `{}` (interrupt the running turn) |
| `agents.close` | `{id}` | `{}` (detach and stop its engine; conversation stays on disk) |
| `asks.list` | `{agent}` | `{asks: Ask[]}` (open asks only) |
| `asks.answer` | `{agent, id, option?, text?}` | `{}`; `not_found` if already answered elsewhere |
| `tasks.list` | `{agent}` | `{tasks: Task[]}` |
| `models.list` | – | `{models: Model[]}` (what the configured keys can use) |
| `settings.get` | – | `{settings: Settings}` |
| `spend.today` | – | `{spend: Spend}` |
| `crews.list` | – | `{crews: Crew[]}` |

## Events

| event | data |
|---|---|
| `hello` | see Lifetime |
| `agent.changed` | `{agent: Agent}` |
| `turn.started` | `{agent, turn}` |
| `turn.delta` | `{agent, turn, kind: "text" \| "thinking", text}` |
| `turn.tool` | `{agent, turn, call, name, phase: "begin" \| "end", ok?, summary?}` |
| `turn.done` | `{agent, turn, ok, error?, error_kind?: "provider" \| "budget" \| "stopped" \| "other", status?}` (provider: the provider failed 3 times in a row; message is the provider's own words) |
| `ask.opened` | `{ask: Ask}` (agent state becomes `needs_you`) |
| `ask.closed` | `{agent, id, by: "app" \| "elsewhere" \| "stopped", option?, at}`; a receipt Entry follows as `entry.added` |
| `entry.added` | `{entry: Entry}` (user message, final answer, receipt, notice, error; streaming text still uses turn.delta) |
| `spend.changed` | `{spend: Spend}` |
| `task.changed` | `{task: Task}` |
| `crew.changed` | `{crew: Crew}` |
| `crew.removed` | `{id}` |
| `log` | `{level: "info" \| "warn" \| "error", message}` (non-fatal notices) |

## Identity and de-duplication (rev 2)

- `agents.create` and `agents.send` accept `client_id` (string chosen by the app).
  The engine echoes it on the user `Entry` (`client_id`) so the app replaces its
  optimistic bubble instead of adding a second one.
- `agents.create` answers at once (target under 50 ms): it returns the Agent
  (state `working` when `text` was given) before the conversation engine has
  finished loading; loading continues in the background and failures arrive as
  `turn.done {ok:false}` + `agent.changed`.
- Every assistant message inside a turn has an index `msg` (0, 1, 2 …).
  `turn.delta` carries `msg`; the `entry.added` answer for that message carries
  `turn` and `msg`. The app keys answers by `(agent, turn, msg)`: deltas append,
  the entry replaces. An answer is never shown twice.
- Entry ids are stable: the id sent live in `entry.added` equals the id returned
  later by `agents.entries` for the same item.
- `turn.tool` and tool entries carry `title`: the same one-line label the TUI
  shows for that call (e.g. `read src/auth/session.ts`, `bash pnpm test`), and
  `detail?` (short result line). Never just the bare tool name.
- Engine notices (e.g. "skills carried: …") are `kind: "notice"` with
  `quiet: true` when the TUI shows them as a dim line; the app renders quiet
  notices as one muted line, not a card.
- `models.list` returns every model the user can pick in the TUI's model
  picker for their configured providers, plus the current default, each with
  `{id, name, provider, default?: bool}`; never empty when a turn can run.
- `agents.set_effort {id, effort: "low"|"medium"|"high"}` and Agent gains
  `effort?`.

## Wave 2: setup, keys, settings, money

Types:
```
Provider { id, name, signed_in: bool, key_hint?: "…a91f", sign_in?: bool }
Settings { default_model, budget_usd, asks_before, day_cap_usd, work_folder,
           task_cap_usd, proposed_task_clock_s, idle_close_days, effort? }
Setup    { step: 1|2|3|4|"done", provider?, default_model?, day_cap_usd?, start?: "agents" }
```
Keys never touch disk in codeaf: the app keeps them in the macOS Keychain and
hands them to codeaf at every launch with `keys.set`; codeaf holds them in memory.

| method | params | result |
|---|---|---|
| `providers.list` | – | `{providers: Provider[]}` |
| `keys.check` | `{provider, key}` | `{ok, models?: Model[], status?, message?}` exactly one provider request; 10 s timeout → `{ok:false, status:"timeout", message:"No answer · couldn't reach <host> in 10s"}`; a rejection carries the provider's status code and own words |
| `keys.set` | `{provider, key}` | `{}` (memory only; models.list / turns use it) |
| `keys.remove` | `{provider}` | `{}` |
| `providers.sign_in` | `{provider}` | `{url}` browser sign-in where the engine supports it; completion arrives as `provider.changed` |
| `setup.get` / `setup.set` | – / `Setup` | `{setup: Setup}` (persisted by codeaf so quitting mid-setup resumes at the same step) |
| `settings.set` | partial `Settings` | `{settings}`; emits `settings.changed` |
| `caps.raise` | `{scope: "agent"\|"day", id?, to_usd, for: "today"\|"good"}` | `{}` |

Budget asks: when an agent nears/hits its budget or the day cap, an `Ask` with
`kind:"approval", subkind:"budget"`, `detail` = what it has done so far, and options
`raise` (`{to_usd}` via `asks.answer {option:"raise", text:"8.00"}`), `stop`, `tell`
(free text). Day cap options: `raise_today`, `wait`. Copy per U1/U2 edge cases
("It's close to its $5.00 budget", "Day cap reached · $40.00 of $40.00").

Events: `settings.changed {settings}`, `provider.changed {provider, key?}` (key: sent once after a browser sign-in so the app can store it in the Keychain), `spend.changed`.

## Wave 3: tasks, quick tasks, processes, running long, sleep

Types:
```
Task (adds) { proposed?: bool, clock_until?: time, irreversible?: bool,
              your_call?: bool, result?, files?: [{path, add, del}], budget_usd?, spent_usd? }
TaskPage { task: Task, result?, files, log: Entry[], notes: [{id, text, at, read_at_step?}], brief }
Quick    { id, agent, title, files: [{path, add, del}], state: "running"|"done"|"undone"|"grew"|"conflict",
           grew?: {files, reason}, conflict?: {changed: string[]} }
Process  { id, agent, title, command, state: "running"|"ended"|"failed"|"stopped",
           started_at, ended_at?, exit_code?, last_line? }
```

| method | params | result |
|---|---|---|
| `tasks.get` | `{id}` | `{page: TaskPage}` |
| `tasks.start` · `tasks.hold` · `tasks.decline` | `{id}` | `{}` proposed-task card actions |
| `tasks.typing` | `{id}` | `{}` holds the clock while the person types in the card |
| `tasks.decide` | `{id, verdict: "accept"\|"not_right"\|"recheck"\|"let_it_decide", text?}` | `{}` Your call (Tell it… = `not_right` or a note with text) |
| `tasks.note` | `{id, text}` | `{}` |
| `tasks.stop` | `{id}` | `{}` |
| `quick.undo` | `{id}` | `{}` or error `conflict` with the changed files |
| `quick.promote` | `{id}` | `{task}` Make it a task |
| `processes.list` | `{agent}` | `{processes: Process[]}` running and ended (7 days) |
| `processes.output` | `{id, from_line?}` | `{lines: string[], next_line}`; live tail via `process.output` events only while subscribed |
| `processes.follow` / `processes.unfollow` | `{id}` | `{}` |
| `processes.stop` · `processes.rerun` | `{id}` | `{}` / `{process}` |
| `agents.move_to_task` | `{id, turn}` | `{task}` the running reply continues as a task |
| `power.sleep` · `power.wake` | – | `{}` sent by the app on macOS sleep/wake |

Events: `quick.changed {quick}`, `process.changed {process}`, `process.output {id, lines}`
(followed only), `turn.long {agent, turn, elapsed_s, steps}` once per reply at 180 s,
`agent.changed` gains state `asleep`.

Rules: in app mode the engine never moves a running reply to a task by itself (dev's
checkpoint auto hand-over is off); it emits `turn.long` and waits for `agents.move_to_task`.
Proposed tasks start on their own after `proposed_task_clock_s` unless held, typed in or
answered; irreversible tasks have no clock. Quick tasks: > 30 files or any test run → `grew`.

## Work log detail (V63 · An agent at work, 20+ tool calls)

The app groups, folds, pins and tails tool rows itself (deterministic rules on V63);
the engine supplies the facts per call. `turn.tool` and tool `Entry` gain:

```
kind: "read" | "search" | "run" | "edit" | "web" | "connector" | "task" | "asks_first" | "other"
started_at, ended_at?            wall clock; duration = ended - started
batch?                           same value for calls the agent started together (parallel)
exit_code?                       run
retried_of?                      call id this one re-runs (a later pass of the same command)
files?: [{path, add, del}]       edit
connector?                       e.g. "GitHub" (connector kind)
task?                            task id (task kind)
ask?                             ask id (asks_first kind: the log pauses here)
input?: {cwd?, timeout_s?, text} what was asked (command, query, path)
tail?: string[]                  last ≤3 output lines while running, ≤12 when ended
lines?                           total output lines
```

| method | params | result |
|---|---|---|
| `tools.output` | `{agent, call, from_line?, limit?}` | `{lines: string[], next_line, total}` full output of one call (works after the turn) |
| `tools.follow` / `tools.unfollow` | `{agent, call}` | `{}`; live `tool.output {agent, call, lines}` events only while followed |

`turn.tool` is sent at begin, at end, and at most every 500 ms in between with a
fresh `tail` for calls running longer than 5 s.
