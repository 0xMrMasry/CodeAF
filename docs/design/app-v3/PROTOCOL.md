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
tool_kind: "read" | "search" | "run" | "edit" | "web" | "connector" | "task" | "asks_first" | "other"
                                 (named tool_kind so it never clashes with Entry.kind)
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

## Wave 3b: conversation, dock, queue, tasks as chats (V65–V70)

Boards: docs/design/boards/V65-TaskIsAChat, V66-TaskTree, V67-TurnStates,
V68-DecisionDock, V69-ComposerQueue, V70-ConversationBehaviour.

Entries and turn events (adds):
```
Entry kind "update"   { text }   interim mid-turn message (eyebrow "Update"), full markdown
Entry kind "steer"    { text, state: "pending"|"consumed"|"fell_through", step? }
Entry kind "note"     { task, text, from: "you"|"task", state?: "sending"|"waiting"|"read", read_at_step? }
Entry kind "summary"  { text }   "Earlier messages summarized" (engine compaction point)
Entry kind "footer"   { text, footer_kind: "stopped_by_you"|"retrying"|"provider_failed"|"fallback"|"process_finished",
                        retry_in_s?, model_from?, model_to? }
Entry (user) adds     attachments?: [{kind: "file"|"link"|"paste", path?, url?, title?, lines?, add?, del?,
                                      state?: "exists"|"changed"|"missing"|"outside"}]
turn.thinking         {agent, turn, text}  live only, never saved; turn.thinking_done {agent, turn, seconds}
turn.retrying         {agent, turn, in_s, attempt}   (2 s, 4 s, 8 s), then provider_failed or fallback
```

Asks (the decision dock, V68) add:
```
Ask { …, source: {kind: "reply"|"task", task?}, reason?, evidence?: {lines: string[], more: int},
      choices?: [{id, label, detail?, suggested?: bool}], clock_until?, irreversible?,
      batch?: [{id, text, state?: "allowed"|"denied"}], holding_up?: [task ids], say_why?: bool }
```
| method | params | result |
|---|---|---|
| `asks.answer` (adds) | `{…, why?, batch_item?, later?: bool, you_decide?: bool}` | `{}` |
| `asks.hold` | `{agent, id}` | `{}` stops the choice clock (H / typing) |

Queue and steer (V69):
| method | params | result |
|---|---|---|
| `agents.steer` | `{id, text, client_id}` | `{}` lands after the current step (`steer` entry pending → consumed / fell_through) |
| `agents.queue` | `{id, text, client_id}` | `{queued: Queued[]}` sent when the turn ends |
| `queue.edit` · `queue.remove` · `queue.move` | `{agent, id, text?/to?}` | `{queued}`; edit after send → error `sent` |
Events `queue.changed {agent, queued: [{id, text}]}`.

Files and links in the composer (V69 @ reference, chips):
| method | params | result |
|---|---|---|
| `files.find` | `{agent, query, limit?}` | `{groups: [{label: "Changed in this chat"\|<repo>, files: [{path, add?, del?, dir?}]}]}` |
| `files.stat` | `{agent, paths}` | `{files: [{path, state, add?, del?, size?, binary?}]}` |
| `attachments.add` | `{agent, kind, path?/text?/url?}` | `{attachment}` paste over 12 lines or 2,000 chars → paste card |

Tasks as conversations (V65, V66) add:
```
Task { …, paused?: bool, waits_on?: [task], holding_up?: [task], model?, steps?, cost_usd?,
       tokens?, started_at?, started_by?, instructions?, children_done?, children?, finished_at? }
```
| method | params | result |
|---|---|---|
| `tasks.pause` · `tasks.resume` · `tasks.retry` | `{id}` | `{}` |
| `tasks.brief` | `{id, text}` | `{}` Change the brief (read at its next step) |
| `tasks.note` (adds) | `{id, text, client_id}` | note entry; `note.changed {task, id, state, read_at_step}` |
| `tasks.entries` | `{id, before?, limit?}` | the task's own conversation (log, notes, its sub-tasks) |

Limits the engine enforces and reports: 16 processes per agent (17th fails with a footer
"16 processes are running for this agent."); process/tool output kept to the last 512 KB
("… earlier output trimmed to the last 512 KB").

## Wave 4: Inbox (V04, V68 "The dock and the Inbox")

Every Ask from every agent (held or not), task and crew is one Inbox item. The
Inbox needs agents with open asks to stay held (never idle-released while an ask is open).

