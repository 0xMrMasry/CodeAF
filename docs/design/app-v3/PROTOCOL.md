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
Task   { id, agent, title, state, updated_at }
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
