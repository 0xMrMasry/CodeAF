# codeaf factory — design brief for the dashboard (and the TUI page)

Status: 2026-10-10. For the product designer who owns the UX, UI and theme of the factory dashboard (web) and may touch the TUI page.
Sources: `PRODUCT.md`, `FACTORY-YAML.md`, the wireframe `.scratch/factory-dashboard.html`, `internal/factory/types.go`, `internal/factory/seam.go`, `internal/manual/chat/factory.md`.
Where this brief and an older source disagree on a word, this brief wins (see §6).

---

## 1. What this is

**The factory** is a service that turns a project's open work (issues, pull requests, red CI runs) into reviewed pull requests. It reads the forge (GitHub), decides under rules kept in the repo what may start, hands each job to one person's machines, and records everything that happened. It never executes code, never holds code, never holds accounts, never holds money.

**codeaf** is the one binary a person installs. It has two sides: the **TUI** (chat with a running job, steer it, approve at the keyboard) and the **worker** (pulls jobs from a factory and runs their steps in git worktrees with the person's own model keys and forge access). One person on a laptop and a company on a shared box run the same two programs; the deployment changes capacity and reach, never features.

**The dashboard** is the factory's web page, served by the factory process itself. It is where a person *looks and decides* across jobs, repos, people and time: what needs me, approve, answer, add work, drop, take. It is phone-first. It is not a chat app, not an inbox, not an admin console.

### Philosophy — the agentic loop

Every screen is one turn of one loop:

1. **The model works** — a step is a conversation with a model that has tools, inside the job's worktree.
2. **The model reports** — in markdown, plus claims with evidence, plus questions it could not settle.
3. **The person judges** — approve, request changes, answer, drop. Always a person before anything leaves the machine.
4. **The factory remembers** — every start, take, answer, approval, post and cost is an append-only audit event.

**Generic, bounded only by the loop.** The product fixes the loop's primitives (job, step, result, claim, question, approve, rules) and nothing else. A security gate, a migration check, a performance budget are not features — they are prompts a team writes, and their results appear where every result appears. The UI must therefore never special-case a step by its name (except `approve`, the one reserved step that is a person). No step kinds, no risk scores, no block types, no plugin panels.

---

## 2. Who uses it and when

| user | setting | what they want from the dashboard |
|---|---|---|
| **Solo developer** | factory on `localhost:7420`, one laptop = one worker, one viewer, no sign-in | glance from the phone on the couch: what needs me, approve, answer; see what ran and what it cost |
| **Teammate** | team factory on a shared box (`factory.acme.internal`), their own codeaf attached as a worker | their jobs and the questions routed to them; take unassigned work; see who holds what; overlap warnings |
| **Operator** | the person who runs `factory serve` on the box and edits its host config | read-only confirmation in Settings → This factory (GitHub connection, access mode, repos, members). Operations happen in the CLI, never in the dashboard |

### The journeys (current vocabulary)

1. **Solo, day one.** Install codeaf → `/factory` in the TUI → connect GitHub, pick repos → issues stream in as backlog jobs. A job inside `start:` rules auto-starts; or the person starts one from the TUI. Approve the plan, approve the proof → the PR opens as the person. Open `http://localhost:7420` on the phone to approve from the couch.
2. **Solo, second machine.** `codeaf attach <url>` on the desktop. One factory, two of the person's machines; the desktop takes the next queued job. Visible only under You → Your machines.
3. **Team setup.** One person runs `factory serve` on a box, connects GitHub, commits `.codeaf/factory.yaml`. Teammates open the dashboard (access token or tailnet, then Sign in with GitHub), run `codeaf attach <url>`. Nobody holds the same job twice.
4. **Take.** A teammate opens Unassigned, presses **Take** on a job → it is now theirs and runs on their machines ("runs on your machines").
5. **Routed question.** A step asks something about `billing/`; the job's manager could not answer it, so it is routed to the CODEOWNER of `billing/` (@raj). Raj sees it under Mine → Needs you and answers in one line.
6. **Overlap.** Two running jobs change the same file; both job briefs show an Overlap alert naming the file and the other job.
7. **Judging from a phone.** A job reaches its proof approve; the person reads results and claims, presses "Approve → open PR #1498 as @priya"; the factory posts with a trailer bound to that approval event.
8. **Policy check.** Settings → Repos → acme/web shows `.codeaf/factory.yaml` read-only and a dry run: "4 issues would auto-start now · 1 would ask first: #2077 touches auth/".
9. **A lesson.** A reviewer rejects a PR for a reason → the dashboard offers to add it to the rules → a PR to `.codeaf/factory.yaml` (later phase; not in the mock).

---

## 3. Two surfaces and the line between them

| | **codeaf TUI** (factory page + job page) | **dashboard** (browser) |
|---|---|---|
| is for | working inside one job | looking and deciding across jobs, people, time |
| holds | every conversation (the job's manager, each step's chat), steering, logs, keyboard approval, money caps | queue views, job brief, audit log, settings, sign-in |
| verbs | all of them | approve · request changes · answer · reply (one line) · take · add (New job) · drop · open on GitHub · export |
| never | — | rich chat, process control (run/pause/stop), machine admin, policy editing, money settings |

Rules of the line:
- **Conversation lives in the TUI.** The dashboard takes reply-level answers to questions and a one-shot reply to the manager; footer copy says "deep steering happens in the TUI".
- **Policy lives in git.** The dashboard renders and dry-runs `.codeaf/factory.yaml`; it never edits it.
- **Money lives in codeaf.** The dashboard shows cost; it never sets a cap.
- **Operations live in the CLI** (`factory serve`, `codeaf attach`, `factory sessions revoke @handle`). A stolen browser session can judge, never administer or execute.
- **Not an inbox.** "Needs you" is the list of gates the pipeline created (approve steps, questions routed to you). No feed, no read marks.

### What the TUI shows today (keep them consistent)

The TUI factory page exists and is mature; the dashboard should feel like the same product. (Its on-screen labels still use several retired words — see §6; they will be renamed to match.)

- **Layout by width:** ≥120 cols = a since-you-last-looked line, then rows (58%) + a *peek* of the selected job on the right; 90–119 = rows only; <90 = mark, short name, title.
- **Summary line:** `◆ 2 merged · 3 arrived · ? 5 waiting · $8.44 / $60 · polled 14s ago` (expandable to four rows with a 24h sparkline).
- **Groups, in order:** NEEDS YOU · (landed, waiting for approval) · RUNNING · NEW (backlog) · DONE. Empty groups are not drawn.
- **A row:** state mark · priority glyph (`▇▅▃▁`) · short name `#1538` right-aligned · title · repo · facts (`bug · S`, `$1.42/$5`, author, tags like `thin`, `dup #950?`) · age `12m`.
- **Step marks:** `●` done · `◐` running · `?` waiting on you · `✕` failed · `○` to come · pause mark · `■` stopped · `–` skipped. Loop: `↻ 1/2 until clean`.
- **Peek blocks:** title + meta, the open question with `[y] yes · [n] no · [a] in words`, one-sentence read, facts, steps strip, running line (`review 1/2 · 3 findings · fixing`), description, claims (when landed), comments/checks/files/activity from GitHub, then at most five verbs.
- **Job page:** breadcrumb `Factory › codeaf › #1551 …`, run control (`▶ run` / `= pause` / `▶ continue`), left column (issue · manager · steps with loop info · log · result · settings), center = the selected part (a step's chat, the proof sheet, settings table).
- **Proof sheet:** one row per claim, `✓` shown or `✕ — not shown`, evidence dim, medium chip (`test`, `screenshot`, `policy`); last line `all 6 shown · s approve` or `1 of 6 not shown · e approve with changes · B request changes`.
- **Keys:** `↑↓` walk · `enter` open · `y/n/a` answer (at an approve: continue / send back / in words) · `s` approve · `B` request changes · `v` re-run checks · `T` chat · `S` steer · `space` pause · `g` GitHub · `/` filter · `[ ]` repo · `?` key sheet · `esc` back.

The dashboard uses `j/k` (not arrows) because the browser owns arrows; `y`, `a`, `enter`, `?`, `esc` match.

---

## 4. Information architecture

The mock's structure is listed as-is; it is a wireframe, not a decision (see §7 for what you may change).

### 4.0 Shell
- **Factory address** (always visible, top of sidebar; topbar on phone): host + one line. Solo `localhost:7420 · this laptop · 1 worker`; team `factory.acme.internal · 4 members · 3 workers`. Click → Settings.
- **New job** button.
- **Views:** Mine (badge = count needing you) · Unassigned (badge = count) · Everything.
- **More:** Audit log · Settings.
- **Footer:** avatar + `@handle` + `this laptop` (solo) or `signed in with GitHub` (team).
- **Phone:** topbar with menu button → sheet with the same sidebar.
- **Toasts** (bottom): title + one line. **Dialogs**: approve, request changes, drop, keyboard shortcuts.

### 4.1 Queue views — Mine / Unassigned / Everything
| view | subtitle | contains |
|---|---|---|
| Mine | "Jobs you took, and anything waiting on you." | jobs assigned to you + jobs with a gate routed to you |
| Unassigned | "Jobs nobody has taken. Take one and it runs on your machines." | jobs with no person, not done |
| Everything | "The whole pipeline on this factory." | all visible jobs |

Above the groups:
- **Since line:** `since 08:12 · 2 merged · 3 need you · $8.44`.
- **Pipeline strip:** one chip per step with running/queued jobs, `proof 1 ⚑` (⚑ = an approve waits there). Empty: "No jobs in a step."
- **Repo chips:** toggle filter per repo `✓ acme/api`; a repo the viewer cannot see shows as `acme/infra · no access` (disabled).
- **Capacity line:** `1 queued for you · priya-mbp busy` (solo: `your laptop busy`).

**Groups, in order:** Needs you · Running · Queued · Backlog · Done (count beside each; empty groups hidden).

**Row columns:** Kind (ISSUE / PR / CI) · Job (`api#1498`) · Title · State phrase (+ `stuck` badge) · Step (progress bar + `k/n`) · Cost (`$0.94`) · Age (`58m ago`; Done shows time `09:41`) · Taken by (team only: avatar + `@handle`) · Action.

**Row action:** unassigned → **Take** ("runs on your machines"); Needs you → **Approve** (gate is approve) or **Answer** (gate is a question).

**State phrases** (exact strings the mock computes):
| condition | phrase |
|---|---|
| done | `merged` · `dropped` |
| needs you, approve | `approve plan` · `approve proof` |
| needs you, question | `<step> · asks` |
| waits on someone else | `<step> · asks @raj` · `approve plan · @priya` |
| running, no progress | `stuck 14m` |
| queued, nobody | `waiting for someone to take it` |
| queued, owner offline | `waiting for @raj's worker (offline)` |
| queued, owner online | `waiting for your laptop` (solo) · `waiting for priya-mbp` |
| backlog | `not started` · `not started · nobody has taken it` |
| running otherwise | the step name (`write`) |

**Empty states:** Unassigned solo "Nothing unassigned. On this laptop every job is yours."; team "Nothing unassigned. New issues land here until someone takes them."
**Footer hint:** `j/k move · enter open · y approve · a answer · ? all keys`.

### 4.2 Job brief
Order in the mock: Back · alert · header · step rail · Results · Claims · Questions · codeaf · Cost · action bar.

**Header**
- `acme/api #1498` (mono), title (h1).
- Badges: kind · state (Needs you / Running / Queued / Backlog / Done) · `step 8/8` · cost `$0.94` · age (`58m ago` / `merged 09:41`) · `stuck`.
- Team: `assigned to @priya · priya-mbp` (+ `(offline)`), or "Not taken by anyone yet."
- Needs you: **waiting on you since 11:52 (34m)**.
- Asked-first backlog job: `asked first: touches auth/ · rule start.never: auth/**`.
- PR line: `PR #1513 · ci ✓ · opened as @santosh`.
- Button: **View on GitHub**.

**Step rail** — the job's pipeline in order. Each step: mark + name + `(fixed)` + `· custom` (not built-in) + loop `↻ 3/3 until every claim has evidence`. Marks: `✓` done · `⚑` waiting on a person · `●` running · plain = pending · all muted on backlog. Hint: "fixed: a job can never drop approve or proof".

**Alerts** — after approve: "Approved — Approved as @priya · recorded as evt_a81 · the PR will carry the trailer" + "Back to Mine". After request changes: "Changes requested — Recorded as evt_a82 · the manager sends the job back to plan". Team overlap: "Overlap — Overlaps `api/billing/invoice.go` with #1509 (@mei, running)" (link to the other job).

**Results** — plan line `planned 4 files · changed 4`; then one collapsible card per finished or current step: name · loop · `custom step` / `fixed` badges · `running` / `waiting` badge · first line of the result · finish time · cost; body = rendered markdown. Last two open by default. Empty: "No results yet. Each step adds a summary here when it finishes."

**Claims** — table Claim · Evidence · Proven. Evidence is mono (`tests · 41 ✓ · log`, `screenshot webhooks-page.png`, `benchmark p95 212ms −3%`) or "none". Proven = `✓ proven` or `unproven` (destructive outline). Empty: "Claims are written at proof, step 8/8."

**Questions** — note "Questions go to the job's manager first; you see the ones it could not answer." Each: question text · `from security` badge · `open`/`answered` badge · `about billing/` · `routed to @raj (owner of billing/ via CODEOWNERS)` (solo: "routed to you") · one-line answer input + **Answer**, or the answer `@priya: 5. That matches the provider SLA.` Empty: "None for this job."

**codeaf (the manager thread)** — "This job's one manager thread. Every step's question goes to the manager first." Messages: avatar (`af` for manager) · author · time · optional event id · markdown. Reply textarea ("Message the manager") + **Reply**; footer "Delivered to this job's manager in codeaf on priya-mbp · deep steering happens in the TUI". Empty: "The manager starts when someone takes this job."

**Cost** — table Step · Finished · Cost, Total row; "Paid with @priya's keys on priya-mbp. The factory only shows cost." Empty: "Cost appears once a worker starts the job."

**Action bar** (sticky; hidden on Done): **Approve** (label states what it does; disabled unless an approve waits) · **Request changes** (disabled on backlog) · **Answer** (disabled with no open question) · spacer · **Drop** (ghost destructive).

Approve labels:
- plan gate: `Approve plan → continue to write`
- proof gate, issue: `Approve → open PR #1498 as @priya`
- proof gate, PR: `Approve → mark PR #1513 ready as @priya`

**Dialogs**
- Approve: title = label + "?" · `acme/api #1498` · "Approving the proof posts the PR. 3 of 4 claims have evidence; 1 is unproven." (or "Approving the plan lets the job continue to write.") · "Recorded in the audit log as @priya. The PR carries the approval as a trailer." · Cancel / label.
- Request changes: "The manager gets your reason and sends the job back one step." · Reason textarea · Cancel / Request changes.
- Drop: "The job stops and its branch is deleted. The issue stays open on GitHub. Cost so far stays in the audit log." · Cancel / Drop job.
- Keyboard shortcuts: table of keys.

### 4.3 New job
"Opens an issue on GitHub and puts the job in your Backlog." Card "Send a job to the factory" / "Auto-start rules from .codeaf/factory.yaml decide when it runs." Fields: Repository (select, only repos you can see) · Title · Description · switch **Auto-start when rules allow** (default on) · **Send to factory**. Result: toast "Sent to factory — acme/web #2111 is in your Backlog", row flashes in Mine.

### 4.4 Audit log
"Every approval, answer, start, take and post. Append only." Tabs: All · Approvals · Answers · Starts · Takes. Table: Time · User (avatar; `factory` for automatic) · Action · Cost. **Export** (CSV: time,user,action,cost). Empty: "No events of this kind yet." Action phrases: `took #2099`, `auto-started #2085 by rule agent-ready`, `posted PR #1513 as @santosh`, `answered #2051`, `approved #1498 · evt_a74`, `requested changes on #1498 · evt_a82`, `merged #2051 · tests 63 ✓`, `asked first on #2077 · touches auth/`, `dropped #1484 · duplicate of #1490`, `polled acme/api, acme/web, acme/infra · 3 new issues`, `sent #2111 to the factory · auto-start on`.

### 4.5 Settings
Subtitle = address line. Two sections.

**This factory**
- **GitHub** — "How the factory reads issues and posts pull requests." `connected as @santosh via gh · polling every 60s`. Solo: Reconnect. Team: "edited by the factory operator on the host".
- **Access** — "How people reach this factory before they sign in." Solo `localhost only` + "no sign-in on this laptop"; team `access token` · `tailnet`.
- **Repos** — "Each repo carries its own pipeline file on main." One collapsible row per repo: `acme/api` · `.codeaf/factory.yaml` | `no file · using defaults` | `no access` · polling switch. Open: **Pipeline** badge `read-only · edit in the repo` (or `defaults`), the YAML read-only, **Dry run** card ("Under these rules, 4 issues would auto-start now: #2088 #2090 #2101 #2104 · 1 would ask first: #2077 touches auth/" · "Nothing starts from a dry run.").
- **Members** (team only) — "People who signed in with GitHub. No accounts: a session and a GitHub credential only." Handle · Sees · Last seen (`now · owner`, `now · you`, `09:40`).

**You**
- **Your machines** — "Workers running codeaf under your name. Each runs with your own keys." Name · Status (busy / idle / offline) · Current job · Takes (`takes unassigned: acme/api`). The only place machine presence is shown.
- **Attach** — "Run this on a machine to make it one of your workers and steer jobs from the TUI." `codeaf attach https://factory.acme.internal` + Copy.
- **Take unassigned jobs** — "Your machines pick up unassigned jobs from these repos." A switch per visible repo.
- **Sign out** (team) — "Ends this session. Your GitHub credential is removed from the factory."

### 4.6 Sign-in (team only; never at loopback)
1. **This factory is private** — "`factory.acme.internal` needs an access token, or a device on the tailnet." Access token input → Continue. (Skipped on the tailnet.)
2. **Sign in with GitHub** — "Open github.com/login/device and enter this code." Big code `WXYZ-1234` · "The code expires in 15 minutes." / "Waiting for GitHub…" · Copy code · I entered the code.
3. **Signed in as @priya** — "you can see: api, web" · "No account is created; a session and a GitHub credential only." · Continue → Mine.

---

## 5. Data model

All lists, counts, chips, strips and exports are computed **after** the viewer filter (repos the viewer's GitHub token can see). Every entity carries its repo.

### Factory
| field | type | example | shown |
|---|---|---|---|
| address | string | `factory.acme.internal`, `localhost:7420` | shell, Settings, Attach command |
| mode summary | derived | `4 members · 3 workers` / `this laptop · 1 worker` | shell |
| github | {handle, via, poll_s} | `@santosh`, `gh`, 60 | Settings → GitHub |
| access | enum[] | `localhost only` · `access token` · `tailnet` | Settings → Access |
| since | time + counts | `since 08:12 · 2 merged · 3 need you · $8.44` | queue header |

### Repo
| field | type | example | shown |
|---|---|---|---|
| name | `owner/name` | `acme/api` | chips, rows (`api#1498`), Settings |
| visible | bool (per viewer) | false → `no access` | chips, Settings |
| polling | bool | on | Settings → Repos |
| pipeline_file | PipelineFile? | `.codeaf/factory.yaml` or none (defaults) | Settings → Repos |
| dry_run | DryRun | — | Settings → Repos |

### Job  *(code: `Item`)*
| field | type | example | shown |
|---|---|---|---|
| id | string | `api-1498` | URL |
| repo, num | string, int | `api`, 1498 (no forge number → factory id) | row, header |
| kind | enum | ISSUE · PR · CI (code also: chore) | row badge, header |
| title | string | "Retry webhook deliveries with backoff" | row, header |
| body | markdown | issue text | (not in mock; GitHub link) |
| state | enum (§5.1) | running | group, header badge |
| assignee | handle? | `priya` / null | Taken by, header, routing |
| pipeline | step names | `[plan, approve, write, …, proof]` | step rail, `k/n` |
| current_step | name | `proof` | rail, Step column |
| waits | {kind: approve\|answer, on: handle, since} | approve, @priya, `11:52 (34m)` | phrase, Needs you, header |
| loop | {step, n, max, until} | proof, 3, 3, "every claim has evidence" | rail, result card |
| stuck_min | int? | 14 | `stuck` badge, phrase |
| plan_files | string | `planned 4 files · changed 4` | Results |
| diff_stat | string | `+164 −22 · 4 files` | (data only in mock) |
| pr | {num, by, ci} | 1513, santosh, ✓ | header PR line |
| overlap | {file, num, who} | `api/billing/invoice.go`, 1509, mei | Overlap alert (team) |
| asked_first | {why, rule} | `touches auth/`, `start.never: auth/**` | header (backlog) |
| cost | USD | 0.94 | row, header, Cost |
| age | relative/time | `58m` / `09:41` | row, header |
| results / claims / questions / thread | lists | below | job brief |
| url | string | GitHub page | View on GitHub |

Code-only fields not to surface: triage (type, size S/M/L, readiness, priority 1–5, dup), tier (owner/collaborator/stranger), origin (github/terminal/chat), labels, checks, comments, files, activity. Size and priority may appear later; **risk is never shown** (no risk model).

### 5.1 Job states
| state | code | meaning | next |
|---|---|---|---|
| **backlog** (new) | `new` | arrived, not started; may be asked-first by rules | queued (Take / auto-start / New job) · dropped |
| **queued** | `queued` | has a person, waiting for one of their machines | running |
| **running** | `running` | a step is executing on the person's worker | needs you · done · dropped |
| **needs you** | `needs you` / `landed` | an approve step or a routed question waits *on the viewer* (per-viewer: the same job is "running · asks @raj" for others) | running (approve/answer) · back a step (request changes) · done |
| **done** | `shipped` | final approve passed; PR opened/marked ready; `merged` | — |
| **dropped** | `dismissed` | stopped by a person; branch deleted; issue stays open | — |

### Step — definition  *(code: `Stage`)*
| field | type | example | shown |
|---|---|---|---|
| name | word | `security`, `migrate-check` | rail, results, strip |
| prompt | sentence | "if the change touches api/ or auth/, review it…" | YAML in Settings |
| until | sentence | `green`, `every claim has evidence` | loop text |
| max | int (default 1) | 3 | loop `n/max` |
| builtin | bool | `migrate-check` → false → `custom` | badge |
| fixed | bool | approve, proof | `(fixed)` |
| reserved | `approve` only | a person, no prompt | ⚑, Approve button |

Code-only, not surfaced: kind (chat/check/gate/post), when, effort, fanout, by, why.

### Step — run state  *(code: `Phase`)*
`pending` → `running` → `done` | `failed` | `waiting` (on a person: approve or question) ; `skipped` (switched off, or the run went past it). Plus `round` (the loop counter n of max). Paused and stopped exist in the TUI only.

### Result  (one per step that ran)
| field | type | example |
|---|---|---|
| step | name | `test` |
| finished | time / `now` | `11:39` |
| cost | USD | 0.14 |
| markdown | string | "`go test ./api/...` · **41 passed**, 0 failed in 38s." |
| current | bool | running / waiting badge |

### Claim
| field | type | example |
|---|---|---|
| text | sentence | "Retries use exponential backoff and stop after 5 attempts" |
| evidence | Evidence? | — |
| proven | bool | true / false (no evidence = unproven) |

### Evidence
| field | type | example |
|---|---|---|
| kind | open word | test · screenshot · benchmark · log · transcript · policy |
| ref | string | `41 ✓ · log`, `webhooks-page.png`, `p95 212ms −3%` |

Same claim text again updates the row; a later claim with evidence replaces one without; never the reverse.

### Question  *(code: `Asked`)*
| field | type | example |
|---|---|---|
| text | sentence | "Should invoices round half-up?" |
| about | path | `billing/` |
| from_step | name / `manager` | `write` |
| routed_to | handle | `raj` (CODEOWNERS of `billing/`) |
| state | open · answered | open |
| answer | {by, text, event} | `priya`, "5. That matches the provider SLA." |

### Message (manager thread)
| field | type | example |
|---|---|---|
| author | `manager` or handle | manager |
| time | time | `11:52` |
| markdown | string | "Proof is ready. 3 of 4 claims have evidence…" |
| event | id? | `evt_a82` (on request changes) |

### Approval / audit event
| field | type | example |
|---|---|---|
| id | string | `evt_a74` |
| time | time | `11:06` |
| actor | handle or `factory` | `priya` |
| kind | take · start · answer · approve · post · drop (request changes logs as approve) | approve |
| action | phrase | `approved #1498 · evt_a74` |
| cost | USD? | 1.38 |

An approval is {event id, handle, time}; the PR trailer is bound to it.

### Worker
| field | type | example |
|---|---|---|
| name | string | `priya-mbp` |
| person | handle | `priya` |
| status | busy · idle · offline | busy |
| current job | ref? | `#1513` |
| takes | repos | `acme/api` |

### Member / session (team)
| field | type | example |
|---|---|---|
| handle | GitHub login | `@raj` |
| sees | repos | `api, web, infra` |
| last seen | time / `now · owner` / `now · you` | `09:40` |

### PipelineFile
`.codeaf/factory.yaml` on main, keys `start`, `size`, `pipeline`, `steps`, `fixed`, `rules` (Appendix A). Rendered read-only.

### DryRun
| field | type | example |
|---|---|---|
| would_start | job refs | `#2088 #2090 #2101 #2104` |
| would_ask_first | {ref, why} | `#2077 touches auth/` |
| note | | "Under the defaults, nothing auto-starts. Jobs wait until someone takes them: #311" |

### Cost line
{step, finished, cost} per result + total; "Paid with <whose> keys on <machine>".

---

## 6. Vocabulary

| say | meaning | never say |
|---|---|---|
| factory | the service and its dashboard | — |
| dashboard | the factory's web page | floor, wall |
| TUI | codeaf's terminal UI | desk |
| job | one unit of work: an issue, PR or red CI run going through steps | item |
| pipeline | a job's ordered steps, per kind | recipe |
| step | one prompt run to its `until` | stage, phase |
| result | a step's markdown report | — |
| claim | a sentence a step asserts, with evidence | (the worker lease — never call that a claim in UI; say "taken by") |
| question | something the manager could not settle, routed to a person | — |
| approve | a person's go at an approve step | sign off, ship |
| request changes | send the job back one step with a reason | send back |
| drop | stop the job, delete its branch | dismiss |
| take / taken by | a person accepts an unassigned job | claim-desk, lease |
| manager | the job's one codeaf conversation | foreman (that was the whole-factory chat) |
| audit log | the append-only event list | record |
| Mine / Unassigned / Everything | queue views | — |
| Needs you / Running / Queued / Backlog / Done | groups | landed, shipped, new, streams |
| since … | what happened since you last looked | handover |
| rules / start rules | `rules:` and `start:` in the YAML | envelope |
| access token · tailnet · Sign in with GitHub | getting in | door, link |
| This factory / You | settings sections | — |
| your machines / worker | the person's attached codeaf instances | bench, pool |

---

## 7. Fixed vs yours

### Fixed (do not change)
1. The dashboard is for **looking and deciding**. All conversation and steering is in the TUI; the dashboard has reply-level answers only.
2. **One UI**, no solo/team mode switch. Team adds elements: sign-in once, who holds a job, routed questions, overlap warning, Members. **The factory address is always visible.**
3. A job runs on **its person's machines** (assignee, or whoever started/took it). **Take** on unassigned jobs. Machine presence appears **only under You → Your machines** — never a team roster of machines, never "worker offline" counts on the queue beyond the job's own phrase.
4. **Policy is a YAML file in the repo**, rendered read-only per repo with a dry run; never edited in the dashboard. **Money** (caps) is personal and set in the TUI; the dashboard shows cost only.
5. A step is a prompt; results are **markdown**. The only structured things: **claims** {text, evidence{kind, ref}, proven}, **questions** {text, about-path, from-step, routed-to, state, answer}, **approvals** {event id, handle, time}. No risk model, no block types, no per-step-name UI.
6. **Approve text states what it does at this gate** ("Approve → open PR #1498 as @priya"). Keyboard: `j/k`, `enter`, `y`, `a`, `?` (+ `esc`).
7. **No accounts**: a user is a GitHub handle; sign in = device flow once per device; sessions; access token or tailnet off loopback. No sign-in at all on loopback.
8. Every count, chip, strip, row and export is computed after the viewer's repo filter; a repo the viewer cannot see shows only as `no access`.
9. Implementation: **React + Tailwind + stock shadcn/ui**, embedded in a Go binary. Theme through CSS variables; no custom component library.
10. Not an inbox: no feed, no unread marks, no notifications UI in this phase.

### Yours
- Sidebar items, order, grouping, and whether views are sidebar entries, tabs or a segmented control.
- Tables vs cards vs list rows for the queue, per breakpoint; density; what columns survive on a phone.
- Job brief layout: one scroll vs tabs (PRODUCT.md proposed tabs proof · diff · checks · log · conversation · cost); a peek/split on wide screens (the TUI has one).
- Step rail form (chips, stepper, vertical list), mark glyphs/icons, how loops and `fixed`/`custom` read.
- Theme: palette tokens, light/dark (both required to exist; default yours), typography, iconography (lucide ships with shadcn), the `af` manager avatar.
- Empty, loading, error and offline states; motion; toasts vs inline confirmation.
- Phone layout (phone-first is fixed; how is yours), sticky action bar behaviour.
- Copy edits that keep the vocabulary.
- TUI factory page: you may propose changes that bring it in line (labels, group names, marks), within the TUI's widths and keys.

---

## 8. Open questions for you

1. Job brief: one long scroll (mock) or tabs (PRODUCT.md)? Where do diff, checks and log go — inline, tabs, or "open in GitHub / TUI"?
2. Wide screens: rows + a peek of the selected job, like the TUI, or always navigate to the brief?
3. Needs you: group inside each view (mock), or its own top-level view?
4. Should Done show dropped jobs (the mock removes them) and should Audit log get a Drops tab?
5. The repo **polling** switch and **Take unassigned** switches are writes in Settings. Polling is factory config (operator, host file): keep it read-only in team mode?
6. Cost column: show for every row, or only on running/done? Show a per-viewer day total anywhere (cap stays in the TUI)?
7. Kind badge: words (ISSUE/PR/CI), icons, or both?
8. How does a stuck or failed step look different from waiting-on-a-person?
9. The proof approve with unproven claims: plain Approve (mock) or a distinct "approve with unproven" affordance (the TUI has `e approve with changes`)?
10. Brand mark and the factory address block: how prominent on phone?
11. Any element you think breaks a Fixed rule — tell us; do not silently drop it.

---

## Appendix A — full pipeline file

```yaml
# .codeaf/factory.yaml — read from main; a PR can't change its own rules
start:
  labels: [agent-ready]          # default [] — nothing auto-starts until you name a label
  max_size: M                    # S · M · L
  max_cost: 2                    # USD per job; over → the job asks first
  never: [auth/**, migrations/**]
  ask_first:
    - anything that changes how secrets are read or stored
size:
  pr_lines: [50, 400]            # ≤50 S · ≤400 M · more L
  issue: model                   # model · label
pipeline:
  issue: [plan, approve, write, test, review, security, migrate-check, proof]
  pr:    [read, review, proof]
  ci:    [read, write, test, proof]
steps:
  test: run the test suite this repo uses; fix what fails; stop when it is green
  security: if the change touches api/ or auth/, review it for injection, authz gaps and secret handling and list findings as file:line; otherwise say so and stop
  migrate-check: if the change adds a migration, run it against a fresh database and show the schema diff
  proof:
    prompt: show each claim in its own medium; a screenshot for any UI change; a benchmark for anything under hot/
    until: every claim has evidence
    max: 3
fixed: [approve, proof]
rules:
  - tests pass before anything posts
  - no pull request without a green check
  - a stranger's PR never runs write
```

Built-in steps: `plan` (until: the plan names its files) · `approve` (a person) · `write` (the plan is done) · `test` (green) · `review` (clean) · `security` (no findings) · `proof` (every claim has evidence) · `read` (PRs; summarised).

## Appendix B — example dataset (from the mock, trimmed)

Viewer: team `@priya` (sees api, web); solo replaces every handle with `@santosh`.

```json
{
  "factory": {"address":"factory.acme.internal","members":4,"workers":3,
    "since":{"at":"08:12","merged":2,"need_you":3,"cost":8.44}},
  "repos":[{"name":"acme/api","file":true},{"name":"acme/web","file":true},
           {"name":"acme/infra","file":false,"visible_to_priya":false}],
  "jobs":[
    {"ref":"api#1498","kind":"ISSUE","title":"Retry webhook deliveries with backoff","assignee":"priya",
     "state":"needs you","step":"proof","step_n":"8/8","waits":{"kind":"approve","on":"priya","since":"11:52 (34m)"},
     "loop":{"step":"proof","n":3,"max":3,"until":"every claim has evidence"},
     "plan_files":"planned 4 files · changed 4","diff_stat":"+164 −22 · 4 files","cost":0.94,"age":"58m",
     "results":[
       {"step":"plan","t":"11:02","cost":0.21,"md":"Plan · 4 changes, size **S**.\n- Wrap `Deliverer.Send` in a retry with exponential backoff (2s base, 5 attempts).\n- Retry on 5xx and timeouts only; 4xx fails fast."},
       {"step":"approve","t":"11:06","cost":0,"md":"Plan approved by @priya · `evt_a74`."},
       {"step":"write","t":"11:31","cost":0.38,"md":"Added `retry.Backoff` and wired it into `deliver.go`."},
       {"step":"test","t":"11:39","cost":0.14,"md":"`go test ./api/...` · **41 passed**, 0 failed in 38s."},
       {"step":"review","t":"11:44","cost":0.09,"md":"No blocking findings. One fix applied: retries resend the body through `GetBody`."},
       {"step":"security","t":"11:46","cost":0.05,"md":"Touches `api/`. No findings."},
       {"step":"migrate-check","t":"11:47","cost":0,"md":"No migration in this change."},
       {"step":"proof","t":"11:52","cost":0.07,"md":"4 claims, 3 with evidence. Could not show **no duplicate deliveries** within the cap of 3 attempts."}],
     "claims":[
       {"text":"Retries use exponential backoff and stop after 5 attempts","evidence":{"kind":"test","ref":"41 ✓ · log"},"proven":true},
       {"text":"The webhooks page shows a retry banner while a delivery is retrying","evidence":{"kind":"screenshot","ref":"webhooks-page.png"},"proven":true},
       {"text":"Delivery latency does not regress","evidence":{"kind":"benchmark","ref":"p95 212ms −3%"},"proven":true},
       {"text":"No duplicate deliveries under retry","evidence":null,"proven":false}],
     "questions":[
       {"text":"Webhooks to billing/ endpoints are not idempotent. Retry them anyway?","about":"billing/","from":"security","routed_to":"raj","state":"open"},
       {"text":"Cap retries at 5 attempts or 8?","about":"api/webhooks/","from":"manager","routed_to":"priya","state":"answered","answer":{"by":"priya","text":"5. That matches the provider SLA."}}],
     "thread":[
       {"author":"manager","t":"11:03","md":"Plan is ready: backoff in `Deliverer.Send`, 5 attempts, 5xx and timeouts only."},
       {"author":"priya","t":"11:05","md":"Keep max attempts at 5. The provider SLA allows 5."},
       {"author":"manager","t":"11:52","md":"Proof is ready. 3 of 4 claims have evidence. Approve as is, or send it back with a different proof plan."}],
     "approve_label":"Approve → open PR #1498 as @priya"},
    {"ref":"api#1513","kind":"PR","title":"Providers unification","assignee":"priya","state":"running","step":"write",
     "waits":{"kind":"answer","on":"raj"},"phrase":"write · asks @raj","pr":{"num":1513,"by":"santosh","ci":"✓"},
     "overlap":{"file":"api/billing/invoice.go","num":1509,"who":"mei"},"cost":1.12,"age":"38m",
     "questions":[{"text":"Should invoices round half-up?","about":"billing/","from":"write","routed_to":"raj","state":"open"}]},
    {"ref":"web#2097","kind":"ISSUE","title":"Saved filters lose sort order on reload","assignee":"priya","state":"needs you","step":"approve","phrase":"approve plan","approve_label":"Approve plan → continue to write","cost":0.22},
    {"ref":"web#2093","kind":"ISSUE","title":"Keyboard shortcut to focus search","assignee":"mei","state":"running","step":"test","stuck_min":14,"loop":{"step":"test","n":2,"max":3,"until":"green"},"phrase":"stuck 14m","cost":0.47},
    {"ref":"api#1509","kind":"ISSUE","title":"Split billing service config by region","assignee":"mei","state":"running","step":"write","cost":0.61},
    {"ref":"api#1527","kind":"ISSUE","title":"Move invoice PDF rendering behind a flag","assignee":"raj","state":"queued","phrase":"waiting for @raj's worker (offline)"},
    {"ref":"api#1540","kind":"ISSUE","title":"Cache provider price lookups","assignee":null,"state":"queued","phrase":"waiting for someone to take it"},
    {"ref":"infra#311","kind":"CI","title":"Nightly deploy fails on arm64 runners","assignee":null,"state":"queued"},
    {"ref":"web#2088","kind":"ISSUE","title":"Empty state for the saved filters panel","assignee":null,"state":"backlog","phrase":"not started · nobody has taken it"},
    {"ref":"web#2077","kind":"ISSUE","title":"Remember me on the sign-in form","assignee":null,"state":"backlog","asked_first":{"why":"touches auth/","rule":"start.never: auth/**"}},
    {"ref":"api#1490","kind":"ISSUE","title":"Return 409 on duplicate idempotency key","assignee":"priya","state":"done","phrase":"merged","age":"09:41","cost":1.38,"pr":{"num":1490,"by":"priya","ci":"✓"}}
  ],
  "audit":[
    {"t":"11:58","u":"priya","k":"take","a":"took #2099"},
    {"t":"11:52","u":"factory","k":"start","a":"auto-started #2085 by rule agent-ready"},
    {"t":"11:40","u":"santosh","k":"post","a":"posted PR #1513 as @santosh","cost":0.38},
    {"t":"11:06","u":"priya","k":"approve","a":"approved #1498 · evt_a74"},
    {"t":"09:30","u":"factory","k":"start","a":"asked first on #2077 · touches auth/"},
    {"t":"08:40","u":"raj","k":"drop","a":"dropped #1484 · duplicate of #1490","cost":0.11}],
  "workers":[
    {"name":"priya-mbp","person":"priya","status":"busy","job":"#1513","takes":"acme/api"},
    {"name":"raj-desktop","person":"raj","status":"offline","job":null,"takes":"acme/api, acme/infra"},
    {"name":"mei-air","person":"mei","status":"busy","job":"#1509","takes":"acme/web"}],
  "members":[
    {"handle":"santosh","sees":"api, web, infra","last_seen":"now · owner"},
    {"handle":"priya","sees":"api, web","last_seen":"now · you"},
    {"handle":"raj","sees":"api, web, infra","last_seen":"09:40"},
    {"handle":"mei","sees":"web","last_seen":"11:52"}],
  "dry_run":{
    "acme/api":{"would_start":["#1540"],"would_ask_first":[]},
    "acme/web":{"would_start":["#2088","#2090","#2101","#2104"],"would_ask_first":[{"ref":"#2077","why":"touches auth/"}]},
    "acme/infra":{"note":"Under the defaults, nothing auto-starts. Jobs wait until someone takes them: #311"}}
}
```
