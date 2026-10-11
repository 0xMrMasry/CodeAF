# The codeaf factory — product document

Status: goal agreed 2026-10-10; screens and open-source money model settled the same day (supersedes every earlier draft)
Scope: the factory service, its web face, and the factory-related changes to codeaf. Chat and personal tooling in codeaf are out of scope.

---

## 1. The goal

**Build the software factory: a service that turns a project's open work into reviewed pull requests automatically, under rules kept in the repo, with people judging outcomes instead of pressing go — and that works identically for one person on a laptop and for a company in the cloud.**

Two artifacts, nothing else:

| artifact | what it is | what it never does |
|---|---|---|
| **`factory`** | the service. Holds what is shared and scarce: claims, policy, routing of judgment, one voice on the forge, the record, continuity. Serves its own web face, the **floor**. | execute work · hold code · hold accounts · hold money |
| **`codeaf`** | one binary a person installs. The **desk** (CLI + TUI: chat with a run, steer, approve at the keyboard) and the **worker** (pulls claims from a factory, runs them in worktrees with the person's own access and keys, returns proofs). | act outward on its own |

Same two artifacts in every deployment. The deployment decides capacity and reach, never features.

---

## 2. Why a center exists — the shared scarcities

Many people (or one person with several machines) running codeaf against one codebase break in known ways. Git and the forge solve none of them. The factory holds exactly these:

| scarcity | the factory holds | the problem it removes |
|---|---|---|
| **work** | one lease per job (one worker, expiry, re-queue); files in flight | two factories start the same issue; two change the same files blind |
| **policy** | one interpreter of `.codeaf/factory.md` | everyone's rules differ; automatic intake starts the wrong thing |
| **judgment** | questions routed to whoever owns that code, answerable by any qualified person | my factory asks me about code I do not own |
| **voice** | one presence on the forge; the trailer bound to the approval event | two bots talk on one issue; nobody can tell who approved |
| **memory** | the record (starts, approvals, publishes, overrides, spend per item); lessons proposed as PRs to the rules | a rejected PR's reason stays on one laptop |
| **attention** | one ordering; one pipeline view | nobody can see twelve items stuck at approve across four people |

**Who runs a job: its person, not a pool.** A job runs only on the machines of its person — the forge assignee, or whoever started it. A worker takes other people's unassigned jobs only when its owner opted in (`codeaf attach --take-unassigned`). This keeps money personal, keeps presence quiet, and makes "assigned to me" mean what it already means on the forge.

**Money is not a shared scarcity in open source.** Every worker runs on a person's machine with that person's own model keys and forge access. Caps and the day rail are personal settings in codeaf. The factory only *records* what each item cost. A team budget needs one payer and one key, which is the hosted product.

Everything abundant — execution, worktrees, conversations, model calls — stays at the edge, in codeaf.

**Scope test for any feature:** does it govern a shared scarcity? Then it belongs to the factory. Otherwise it belongs to codeaf.

---

## 3. For one person

The factory at scale one is the same engine with a queue of one, a pool of one, an approver of one. No solo code path exists: solo is a factory at `127.0.0.1` with one worker and one viewer, and the TUI is its client like any other.

What a solo person gets, without being forced into anything:

- **Automatic intake.** Write once what may start (labels, size, cap, paths that never auto-start). Work begins without pressing go; you approve outcomes. Intake and polling need a service awake, not a window open.
- **Evidence, not text.** The floor renders what the run produced: the proof sheet when the recipe has a `proof` stage (default on; the model may never skip it; a person may remove it from their recipe), the diff, checks, log, spend.
- **Glance moments.** Any browser on the same network: what needs you, approve, answer, add work, reprioritize.
- **Breadth.** Pipeline across repos, what is stuck, spend over time, the record of what ran and what it cost.
- **Rules you can see.** The policy file rendered, with a dry run: "under these rules, these items would start today."
- **More than one machine.** Laptop and desktop are two workers on one floor and one claims desk — the solo form of the team problem.
- **Growth without migration.** A teammate points their codeaf at your factory's URL. Hosted is the same floor with our machines in the pool.

What the factory does **not** give a solo laptop: continuity while the lid is closed. A sleeping machine runs nothing, factory or not. Continuity comes from where the factory and a worker run — an always-on box, a relay (its own branch), or hosted.

---

## 4. The design constraint — generic, bounded only by the agentic loop

The product fixes the primitives of the loop and nothing else; everything else is words the user writes.

| fixed, because the era demands it | open — words |
|---|---|
| a **job** runs an issue or PR through steps on a machine; a person judges the result | which steps, in what order |
| a **step** is a conversation with a model that has tools; it ends when the model says the step's condition holds | the prompt, the condition (`until`), the rounds (`max`) |
| a step reports in **markdown** and may assert **claims** with evidence | what it says, claims, what counts as evidence |
| a step may ask a **question**; a person answers | what it asks |
| **approve** is a person, always before anything leaves the machine | where approvals sit; which steps are `fixed` |
| **rules** are sentences every job obeys | the sentences |
| the factory keeps one job per worker, the record, and reads the forge | — |

No step kinds, no condition language, no block schema, no risk model, no plugin API. A security gate, a migration check, a performance budget, a docs step — each is a prompt, and its result shows up where every result shows up. The pipeline file is specified in `FACTORY-YAML.md`.

## 5. Surfaces: the binary and the app

**The binary is me and my machine; the app is us.** The TUI shows Mine; the app shows Everything. That is the one product-level difference, and it is deliberate, not a rendering limit. Team view is free in the app.

The public model is three lines:

- **codeaf** — a coding agent in your terminal. You talk, it works.
- **codeaf factory** — the same codeaf running your repo's work unattended, for you or your team. You approve.
- **codeaf app** — talk and factory in one window, for you and the people you work with.

One engine, one vocabulary. Nobody hears "worker", "control plane" or "mode": your codeaf is a worker when attached; that is plumbing.

Two verbs on one engine: **talk** (chat, steer, terminal) and **run** (factory: jobs that run unattended; you approve). Every surface can do both; they differ in what they are best at.

```mermaid
flowchart TB
    GH["GitHub<br/>issues · PRs · checks"]
    FAC["codeaf factory<br/>intake · auto-start rules · leases · record · API"]
    ENG(("one engine<br/>codeaf"))
    TUI["codeaf<br/>TUI: talk + Mine"]
    APP["codeaf app<br/>talk + Everything"]
    M1["your machine<br/>codeaf runs your jobs"]
    M2["teammate's machine<br/>codeaf runs their jobs"]
    GH <-->|"poll · publish"| FAC
    TUI -->|"local engine (chat)"| ENG
    APP -->|"bundled engine (chat)"| ENG
    TUI <-->|"/v1 HTTP + SSE"| FAC
    APP <-->|"/v1 HTTP + SSE"| FAC
    M1 -->|"/v1/workers pull"| FAC
    M2 -->|"/v1/workers pull"| FAC
```

| responsibility | binary `codeaf` | app |
|---|---|---|
| chat with codeaf, steer, terminal | TUI — home | yes, same engine (bundled) |
| **my** jobs: take, approve, answer, steer, caps | TUI (Mine) | yes |
| **team**: everyone's jobs, who has what, needs-you across the team, proof media, the record | — | yes (Everything) |
| run the factory: GitHub intake, auto-start rules, leases, record, API | `codeaf factory` | — (attaches to one) |
| run jobs (be a worker) | yes | yes, via bundled engine, if the person turns it on |
| ops: attach, invite, repos, pipeline, revoke | CLI | attach + invite in UI; the rest read-only |
| policy | `.codeaf/factory.yaml` in the repo | read-only |

Boundaries that never move:

- The TUI never grows team views, proof media or the record browser.
- The app never grows running a factory, editing policy, or ops.

**Capability rule:** anything you can say to codeaf, you can say in both; anything you need to see, you see in the app. Steering a job that runs on a teammate's machine is reply-level everywhere in v1; deep steering happens on that machine.

### App v1

- **attach** — sign in to a factory: URL + GitHub handle (device flow) or token. Without it, the app is codeaf chat on your laptop with your own loopback factory.
- **factory view** — Mine / Unassigned / Everything; Needs you / Running / Queued / Backlog / Done; the job brief with claims and proof; the record.
- **job conversations** — one job's thread: answer, approve, request changes, steer. Full depth on my jobs (my engine), reply-level on a teammate's.
- **general chat** — the TUI's chat, in a window. Not a factory feature; it is why the app is opened daily.
- **terminal** — the app's existing terminal tab. Optional for v1.
- **bundled engine** — the app ships `codeaf`, so an invited teammate installs nothing else. It can put `codeaf` on PATH (like VS Code's `code`).

The app opens on the factory view when attached, on chat when not. The invited teammate lands on work; the solo user lands on talk.

---

## 6. Screens

The screens below describe the factory view; the app renders it. The small page the factory itself serves is the same view and stands in until the app ships.

### 5.1 The floor (browser, served by `factory`)

| screen | shows | verbs | solo | team |
|---|---|---|---|---|
| **Door** | "this floor is private" → floor token or tailnet; then "link your GitHub to view" (device flow) when watched repos differ in access | link · continue | never shown at loopback | first visit per device |
| **Queue** | since you last looked · pipeline strip (`plan 2 · approve 4 ⚑ · write 5 · review 1 · proof 2`) · groups Needs you / Queued / Running / Backlog / Done · repo chips · your capacity line (`2 queued for you · your laptop busy`) · the factory address | open · approve · answer · prioritize · drop · add | yes | filtered per viewer; a job says who it is assigned to and what it waits for (`waiting for @raj's worker (offline)`); no roster on the page |
| **Item** | header (kind, repo, number, title, state, holder) · stage rail · tabs: proof · diff · checks · log · conversation · spend | approve · request changes · re-run checks · answer · reply · prioritize · drop | yes | the conversation shows who asked and who answered |
| **Add work** | repo · title · text → a `new` item | add | yes | yes |
| **Record** | what ran, who approved what, when, at what cost; export | export | yes | filtered per viewer |
| **Settings** | connect forge · watched repos · the pipeline file rendered + dry run ("these N would start today") · roster (members and workers by handle, online/offline — the only place presence is shown) · sessions · the factory's address and the attach command | connect · watch · (roster and sessions are read-only here; revoke is CLI) | yes | yes |

Phone-first, one column; on a wide screen the floor becomes rows + a peek, like the TUI's 120-column layout. Every row, count, chip, event and export is computed *after* the viewer filter — aggregates leak as surely as diffs do.

### 5.2 The desk (codeaf TUI) — changes only

- The existing factory page stays; it becomes a client of the factory API. Locally it attaches to the loopback factory; `codeaf --floor <url>` attaches to a team's.
- Settings gains one line: the factory it is attached to, and `join` when it is not yet a worker there.
- `codeaf factory serve` (and the `factory` alias) starts a service; `codeaf worker --join <url>` joins one; `factory sessions revoke @handle` and the other ops verbs are CLI only.
- Nothing is removed. Conversations, steering, keyboard approval stay where they are.

### 5.3 Uses

1. **Solo, day one.** Install codeaf → `/factory` → connect GitHub, pick repos → items stream in, triaged → `r` runs one, or `## start` starts it → approve the plan, approve the proof → the PR opens as you. Open `http://127.0.0.1:<port>` on the phone to approve from the couch.
2. **Solo, second machine.** `codeaf worker --join http://laptop:<port>` from the desktop. One floor, one claims desk; the desktop takes the next item.
3. **Team.** One person runs `factory serve` on a box, connects the forge, commits `.codeaf/factory.md`. Teammates open the floor (door, link GitHub), run `codeaf worker --join <url>`. Nobody takes the same item twice; a plan's file list warns of overlap; a question about billing goes to whoever owns billing.
4. **Judging from a phone.** A landed item shows its proof; approve as `@handle`; the factory posts with the trailer bound to that approval.
5. **A lesson.** A reviewer rejects a PR for a reason → the floor offers "add to the rules" → a PR to `.codeaf/factory.md`.

---

## 7. Architecture

```mermaid
flowchart TB
    subgraph FORGE["FORGE — GitHub · GitLab · local"]
        R1[repos · issues · PRs]
        R2[".codeaf/factory.md<br/>rules + recipe — code, reviewed in PRs"]
        R3[identity · permissions]
    end

    subgraph FACTORY["FACTORY — one service (never executes)"]
        direction TB
        SRC[sources<br/>poll · webhook when hosted]
        PG[policy gate<br/>start = rules · outside the envelope = a question]
        CD[claims desk<br/>one lease per item · files in flight · one ordering]
        WR[worker registry<br/>join tokens bound to a handle · pull dispatch]
        ST[run state<br/>items · stages · proofs · spend]
        REC[record<br/>append-only events · export]
        PUB[publisher<br/>approval event → trailer → post as person or app]
        SES[sessions<br/>door · link-to-view · per-viewer filtering]
        FLOOR[the floor — web face<br/>served by this process]
    end

    subgraph CODEAF["CODEAF — one binary per person"]
        TUI[desk · TUI/CLI<br/>conversations · steering · keyboard approval · caps]
        WK[worker<br/>pulls claims · runs stages in worktrees<br/>with the person's keys and forge access]
    end

    BR[browser · phone<br/>no install]

    FORGE <-->|"poll · write — one voice"| SRC
    SRC --> PG --> CD --> WR
    WR <-->|"pull claims · return proofs — never pushed"| WK
    WK --> ST --> PUB -->|trailer-bound posts| FORGE
    ST --> FLOOR
    CD --> FLOOR
    REC --> FLOOR
    SES --> FLOOR
    TUI <-->|"factory API — loopback or --floor url"| ST
    FLOOR <-->|HTTPS| BR
```

The contract in one line: **the factory decides and records; codeaf executes and converses; the floor is where people judge.**

### Deployments

| deployment | factory runs on | workers are | reach |
|---|---|---|---|
| **solo** | your laptop, loopback | your codeaf, auto-joined | TUI; the floor in a browser on your network |
| **team, self-hosted** | a box the team owns | teammates' codeafs, joined once, each with its person's keys | TUI with `--floor <url>`; the floor behind a door |
| **hosted** (paid) | our infra, one tenant per company | cloud players + any registered machine; one payer, one key | the browser is enough |

Hosted changes capacity, tenancy and money — webhooks, always on, a pool that never sleeps, one team bill — never features.

---

## 8. Identity and security

**No accounts, ever.** Identity, permissions and code live on the forge. The factory keeps sessions and credentials; the forge keeps people. The day the factory stores a password, an email, an invitation or a role we invented, the discipline broke.

**User = a forge handle. Team = the deployment.** One running factory is the team; there is no org object, no invitations, no roles. A second team runs a second factory.

The ladder, by deployment:

| | solo (loopback) | team (off loopback) |
|---|---|---|
| open the floor | nothing | **door**: tailnet, or one shared floor token |
| who am I | the factory already holds your forge token | **link the forge** once per device (device flow, read-only) → a session + a credential row keyed by `@handle` |
| what I see | everything | only repos *your* token can see (`GET /repos/{o}/{r}` × watched repos, cached with a short TTL); an optional `team:` allowlist in the **factory's host config** (never a repo's file) narrows further — admission = listed **and** visible |
| approvals | as you | as your handle, recorded as a bound event |
| worker | your codeaf, in-process credential | `codeaf worker --join <url>` with a join token minted **for a linked handle**; a claim is only offered to a worker whose person can see that repo |
| leaving | — | remove repo access on the forge → the next refresh empties their floor; `factory sessions revoke @handle` drops the session; sessions expire |

Rules that hold everywhere:

- **Pulled, never pushed.** Workers dial out. The factory never opens a connection into any machine. A compromised floor session can queue intent at worst.
- **Agents never attest.** Approvals are server-side events; the factory writes the trailer; publishing happens as the person (linked token) or the app (unattended, hosted).
- **Scope tag on everything.** Items, proofs (they contain diffs), logs, spend lines, events, notifications, counts, the pipeline strip, exports — every derived artifact carries its repo and passes the same one filter.
- **Token posture.** Self-hosted viewers' tokens are browser-held by default; hosted stores them encrypted and injects run secrets per run, never at rest.
- **Blast radius.** Ops verbs exist only in the CLI; the floor holds no execution channel.

---

## 9. Policy

`.codeaf/factory.yaml`, committed and reviewed like code, read from the main branch (a PR can never change the rules for its own run). The full file is specified in `FACTORY-YAML.md`; in short:

```yaml
start:     # what may begin without a person: labels · size · cost · never paths · ask-first sentences
pipeline:  # the step names per kind of job: issue · pr · ci
steps:     # a step is a prompt (+ until, max); same name overrides a built-in
fixed:     # steps the job's manager may never drop
rules:     # sentences every job obeys
```

Two layers: **repo rules bind everyone; a personal `~/.codeaf/factory.yaml` may only be stricter.** Automatic inside what `start` allows; anything outside it — over a person's cap, `never` paths, `ask_first` matches, strangers' PRs — becomes a question, never a lease.

Two gates: **start = policy** (automatic), **publish = a person** (always).

The factory's own config lives on its host (`~/.codeaf/factory/config.yaml`: port, access, repos, optional member allowlist), edited by whoever runs the box.

## 10. Sequences

### 9.1 Solo — day one

```mermaid
sequenceDiagram
    autonumber
    actor P as Person
    participant C as codeaf (desk + worker)
    participant F as factory (loopback)
    participant G as forge
    P->>C: codeaf → /factory
    C->>F: start the factory in-process on 127.0.0.1, join as the worker
    P->>F: connect GitHub (token or gh), pick repos
    F->>G: poll
    G-->>F: issues · PRs · checks
    F->>F: triage · policy gate: inside the envelope → claim
    C->>F: pull claim
    C->>C: plan → approve → write → test → review → proof (person's keys)
    C-->>F: proof sheet · spend
    F-->>P: needs you (TUI or browser)
    P->>F: approve
    F->>G: PR opened as the person · trailer bound to the approval
```

### 9.2 Team — one factory, many codeafs

```mermaid
sequenceDiagram
    autonumber
    actor O as Owner
    participant F as factory (their box)
    participant G as forge
    actor T as Teammate
    participant C as teammate's codeaf
    O->>F: factory serve · connect the forge
    O->>G: commit .codeaf/factory.md
    T->>F: open the floor → door → link GitHub (device flow)
    T->>C: codeaf worker --join <url> (token minted for @T)
    G->>F: item inside the envelope
    F->>F: one claim, offered only to workers whose person can see the repo
    C->>F: pull (only one codeaf ever gets it)
    C->>F: planned files → overlap check against files in flight
    C->>F: a step's question → routed to the code's owner
    F-->>O: approve from the floor or the TUI
    F->>G: one voice · trailer bound to the approval
```

---

## 11. Business logic and strategy

**The goal now is user growth.** Revenue comes later. It only needs to stay possible: a hosted factory, memory, a relay, compute. Nothing we build now may close those doors, and nothing we build now waits for them.

**The delta over the forge is the product.** The forge holds artifacts: code, issues, PRs, identity, permissions. The factory holds what the forge has no concept of: live job state, leases, pending approvals, ordering, the record.

### Growth shape

1. **Acquire — the TUI.** One install, two reasons: chat, and "it does labeled issues while you are away" with `codeaf factory` on loopback.
2. **Spread — the factory is the multiplier.** One dev attaches a repo and invites a reviewer, a PM, a lead. They are not terminal people, so they need the app. The app's onboarding starts from "you were invited to @handle's factory", not from an empty chat.
3. **Retain — chat is the daily habit; the record and memory are the stake.**

```mermaid
flowchart TB
    A["install codeaf (TUI)"] --> B["chat with codeaf"]
    B --> C["codeaf factory on your own repo"]
    C --> D["codeaf factory invite"]
    D --> E["teammate opens the invite link"]
    E --> F["the app installs and attaches"]
    F --> G["approves on the factory view"]
    G --> H["discovers chat in the app"]
    H --> I["starts their own factory"]
    I --> D
```

### Invites

A factory invite is the app invite. It is a link that carries the factory address, a token and "invited by". It downloads the app, attaches it, and shows the queue. Invites are unlimited and minted by the factory owner (`codeaf factory invite`).

Nothing on the invited teammate's path is gated: no waitlist between an invite and the queue. Early access (signed builds, a waitlist for the first weeks) is for quality, not scarcity; a factory invite skips the line.

### Open and closed

Open or closed is a packaging decision. It can change at any time and it does not shape the product: there is one binary either way. A second binary would only ever follow a license split, never a product split. If we want exclusivity later, the hosted founding-teams program is the place for it.

### What would break the clean story

- A separate "factory app" brand or binary.
- A feature that lives only in the app for product reasons (a crippled terminal path).
- Different names for one thing across surfaces.
- An app without chat.
- A waitlist between the invited teammate and the queue.

---

## 12. Rollout

A milestone is a journey that works end to end, not a set of components. Each milestone exits after one week of dogfood without a manual bypass. The one number we track: jobs completed through the factory per week; later, teams attached.

```mermaid
flowchart LR
    M0["M0 · solo<br/>labeled issue → PR,<br/>approved in the TUI"]
    M1["M1 · two people<br/>one factory, two machines,<br/>leases hold"]
    M2["M2 · app attaches<br/>factory view, approve + chat,<br/>internal use"]
    M3["M3 · early access<br/>invite link, signed builds,<br/>~20 teams"]
    M4["M4 · public"]
    M0 --> M1 --> M2 --> M3 --> M4
```

- **M0 — solo.** Factory + TUI on one laptop. One labeled issue becomes a PR; you approve it from the TUI.
- **M1 — two people.** One factory, two machines; leases hold. TUI only. Dogfood on the CodeAF repo.
- **M2 — the app attaches.** It shows the factory view; approve and chat through the bundled engine. Internal use.
- **M3 — early access.** The designer's design applied, the invite link, signed builds. About 20 teams.
- **M4 — public.**

---

## 13. Nomenclature

| term | meaning |
|---|---|
| **factory** | the service; also the product |
| **floor** | the factory's web face (and the name of the TUI page that shows the same floor) |
| **codeaf** | the person's binary: desk + worker |
| **desk** | the TUI/CLI side of codeaf |
| **worker** | the executing side of codeaf; pulls claims; runs with its person's keys |
| **item** | a unit of work: issue, PR, red CI run, chore, or work a chat split off |
| **lease** | one worker's hold on a job; in the UI: *taken by* / *Take* |
| **claim** | a statement a step asserts with evidence; what a person approves |
| **envelope** | what `## start` lets begin without a person |
| **door** | what a browser needs off loopback: tailnet or a floor token |
| **link** | a viewer's one-time read-only forge link → session + credential row keyed by handle |
| **record** | the append-only event log |

---

## 14. Not the product

A forge replacement. A platform for others to build on. A chat app in the browser. A notification inbox. A per-seat SaaS. A team budget in open source.

## 15. Open

- **One codeaf attaches to one factory at a time.** Personal repos while attached to a team's factory means `codeaf detach`, or a second factory later. Multi-factory is a later point, not this branch.

- Floor conversation depth: reply-level now; deep steering stays in the TUI.
- Local env for self-hosted runs: documentation and tooling, or managed env as hosted-only.
- Notifications: web push + digest first; every notification carries a scope tag.
- Hosted only: per-run injection of push credentials and model keys for machines nobody owns.
- `codeaf worker --join` is kept (easy join; the moat is after the join); revisit if headless self-run capacity proves to erode hosted.