```
InboxItem { id, ask: Ask, where: {agent, crew?, task?, path: string[]}, at,
            list: "waiting"|"later"|"done", answered?: {option?, text?, by, at, where} ,
            context: Entry[] }   context = the two turns before the question
```
| method | params | result |
|---|---|---|
| `inbox.list` | `{list, query?}` | `{items: InboxItem[], counts: {waiting, later, done_today}}` |
| `inbox.answer` | `{id, option?, text?, why?}` | `{undo_until}` the answer is held 5 s ("Approving… Undo") then sent |
| `inbox.undo` | `{id}` | `{}` within the 5 s window: nothing is sent, item returns to Waiting |
| `inbox.later` · `inbox.back` | `{id}` | `{}` Later / back to Waiting |
| `inbox.open` | `{id}` | `{agent, task?}` where to navigate |

Events: `inbox.added {item}`, `inbox.resolved {id, by: "app"|"elsewhere"|"stopped"|"no_longer_needed", receipt}`,
`inbox.counts {waiting, later, done_today}` (drives the rail badge and the macOS Dock badge).
Answering anywhere (dock, task, crew, Inbox) resolves it everywhere within 1 s.

## Wave 5: Crews (U3, X2, V01, V02, V15, V15b, V15c, V16–V20, V64)

A crew IS a codeaf team (`internal/teams`). The data is the engine's own: the same
`<profile>/teams.json`, the same per-team Traffic log `<profile>/teams/<id>/traffic.jsonl`, the
same decisions file, the same usage ledger. The app adds no store and no fields of its own; it
renames only the words the UI shows (team → crew). A crew made in the app is a team in the
terminal's Teams page, and the reverse.

The app process does what the terminal window does for teams it holds
(`internal/tui3/teamtraffic.go`): it performs a manager's `start` and `stop` lines for members it
holds, and it keeps every member of an open crew held while the crew is open (never idle-released).
Closing a crew does NOT stop its conversations (the engine's Disband); the app only stops holding them.

### Crew ⇄ Team

| Crew (wire) | Team (`internal/teams`) | Notes |
|---|---|---|
| `id` | `Team.ID` | random, minted once (`teams.NewID`) |
| `name` | `Team.Name` | unique among open teams (the TUI refuses a duplicate) |
| `parent?` | `Team.Parent` | sub-crew = child team; `crews` nest to `depth_limit` |
| `root` | `Team.Root` | the "All teams" row; never closes |
| `manager?` | `Team.Manager` | the manager's conversation key, sent as its agent id; always a member |
| `hue`, `tier` | `Team.Hue`, `Team.Tier` | OKLCH hue degrees and lightness tier 0/1 |
| `tint` | derived from `Hue` | see Tint below; never stored |
| `made_at` | `Team.Made` | |
| `state` | `Team.State` + live members | `closed` when `Team.State == "closed"`; otherwise `needs_you` / `working` / `idle` from members (below) |
| `closed_at?`, `closed_with?` | `Team.ClosedAt`, `Team.ClosedWith` | `closed_with` is the crew whose close closed this one (itself or an ancestor) |
| `report?` | `Team.Report` | id of the closing-report packet; the Report itself is that packet's `report` |
| `wrap?` | `Team.Wrap` | `{started, bound_s}` while a wrap-up runs |
| `settings` | `Team.Settings` | the crew's own overrides, each nullable: `questions_up`, `cap_usd_day`, `depth_limit`, `sub_share`, `wake` |
| `effective` | `File.Effective(id, Defaults)` | each value with its `*_from` origin (`team`/`ancestor`/`settings`/`closed`) for "· from Settings" |
| `spend` | `teams.TeamSpend(profile, id, today)` | `{day, usd, calls, by_member}`; a crew's pool counts every crew under it |
| `members` | `Team.Members` | see Member |
| `former_members` | `Team.FormerMembers` | handles kept for history links; no membership |
| `brief?` | parent Traffic `start` line with `team == id` | the words the sub-crew's manager was started with (derived, read-only) |

| Member (wire) | Team member | Notes |
|---|---|---|
| `agent` | `Member.Key` → agent id | the conversation; also listed in `agents.list` with `crews: [id]` |
| `crew` | the team id | |
| `handle` | `Member.Handle`, `Member.HandleBy` | 2–12 lowercase, unique in the crew |
| `title`, `workspace` | `Member.Word`, `Member.Where` | |
| `joined_at` | `Member.JoinedAt` | |
| `started` | `Member.Started` | made by the manager's `team_start` |
| `home`, `independent` | `Member.Home`, `Member.Independent` | which membership reports to a manager |
| `reports_to?` | `File.Home(key)` | `{crew, manager, distance}`: the manager this member answers to |
| `also_in` | other teams that hold the key | derived |
| `state` | live state of the member's conversation | `running` / `asking` / `idle` / `finished` / `failed` (the engine's `MemberState` words); the latest Traffic `event` line says the same |
| `line?` | the member's live one-liner | the agent's running line, same as `agents.list` |
| `spent_usd` | `Spend.ByMember[key]` today | |
| `model` | the member conversation's model | from the agent, not the team |

**Tint** (derived, read-only): the UI's named tints are the nearest anchor to `Hue`, in OKLCH
degrees: `rose` 15, `sand` 75, `sage` 145, `tide` 215, `iris` 285; `graphite` is the root crew and
any crew with no colour yet. Setting a tint writes the anchor's hue (`Team.SetHue`, tier kept);
a new crew takes `teams.NextHue` (the farthest free hue) unless the UI picks one, and the five
`hue_choices` the terminal offers come from `teams.HueChoices`.

**LogLine** is a Traffic `Entry` as stored: `{id, crew, at, kind, from, to, text, handles?,
answers?, files?, member?, state?, packet?, reply?, team?}` with kinds `event`, `note`,
`directive`, `stop`, `start`, `question`, `answer`, `packet`, `close`, `reopen` (and `you`).
`from`/`to` are handles or `manager`/`you`/`system`/`everyone`/`room`/`several`. The app adds
`agent` (the member's agent id) when `member` names one.

**Decision** is a decision packet (`teams.Packet`) as stored: `{id, crew (Origin), waits_on
(Team: a crew id or "you"), kind: question|conflict|cap|judgement|closing, raised_by, parties,
question, options: [{id, label, consequence}], recommendation?: {option, reason}, report?, cap?,
state: open|escalated|decided, decided_by?, decision?, reason?, trail: [{from, to, by, reason?,
at}], raised, at}`. A packet waiting on you is also an Ask (`kind: "question"`, `subkind:
"crew_decision"`, `path` = the crew names from `Origin` up the `trail` to you, "Backend → Release
→ you"), so it reaches the dock and the Inbox. A member's permission prompt never becomes a
packet: it stays the member's own ask, as in the terminal.

**Report** is `Packet.report` of a `closing` packet: `{done, left?, files?, spend_usd?,
incomplete?}` with options `close`, `close-now`, `keep-going`.

**Cap**: a `cap` packet carries `{crew, day, cap_usd, spent_usd, raise_to}` with options `raise`
and `stop`.

| method | params | result | engine call |
|---|---|---|---|
| `crews.list` | `{closed?: bool}` | `{crews}` | `File.Open()` / `File.ClosedTeams()` |
| `crews.get` | `{id}` | `{crew}` | `File.Team` + `Effective` + `TeamSpend` |
| `crews.create` | `{name, agents: [id], manager?: id, parent?, hue?}` | `{crew}` | `Update`: a new `Team` with `NewID`, `AddMember` per agent, `SetManager`, `SetParent` (refused past `depth_limit`: `CanNest`) |
| `crews.update` | `{id, name?, hue?/tint?, settings?}` | `{crew}` | `Update`: rename, `SetHue`, `SetSettings` (bands: cap ≥ 0, depth 1–10, share (0,1]) |
| `crews.message` | `{crew, to: "manager"\|agent, text, client_id}` | `{entry}` | the person's words go INTO that conversation (`agents.send` to the manager's or the member's agent), exactly as typing in its chat; the engine never writes the person's words to Traffic, so a manager does not see words sent to a member |
| `crews.log` | `{id, after?, limit?}` | `{lines, cursor}` | `ReadTraffic` (oldest first, cursor = last id) |
| `crews.decisions` | `{id?, all?}` | `{decisions}` | `Packets(id)` / `OpenPackets(scope)`; `all` includes decided ones (`History`) |
| `crews.decide` | `{packet, option?, text?, reason?}` | `{decision}` | `Decide(id, "you", …)`; a decided `closing` packet with close/close-now then `AcceptClosing` |
| `members.add` | `{crew, agent, handle?}` | `{member}` | `AddMember` (+ `SetHandle`), as `team_add` |
| `members.stop` | `{crew, agent, reason?}` | `{}` | Traffic `stop` line; the app stops the member's running turn (nothing is deleted) |
| `members.remove` | `{crew, agent}` | `{}` | `RemoveMember`; never offered for the manager (the store clears the manager when removed) |
| `members.handle` | `{crew, agent, handle}` | `{member}` | `SetHandle` |
| `members.home` | `{crew, agent}` | `{member}` | `SetHome`: which manager this member reports to |
| `crews.manager` | `{id, agent}` | `{crew}` | `SetManager` (the agent must be a member) / `ClearManager` with `agent: ""` |
| `crews.move` | `{ids, parent}` | `{}` | `MoveCheck` then `Move` (re-parent crews; `""` = top level) |
| `crews.wrap_up` | `{id, text?}` | `{}` | `WrapUpRequest(text)` to Traffic; the manager wakes and raises a `closing` packet → `decision.opened` |
| `crews.close` | `{id}` | `{}` | `Disband` (cascades to sub-crews; conversations keep working) + Traffic `close` |
| `crews.reopen` | `{id}` | `{crew}` | `Reopen` (refused while the parent is closed) + Traffic `reopen` |
| `crews.delete` | `{id}` | `{ids}` | `Delete` (closed crews only; conversations are never deleted) |
| `crews.defaults` · `crews.set_default` | – / `{key, value}` | `{defaults}` | `DefaultsAt` / `config.ApplyTeamDefault` (the five `teams.` rows) |

Events: `crew.changed {crew}`, `crew.removed {id}`, `crew.log {line}` (every new Traffic line),
`decision.opened {decision}`, `decision.changed {decision}`, `member.changed {member}`. A crew's
`state` moves to `needs_you` while a packet waits on you from it or a member is asking.

**Not in the engine (needs engine work before the UI may show it):**
- Roles and role copies (`roles.*`, `Role`): teams have handles, not roles.
- A per-member budget (`cap_usd`): caps are per crew and pool down the tree.
- Pausing a member (`members.pause/resume`): there is only stop.
- Linked items (`Item`, `crews.items/link/unlink`, LogLine `factory`): teams keep no links.
- A crew goal, end date (`goal`, `ends_at`, Proposal `span`) and a manager plan text (`plan`).
- The "Bring in helpers" proposal card (`crews.propose`, `Proposal`, option `keep`): no engine
  tool proposes a crew; a crew is made by the person (`crews.create`) and grows by the
  manager's `team_start`.
- A member started by the person with a brief (`members.start`): a Traffic `start` line and its
  brief are read only when they are the manager's. The person adds an existing agent
  (`members.add`) or asks the manager, whose `team_start` the app performs.
- A person's message to a member that the manager also sees: the person's words are never
  written to Traffic.
- Member `was` and crew `history`.

## Wave 7: closing, archiving, finding (V21, V22, J03, J04, X4)

Agent adds `closed_at?`, `archived_at?`, `last_active_at`, `spent_usd`.
| method | params | result |
|---|---|---|
| `agents.list` (adds) | `{state?: "open"\|"closed"\|"archived"\|"all", query?}` | closed/archived only when asked |
| `agents.move_to_closed` | `{ids}` | `{}` "Move to Closed": stops its engine, keeps everything, leaves the rail at once |
| `agents.archive` | `{ids}` | `{}` |
| `agents.reopen` | `{id}` | `{agent}` exactly as it was |
| `agents.rename` | `{id, title}` | `{agent}` |
| `agents.idle` | `{days}` | `{ids}` agents idle longer than `idle_close_days` (Select idle) |
| `search.all` | `{query, limit_per_group?}` | `{groups: [{kind: "agent"\|"crew"\|"task"\|"message", hits: [...]}]}` ⌘K / All crews and agents |
Tidy commands are undoable until quit: the app keeps the inverse call.

## Wave 6: Standing orders and reminders (V40, U2 7–8, X1)

```
Order { id, agent, crew?, kind: "order"|"reminder", when, does, wont?, answers_in: "chat"|"crew"|"everywhere"|"not_here",
        trust: "asks_first"|"earning"|"trusted", approved_runs, budget_per_run_usd?, state: "proposed"|"active"|"paused"|"stopped"|"done",
        last_run?: {at, result: "ok"|"not_right"|"skipped"|"missed"}, fires_at? }
```
| method | params | result |
|---|---|---|
| `orders.list` | `{agent?}` | `{orders}` |
| `orders.ratify` · `orders.change` · `orders.not_now` | `{id, edits?}` | `{order}` nothing runs before Ratify |
| `orders.pause` · `orders.resume` · `orders.stop` | `{id}` | `{order}` Pause keeps the trust level |
| `orders.run` | `{id, verdict: "go"\|"skip"\|"not_right"}` | `{}` per-run ask answers; Not right drops trust to asks_first |
Events: `order.changed {order}`, `order.fired {order, run}`, `order.missed {order}` (Mac asleep).
Trust: asks_first → earning (n of 5) → trusted after 5 approved runs.

## Wave 8: Settings window (V42–V44, V60, V24)

`settings.get/set` cover every row on V42–V44 and V60 (General: appearance, text size, open at
login, dock badge; Models: default, pinned (⌥⌘1–3), Checks and Planner roles; Spending: day cap,
per-agent budget, task cap, export; Memory: list/forget; Permissions: always-allow rules list/remove;
Accounts: providers (keys via the Keychain), GitHub; Advanced: proposed-task clock, tidying numbers,
work folder, fallback model). Extra methods: `rules.list`/`rules.remove`, `memory.list`/`memory.forget`,
`spend.export {from, to}` → CSV path, `models.pin {ids}`.
