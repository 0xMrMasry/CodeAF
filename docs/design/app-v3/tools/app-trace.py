#!/usr/bin/env python3
"""Record real `codeaf app` protocol traces against a real model.

Each scenario starts `codeaf app --home <home>` on its own pipes, speaks the
protocol in docs/design/app-v3/PROTOCOL.md, and saves every line the app wrote,
byte for byte and in order, to <out>/<scenario>.jsonl. The lines the script sent
go to <out>/<scenario>.requests.jsonl, each with the moment it was sent, so a
fake engine can replay the conversation from both halves.

The trace home is a fresh folder; only the provider key and the chosen model are
copied into it from the source profile, and they are never printed.

    app-trace.py --bin bin/codeaf --out ~/work/traces [--scenario basic] ...
"""

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

TRACE_ROOT = "/tmp/ui2trace"


class App:
    """One running `codeaf app`, its output recorded as it arrives."""

    def __init__(self, binary, home, out_path, req_path, extra=(), extra_env=None):
        env = dict(os.environ)
        env["CODEAF_TELEMETRY"] = "off"
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [binary, "app", "--home", home] + list(extra),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )
        self.out = open(out_path, "wb")
        self.req = open(req_path, "w")
        self.lines = queue.Queue()
        self.seen = []
        self.next_id = 1
        self.started = time.monotonic()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            at = time.monotonic()
            self.out.write(raw)
            self.out.flush()
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            self.lines.put((at, line))
        self.lines.put((time.monotonic(), None))

    def send(self, method, params=None):
        rid = self.next_id
        self.next_id += 1
        msg = {"id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        data = json.dumps(msg)
        self.req.write(json.dumps({"at_ms": round((time.monotonic() - self.started) * 1000, 1), "line": msg}) + "\n")
        self.req.flush()
        self.proc.stdin.write((data + "\n").encode())
        self.proc.stdin.flush()
        return rid, time.monotonic()

    def wait(self, match, timeout):
        """Answers (time, line) for the first line matching, reading in order."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("nothing matched within %ss" % timeout)
            try:
                at, line = self.lines.get(timeout=left)
            except queue.Empty:
                raise TimeoutError("nothing matched within %ss" % timeout)
            if line is None:
                raise RuntimeError("codeaf app ended")
            self.seen.append(line)
            hook = getattr(self, "on_line", None)
            if hook:
                hook(line)
            if match(line):
                return at, line

    def close(self):
        try:
            self.send("shutdown")
            self.proc.wait(timeout=30)
        except Exception:
            self.proc.kill()
        self.out.close()
        self.req.close()


def event(name, **want):
    def match(line):
        if line.get("event") != name:
            return False
        data = line.get("data") or {}
        return all(data.get(k) == v for k, v in want.items())
    return match


def response(rid):
    return lambda line: line.get("id") == rid


def make_home(home, source, approval):
    if os.path.exists(home):
        shutil.rmtree(home)
    os.makedirs(home)
    with open(os.path.join(source, "config.json")) as f:
        profile = json.load(f)
    config = {k: profile[k] for k in ("api_key", "model.talk") if k in profile}
    config["telemetry"] = "off"
    if approval:
        config["tools.approvalMode"] = approval
    with open(os.path.join(home, "config.json"), "w") as f:
        json.dump(config, f)
    os.chmod(os.path.join(home, "config.json"), 0o600)


def forget_key(home):
    """Takes the provider key back out of a trace home once the run is over."""
    path = os.path.join(home, "config.json")
    with open(path) as f:
        config = json.load(f)
    config.pop("api_key", None)
    with open(path, "w") as f:
        json.dump(config, f)


def folder(name, files=()):
    path = os.path.join(TRACE_ROOT, name)
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path)
    for f in files:
        with open(os.path.join(path, f), "w") as out:
            out.write("trace file %s\n" % f)
    return path


def start(app, workspace, text, client_id):
    """Creates an agent with words and answers the latency figures."""
    hello_at, _ = app.wait(event("hello"), 30)
    rid, sent = app.send("agents.create", {"workspace": workspace, "text": text, "client_id": client_id})
    answered, line = app.wait(response(rid), 30)
    agent = line["result"]["agent"]["id"]
    started, _ = app.wait(event("turn.started", agent=agent), 120)
    return agent, {
        "create_response_ms": round((answered - sent) * 1000, 1),
        "create_turn_started_ms": round((started - sent) * 1000, 1),
    }


def scenario_basic(app):
    agent, lat = start(app, folder("basic"), "Say hello in one short sentence.", "trace-basic-1")
    app.wait(event("turn.done", agent=agent), 180)
    return lat


def scenario_tool(app):
    work = folder("tool", ["alpha.txt", "beta.txt", "gamma.txt"])
    agent, lat = start(app, work, "Use the bash tool to run `ls` in the current folder, then tell me how many files are there.", "trace-tool-1")
    app.wait(event("turn.done", agent=agent), 300)
    return lat


def allow_every_ask(app):
    """Answers every ask as it opens: allow_once where offered, else the first."""
    def answer(line):
        if line.get("event") == "ask.opened":
            ask = line["data"]["ask"]
            options = ask.get("options") or []
            if options:
                option = next((o["id"] for o in options if o["id"] == "allow_once"), options[0]["id"])
                app.send("asks.answer", {"agent": ask["agent"], "id": ask["id"], "option": option})
    app.on_line = answer


def scenario_permission(app):
    folder("victim", ["keep-me.txt"])
    work = folder("permission")
    allow_every_ask(app)
    agent, lat = start(app, work, "Run exactly this one shell command with the bash tool and nothing else: rm -rf %s/victim — then say in one sentence that it is gone. Do not check afterwards." % TRACE_ROOT, "trace-perm-1")
    app.wait(event("turn.done", agent=agent), 300)
    return lat


def scenario_tasks(app):
    work = folder("tasks")

    # A task proposal asks before it starts; the trace starts it.
    allow_every_ask(app)
    agent, lat = start(app, work, "Create 3 nested tasks with your task tool: one task that writes outer.txt here and, inside it, a sub-task that writes middle.txt, which in turn has a sub-task that writes inner.txt. Start them now.", "trace-tasks-1")
    app.wait(event("turn.done", agent=agent), 600)
    rid, _ = app.send("tasks.list", {"agent": agent})
    # Tasks run on after the turn: wait until every task said has settled.
    deadline = time.monotonic() + 600
    states = {}
    while time.monotonic() < deadline:
        for line in app.seen:
            if line.get("event") == "task.changed":
                task = line["data"]["task"]
                states[task["id"]] = task["state"]
        if states and all(s in ("done", "failed", "unverified") for s in states.values()):
            break
        try:
            app.wait(lambda line: line.get("event") == "task.changed", 30)
        except TimeoutError:
            pass
    app.send("tasks.list", {"agent": agent})
    time.sleep(1)
    lat["tasks"] = states
    return lat


def scenario_stop(app):
    work = folder("stop")
    agent, lat = start(app, work, "Write a 1500-word essay about the history of lighthouses. Do not use any tools.", "trace-stop-1")
    app.wait(event("turn.delta", agent=agent, kind="text"), 180)
    time.sleep(2)
    app.send("agents.stop", {"id": agent})
    app.wait(event("turn.done", agent=agent), 120)
    time.sleep(1)
    rid, _ = app.send("agents.entries", {"id": agent})
    app.wait(response(rid), 30)
    return lat


def scenario_latency(app):
    """Two creates in one process: the first cold, the second warm."""
    out = {}
    agent, out["cold"] = start(app, folder("latency"), "Reply with the single word: one.", "trace-lat-1")
    app.wait(event("turn.done", agent=agent), 180)
    rid, sent = app.send("agents.create", {"workspace": folder("latency2"), "text": "Reply with the single word: two.", "client_id": "trace-lat-2"})
    answered, line = app.wait(response(rid), 30)
    second = line["result"]["agent"]["id"]
    started, _ = app.wait(event("turn.started", agent=second), 120)
    out["warm"] = {"create_response_ms": round((answered - sent) * 1000, 1), "create_turn_started_ms": round((started - sent) * 1000, 1)}
    app.wait(event("turn.done", agent=second), 180)
    return out


def scenario_keys(app):
    """Setup and a key check against the real provider with a key that cannot work."""
    app.wait(event("hello"), 30)
    out = {}
    for method, params in (("setup.get", None), ("providers.list", None)):
        rid, _ = app.send(method, params)
        app.wait(response(rid), 30)
    bad = "sk-or-v1-" + "0" * 64
    rid, sent = app.send("keys.check", {"provider": "openrouter", "key": bad})
    answered, line = app.wait(response(rid), 30)
    out["keys_check_ms"] = round((answered - sent) * 1000, 1)
    out["keys_check"] = line.get("result")
    for method, params in (
        ("setup.set", {"step": 2, "provider": "openrouter"}),
        ("setup.get", None),
        ("settings.set", {"day_cap_usd": 25, "idle_close_days": 5}),
        ("settings.get", None),
    ):
        rid, _ = app.send(method, params)
        app.wait(response(rid), 30)
    return out


def scenario_budget(app):
    """A real turn on a tiny budget: the agent asks, and the trace raises it."""
    hello_at, _ = app.wait(event("hello"), 30)
    rid, sent = app.send("agents.create", {"workspace": folder("budget"), "text": "Write two short paragraphs about tides. Do not use any tools.", "client_id": "trace-budget-1", "budget_usd": 0.001})
    _, line = app.wait(response(rid), 30)
    agent = line["result"]["agent"]["id"]
    app.wait(event("turn.done", agent=agent), 300)
    _, opened = app.wait(lambda l: l.get("event") == "ask.opened" and (l["data"]["ask"].get("subkind") == "budget"), 60)
    ask = opened["data"]["ask"]
    rid, _ = app.send("asks.answer", {"agent": agent, "id": ask["id"], "option": "raise", "text": "0.05"})
    app.wait(response(rid), 30)
    app.wait(event("ask.closed", agent=agent, id=ask["id"]), 30)
    rid, _ = app.send("spend.today")
    app.wait(response(rid), 30)
    return {"ask": ask}


def first(app, match, timeout):
    """Answers the data of the first event matching, or None on a timeout."""
    try:
        _, line = app.wait(match, timeout)
        return line.get("data")
    except TimeoutError:
        return None


def task_event(pred):
    return lambda l: l.get("event") == "task.changed" and pred(l["data"]["task"])


def scenario_proposal(app):
    """A proposed task with its clock, left alone until the clock starts it."""
    agent, lat = start(app, folder("proposal"), "Use your propose_task tool to propose exactly one task: create a file named notes.txt here containing the word hello. Do not do the work yourself and do not start anything else.", "trace-prop-1")
    card = first(app, task_event(lambda t: t.get("proposed")), 300)
    out = {"card": card and card["task"]}
    started = first(app, task_event(lambda t: t["state"] == "running"), 120)
    out["started_by_clock"] = bool(started)
    first(app, task_event(lambda t: t["state"] in ("done", "failed", "unverified")), 600)
    return out


def scenario_yourcall(app):
    """A task whose acceptance only the person can judge, decided by the trace."""
    agent, lat = start(app, folder("yourcall"), "Use your propose_task tool to propose one task: write a short poem about tides into poem.txt here. Its acceptance must be exactly: the person reads the poem and says it moves them. Do not do the work yourself.", "trace-call-1")
    card = first(app, task_event(lambda t: t.get("proposed")), 300)
    if card:
        rid, _ = app.send("tasks.start", {"id": card["task"]["id"]})
        app.wait(response(rid), 30)
    call = first(app, task_event(lambda t: t.get("your_call") or t["state"] in ("done", "failed")), 900)
    out = {"ended": call and call["task"]}
    if call and call["task"].get("your_call"):
        rid, _ = app.send("tasks.get", {"id": call["task"]["id"]})
        app.wait(response(rid), 30)
        rid, _ = app.send("tasks.decide", {"id": call["task"]["id"], "verdict": "accept"})
        app.wait(response(rid), 30)
        out["decided"] = bool(first(app, task_event(lambda t: t["id"] == call["task"]["id"] and t["state"] == "done"), 120))
    return out


def scenario_quick(app):
    """A quick task that edits a file, then its undo."""
    work = folder("quick", ["a.txt"])
    agent, lat = start(app, work, "Use your quick_task tool (not a full task) to append a line saying hello to a.txt here. Do not edit it yourself.", "trace-quick-1")
    done = first(app, lambda l: l.get("event") == "quick.changed" and l["data"]["quick"]["state"] == "done", 600)
    out = {"quick": done and done["quick"]}
    if done:
        # The quick task lands as the turn ends, in either order, so the
        # undo is sent once both have.
        ended = lambda l: l.get("event") == "turn.done" and l["data"]["agent"] == agent
        if not any(ended(l) for l in app.seen):
            first(app, ended, 300)
        rid, _ = app.send("quick.undo", {"id": done["quick"]["id"]})
        _, line = app.wait(response(rid), 30)
        out["undo"] = line.get("error") or "ok"
        first(app, lambda l: l.get("event") == "quick.changed" and l["data"]["quick"]["state"] == "undone", 30)
    return out


def scenario_process(app):
    """A background process: its row, its followed output, its stop."""
    agent, lat = start(app, folder("process"), "Start this command in the background with your bash tool (run it as a background job, do not wait for it): python3 -c \"import time\nfor i in range(300):\n print('tick', i, flush=True); time.sleep(1)\" — then reply with one short sentence.", "trace-proc-1")
    row = first(app, lambda l: l.get("event") == "process.changed" and l["data"]["process"]["state"] == "running", 300)
    out = {"process": row and row["process"]}
    if row:
        pid = row["process"]["id"]
        rid, _ = app.send("processes.follow", {"id": pid})
        app.wait(response(rid), 30)
        first(app, lambda l: l.get("event") == "process.output", 30)
        rid, _ = app.send("processes.output", {"id": pid, "from_line": 0})
        app.wait(response(rid), 30)
        rid, _ = app.send("processes.unfollow", {"id": pid})
        app.wait(response(rid), 30)
        rid, _ = app.send("power.sleep")
        app.wait(response(rid), 30)
        first(app, lambda l: l.get("event") == "process.changed" and l["data"]["process"]["state"] == "stopped", 30)
        rid, _ = app.send("power.wake")
        app.wait(response(rid), 30)
        first(app, lambda l: l.get("event") == "entry.added" and "slept" in l["data"]["entry"].get("text", ""), 30)
        rid, _ = app.send("processes.list", {"agent": agent})
        app.wait(response(rid), 30)
    return out


def scenario_long(app):
    """A reply that runs long, said at the override, then moved to a task."""
    agent, lat = start(app, folder("long"), "Write ten files here, note1.md to note10.md, each three sentences about a different sea creature. Start at once with note1.md and write them strictly one at a time: one short write call per response, then the next. Do not use a task; do it yourself in this reply.", "trace-long-1")
    long = first(app, lambda l: l.get("event") == "turn.long", 300)
    out = {"turn_long": long}
    if long:
        rid, _ = app.send("agents.move_to_task", {"id": agent, "turn": long["turn"]})
        _, line = app.wait(response(rid), 200)
        out["moved"] = line.get("result") or line.get("error")
    app.wait(event("turn.done", agent=agent), 600)
    # The task goes on after the reply ends; its live line is watched to the end.
    first(app, lambda l: l.get("event") == "task.changed" and l["data"]["task"]["state"] in ("done", "failed"), 600)
    out["lines"] = sorted({l["data"]["task"].get("line") for l in app.seen if l.get("event") == "task.changed" and l["data"]["task"].get("line")})[:12]
    return out


WORKLOG_FILES = {
    "README.md": "# acme-webhooks\n\nDelivers webhooks with retries. Run the tests with `python3 -m unittest -v`.\n",
    "retry.py": (
        "\"\"\"Retry with capped exponential backoff.\"\"\"\n\n"
        "MAX_ATTEMPTS = 5\nBASE_S = 1.0\nCAP_S = 30.0\n\n\n"
        "def backoff(attempt):\n"
        "    \"\"\"Seconds to wait before the given attempt (1-based).\"\"\"\n"
        "    return BASE_S * 2 ** attempt\n\n\n"
        "def should_retry(attempt, status):\n"
        "    return attempt < MAX_ATTEMPTS and status >= 500\n"
    ),
    "deliver.py": (
        "from retry import backoff, should_retry\n\n\n"
        "def deliver(send, payload, sleep):\n"
        "    attempt = 1\n"
        "    while True:\n"
        "        status = send(payload)\n"
        "        if status < 300 or not should_retry(attempt, status):\n"
        "            return status, attempt\n"
        "        sleep(backoff(attempt))\n"
        "        attempt += 1\n"
    ),
    "test_retry.py": (
        "import unittest\n\nfrom retry import backoff, should_retry, CAP_S\n\n\n"
        "class RetryTest(unittest.TestCase):\n"
        "    def test_first_wait_is_base(self):\n        self.assertEqual(backoff(1), 1.0)\n\n"
        "    def test_waits_double(self):\n        self.assertEqual(backoff(3), 4.0)\n\n"
        "    def test_wait_is_capped(self):\n        self.assertEqual(backoff(20), CAP_S)\n\n"
        "    def test_no_retry_on_4xx(self):\n        self.assertFalse(should_retry(1, 404))\n"
    ),
    "test_deliver.py": (
        "import time\nimport unittest\n\nfrom deliver import deliver\n\n\n"
        "class DeliverTest(unittest.TestCase):\n"
        "    def test_gives_up_after_max_attempts(self):\n"
        "        waits = []\n"
        "        status, attempts = deliver(lambda p: 503, {}, waits.append)\n"
        "        self.assertEqual((status, attempts), (503, 5))\n\n"
        "    def test_soak(self):\n"
        "        for i in range(8):\n"
        "            print('soak round', i, flush=True)\n"
        "            time.sleep(1)\n"
    ),
}


def scenario_worklog(app):
    """An agent at work: twenty and more calls, a failing run, its retry,
    parallel reads, an edit, a long run's tail, and one call's whole output."""
    work = folder("worklog")
    for name, body in WORKLOG_FILES.items():
        with open(os.path.join(work, name), "w") as out:
            out.write(body)
    text = (
        "This folder is a small Python project. Work through it step by step with your tools; "
        "this needs at least twenty-four tool calls, so do not combine or skip steps: "
        "1) list the folder; 2) read README.md, retry.py, deliver.py, test_retry.py and "
        "test_deliver.py all at once in one batch of parallel reads; 3) search, as separate calls, "
        "for MAX_ATTEMPTS, for backoff, for CAP_S, for should_retry and for sleep; 4) run "
        "`python3 -m unittest -v` (it fails); 5) fix the bug in retry.py with one edit; 6) run exactly "
        "`python3 -m unittest -v` again; 7) run `python3 -m unittest -v test_deliver`, "
        "`python3 -m py_compile retry.py deliver.py` and `wc -l retry.py deliver.py` as three separate "
        "commands; 8) read each of the five files again, one call each; 9) finish with one short "
        "sentence. Do not start a task."
    )
    agent, lat = start(app, work, text, "trace-worklog-1")
    followed = {}

    def follow_runs(line):
        if line.get("event") != "turn.tool":
            return
        row = line["data"]
        if row.get("tool_kind") == "run" and row.get("phase") == "begin" and not followed:
            followed["call"] = row["call"]
            app.send("tools.follow", {"agent": agent, "call": row["call"]})

    app.on_line = follow_runs
    app.wait(event("turn.done", agent=agent), 900)
    app.on_line = None
    rows = [l["data"] for l in app.seen if l.get("event") == "turn.tool" and l["data"].get("phase") == "end"]
    out = {"calls": len(rows), "kinds": sorted({r.get("tool_kind") for r in rows})}
    failing = next((r for r in rows if r.get("exit_code")), None)
    out["failed"] = failing and {k: failing.get(k) for k in ("call", "exit_code", "lines", "tail")}
    out["retried"] = [r["call"] for r in rows if r.get("retried_of")]
    out["batches"] = sorted({r["batch"] for r in rows if r.get("batch")})
    if failing:
        rid, _ = app.send("tools.output", {"agent": agent, "call": failing["call"], "from_line": 0})
        _, line = app.wait(response(rid), 30)
        res = line.get("result") or {}
        out["output"] = {"total": res.get("total"), "first": (res.get("lines") or [None])[0]}
    rid, _ = app.send("agents.entries", {"id": agent, "limit": 200})
    app.wait(response(rid), 30)
    return out


# ── Wave 3b: the conversation, the dock, the queue, tasks as chats ───────────


def scenario_steer(app):
    """A steer consumed at a step, then one the turn ends before reading."""
    agent, lat = start(app, folder("steer"), "Run `sleep 6; echo first` with your bash tool, then run `ls` with it, then reply in one sentence.", "trace-steer-1")
    first(app, lambda l: l.get("event") == "turn.tool" and l["data"].get("phase") == "begin", 300)
    rid, _ = app.send("agents.steer", {"id": agent, "text": "Also run `date` before you reply.", "client_id": "steer-1"})
    app.wait(response(rid), 30)
    consumed = first(app, lambda l: l.get("event") == "entry.added" and l["data"]["entry"]["kind"] == "steer" and l["data"]["entry"].get("state") != "pending", 300)
    app.wait(event("turn.done", agent=agent), 300)
    rid, _ = app.send("agents.send", {"id": agent, "text": "Reply with exactly one word: ready.", "client_id": "steer-2"})
    app.wait(response(rid), 30)
    app.wait(event("turn.started", agent=agent), 60)
    rid, _ = app.send("agents.steer", {"id": agent, "text": "Now say the word twice.", "client_id": "steer-3"})
    app.wait(response(rid), 30)
    late = first(app, lambda l: l.get("event") == "entry.added" and l["data"]["entry"].get("client_id") == "steer-3" and l["data"]["entry"].get("state") in ("consumed", "fell_through"), 300)
    for _ in range(2):
        try:
            app.wait(event("turn.done", agent=agent), 300)
        except TimeoutError:
            break
    return {"first": consumed and consumed["entry"], "second": late and late["entry"]}


def scenario_queue(app):
    """Two queued messages, edited and reordered while the turn runs, then sent in order."""
    agent, lat = start(app, folder("queue"), "Run `sleep 10` with your bash tool, then reply with the word done.", "trace-queue-1")
    app.wait(event("turn.tool"), 300)
    ids = []
    for text in ("Then say the word alpha.", "Then say the word beta."):
        rid, _ = app.send("agents.queue", {"id": agent, "text": text, "client_id": "q-" + text[-6:-1]})
        _, line = app.wait(response(rid), 30)
        ids.append(line["result"]["queued"][-1]["id"])
    rid, _ = app.send("queue.edit", {"agent": agent, "id": ids[0], "text": "Then say the word gamma."})
    app.wait(response(rid), 30)
    rid, _ = app.send("queue.move", {"agent": agent, "id": ids[1], "to": 0})
    _, line = app.wait(response(rid), 30)
    order = [q["text"] for q in line["result"]["queued"]]
    for _ in range(3):
        app.wait(event("turn.done", agent=agent), 300)
    rid, _ = app.send("queue.edit", {"agent": agent, "id": ids[0], "text": "too late"})
    _, line = app.wait(response(rid), 30)
    return {"order": order, "edit_after_send": line.get("error")}


def scenario_update(app):
    """Thinking, an interim update before a tool call, and the answer."""
    work = folder("update", ["a.txt", "b.txt"])
    agent, lat = start(app, work, "Think it over first. Then, before using any tool, tell me in one sentence what you will do; then list this folder with a tool; then answer with the file count.", "trace-update-1")
    app.wait(event("turn.done", agent=agent), 300)
    kinds = [l["data"]["entry"]["kind"] for l in app.seen if l.get("event") == "entry.added"]
    thought = [l["data"] for l in app.seen if l.get("event") == "turn.thinking_done"]
    return {"entries": kinds, "thought": thought}


def scenario_asks(app):
    """A choice with the agent's suggestion handed back, and a clarification answered in words."""
    agent, lat = start(app, folder("asks"), "Use your ask tool to ask me one multiple-choice question: should trailing commas be strict or tolerant by default? Give two options, mark strict as your own suggested pick, and wait for my answer.", "trace-asks-1")
    opened = first(app, lambda l: l.get("event") == "ask.opened", 300)
    out = {"choice": opened and opened["ask"]}
    if opened:
        rid, _ = app.send("asks.answer", {"agent": agent, "id": opened["ask"]["id"], "you_decide": True})
        _, line = app.wait(response(rid), 30)
        out["you_decide"] = line.get("error") or "ok"
    app.wait(event("turn.done", agent=agent), 300)
    rid, _ = app.send("agents.send", {"id": agent, "text": "Use your ask tool to ask me, as a free-text question with no options, which customers still run v1. Wait for my answer, then repeat it back.", "client_id": "trace-asks-2"})
    app.wait(response(rid), 30)
    opened = first(app, lambda l: l.get("event") == "ask.opened", 300)
    out["clarification"] = opened and opened["ask"]
    if opened:
        rid, _ = app.send("asks.answer", {"agent": agent, "id": opened["ask"]["id"], "text": "Northwind and Acme"})
        app.wait(response(rid), 30)
    app.wait(event("turn.done", agent=agent), 300)
    return out


def scenario_batch(app):
    """Three commands asked about together, allowed as one."""
    work = folder("batch", ["a.txt", "b.txt", "c.txt"])
    agent, lat = start(app, work, "In one single step, call your bash tool three times in parallel: `rm a.txt`, `rm b.txt` and `rm c.txt`. Then reply in one sentence.", "trace-batch-1")
    asks = []
    while True:
        opened = first(app, lambda l: l.get("event") in ("ask.opened", "turn.done"), 300)
        if not opened or "ask" not in opened:
            break
        asks.append(opened["ask"])
        if len(asks) == 1:
            time.sleep(1)
            continue
        if len(opened["ask"].get("batch") or []) >= 2:
            rid, _ = app.send("asks.answer", {"agent": agent, "id": opened["ask"]["id"], "option": "allow_once"})
            app.wait(response(rid), 30)
            break
    try:
        app.wait(event("turn.done", agent=agent), 300)
    except TimeoutError:
        pass
    return {"asks": len(asks), "batch": asks and asks[-1].get("batch")}


def scenario_tasknote(app):
    """A task's note read at a step, and the task paused and resumed."""
    allow_every_ask(app)
    agent, lat = start(app, folder("tasknote"), "Propose one task with your task tool: write six files here, f1.txt to f6.txt, one per step, each holding one sentence about rivers. Do not do it yourself.", "trace-note-1")
    running = first(app, lambda l: l.get("event") == "task.changed" and l["data"]["task"]["state"] == "running", 600)
    out = {}
    if running:
        task = running["task"]["id"]
        rid, _ = app.send("tasks.note", {"id": task, "text": "Make the last file about the Nile.", "client_id": "note-1"})
        _, line = app.wait(response(rid), 30)
        out["note"] = line.get("result") or line.get("error")
        read = first(app, lambda l: l.get("event") == "note.changed" and l["data"]["state"] == "read", 600)
        out["read"] = read
        rid, _ = app.send("tasks.pause", {"id": task})
        _, line = app.wait(response(rid), 30)
        out["pause"] = line.get("error") or "ok"
        paused = first(app, lambda l: l.get("event") == "task.changed" and l["data"]["task"].get("paused"), 120)
        out["paused"] = bool(paused)
        time.sleep(5)
        rid, _ = app.send("tasks.resume", {"id": task})
        _, line = app.wait(response(rid), 30)
        out["resume"] = line.get("error") or "ok"
        done = first(app, lambda l: l.get("event") == "task.changed" and l["data"]["task"]["id"] == task and l["data"]["task"]["state"] in ("done", "failed", "unverified"), 900)
        out["ended"] = done and done["task"]["state"]
        rid, _ = app.send("tasks.entries", {"id": task, "limit": 50})
        _, line = app.wait(response(rid), 60)
        out["entries"] = len((line.get("result") or {}).get("entries") or [])
    return out


def serve_busy(port):
    """A provider that answers every call with 503, so the engine's retry
    ladder runs and says each retry. An unreachable address is not used: the
    engine reads a refused dial as no connection and waits for the network
    without retrying."""
    import http.server
    import threading

    class Busy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = b'{"error":{"message":"the provider is busy","code":503}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Busy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def scenario_retry(app):
    """A provider that answers 503: retries said as they happen, then the failure."""
    agent, lat = start(app, folder("retry"), "Say hi.", "trace-retry-1")
    done = first(app, lambda l: l.get("event") == "turn.done", 600)
    retries = [l["data"] for l in app.seen if l.get("event") == "turn.retrying"]
    footers = [l["data"]["entry"] for l in app.seen if l.get("event") == "entry.added" and l["data"]["entry"]["kind"] == "footer"]
    return {"retries": retries, "footers": footers, "done": done}


# ── Wave 4: the Inbox ─────────────────────────────────────────────────────────


def scenario_inbox(app):
    """An ask answered from the Inbox, one undone in its hold and answered in
    the dock instead, which clears it from the Inbox."""
    agent, lat = start(app, folder("inbox"), "Run these three commands with your bash tool, strictly one at a time, waiting for each to finish: `echo one`, then `echo two`, then `echo three`. Then reply with one short sentence.", "trace-inbox-1")
    out = {}
    added = first(app, lambda l: l.get("event") == "inbox.added", 300)
    if not added:
        return {"error": "no inbox item"}
    rid, _ = app.send("inbox.list", {"list": "waiting"})
    _, line = app.wait(response(rid), 30)
    out["listed"] = len(line["result"]["items"])
    rid, _ = app.send("inbox.answer", {"id": added["item"]["id"], "option": "allow_once"})
    _, line = app.wait(response(rid), 30)
    out["held_until"] = (line.get("result") or {}).get("undo_until")
    resolved = first(app, lambda l: l.get("event") == "inbox.resolved", 30)
    out["first"] = resolved
    added = first(app, lambda l: l.get("event") == "inbox.added" and l["data"]["item"]["id"] != out["first"]["id"], 300)
    if added:
        item = added["item"]
        rid, _ = app.send("inbox.answer", {"id": item["id"], "option": "allow_once"})
        app.wait(response(rid), 30)
        time.sleep(1)
        rid, _ = app.send("inbox.undo", {"id": item["id"]})
        _, line = app.wait(response(rid), 30)
        out["undo"] = line.get("error") or "ok"
        time.sleep(6)
        rid, _ = app.send("asks.answer", {"agent": agent, "id": item["ask"]["id"], "option": "allow_once"})
        app.wait(response(rid), 30)
        out["second"] = first(app, lambda l: l.get("event") == "inbox.resolved" and l["data"]["id"] == item["id"], 30)
    # Whatever is still waiting (one batch makes several items) is answered in
    # the conversation until the reply ends.
    deadline = time.time() + 300
    while time.time() < deadline and not any(l.get("event") == "turn.done" for l in app.seen):
        rid, _ = app.send("inbox.list", {"list": "waiting"})
        _, line = app.wait(response(rid), 30)
        for item in (line.get("result") or {}).get("items") or []:
            rid, _ = app.send("asks.answer", {"agent": agent, "id": item["ask"]["id"], "option": "allow_once"})
            app.wait(response(rid), 30)
        time.sleep(2)
    rid, _ = app.send("inbox.list", {"list": "done"})
    _, line = app.wait(response(rid), 30)
    out["counts"] = line["result"]["counts"]
    return out


# ── Wave 5: a real crew ───────────────────────────────────────────────────────


def allow_permissions(app):
    """Answers permission asks only; a crew's own asks are the scenario's."""
    def answer(line):
        if line.get("event") == "ask.opened":
            ask = line["data"]["ask"]
            if ask.get("subkind") == "permission":
                app.send("asks.answer", {"agent": ask["agent"], "id": ask["id"], "option": "allow_once"})
    app.on_line = answer


def call(app, method, params, timeout=120):
    rid, _ = app.send(method, params)
    _, line = app.wait(response(rid), timeout)
    return line.get("result") if "result" in line else {"error": line.get("error")}


def scenario_crew(app):
    """A real crew: proposed by an agent and made from the app, its members
    started by the manager and talking, a sub-crew, a question climbing from
    the sub-crew to the person, a wrap-up report, finished and reopened."""
    allow_permissions(app)
    out = {}
    ws = folder("crew", ["README.md"])
    agent, _ = start(app, ws, "We are going to write a tiny static site in this folder: index.html (a home page) and about.html. Reply with one short sentence; do not write anything yet.", "crew-1")
    app.wait(event("turn.done", agent=agent), 300)
    out["propose"] = call(app, "crews.propose", {"agent": agent})
    prop = first(app, lambda l: l.get("event") == "ask.opened" and l["data"]["ask"].get("subkind") == "crew_proposal", 400)
    out["proposal"] = prop and prop["ask"]
    if not prop:
        return out
    out["create"] = call(app, "asks.answer", {"agent": agent, "id": prop["ask"]["id"], "option": "create"}, 300)
    crews = call(app, "crews.list", {})
    crew = next((c for c in crews.get("crews", []) if c.get("manager") == agent), None)
    out["crew"] = crew
    if not crew:
        return out
    cid = crew["id"]
    joined = first(app, lambda l: l.get("event") == "member.changed" and l["data"]["member"]["crew"] == cid and l["data"]["member"]["agent"] != agent, 300)
    out["first_helper"] = joined and joined["member"]
    talk = first(app, lambda l: l.get("event") == "crew.log" and l["data"]["line"]["crew"] == cid and l["data"]["line"]["kind"] in ("note", "event") and l["data"]["line"]["from"] not in ("manager", "you", "system"), 900)
    out["member_talk"] = talk and talk["line"]
    out["questions_up"] = call(app, "crews.update", {"id": cid, "settings": {"questions_up": True}})
    out["to_manager"] = call(app, "crews.message", {"crew": cid, "to": "manager", "text": (
        "Start a sub-team now with team_start kind team: name docs, handle writer, brief: write about.html with one paragraph about the site. "
        "When a question from @writer reaches you, do not decide it: send it up to me with team_escalate to you."), "client_id": "crew-2"}, 120)
    sub = first(app, lambda l: l.get("event") == "crew.changed" and l["data"]["crew"].get("parent") == cid and l["data"]["crew"].get("manager"), 900)
    out["sub_crew"] = sub and {k: sub["crew"].get(k) for k in ("id", "name", "parent", "manager", "brief", "tint")}
    if sub and sub["crew"].get("manager"):
        writer = sub["crew"]["manager"]
        out["to_writer"] = call(app, "crews.message", {"crew": sub["crew"]["id"], "to": "manager", "text": (
            "Before you write, ask your manager with the ask tool whether about.html should name the authors or stay anonymous. "
            "It is not yours to decide, so ask; then wait for the answer."), "client_id": "crew-3"}, 120)
        climbed = first(app, lambda l: l.get("event") == "ask.opened" and l["data"]["ask"].get("subkind") == "crew_decision", 900)
        out["climbed"] = climbed and climbed["ask"]
        if climbed:
            ask = climbed["ask"]
            option = (ask.get("options") or [{"id": ""}])[0]["id"]
            out["answer"] = call(app, "asks.answer", {"agent": ask["agent"], "id": ask["id"], "option": option, "text": "" if option else "Stay anonymous."})
            out["decided"] = first(app, lambda l: l.get("event") == "decision.changed" and l["data"]["decision"]["id"] == ask["id"].split(":", 1)[1], 60)
    out["wrap_up"] = call(app, "crews.wrap_up", {"id": cid})
    report = first(app, lambda l: l.get("event") == "decision.opened" and l["data"]["decision"]["kind"] == "closing", 900)
    out["report"] = report and report["decision"]
    if report:
        out["finish"] = call(app, "crews.decide", {"packet": report["decision"]["id"], "option": "close"})
        closed = first(app, lambda l: l.get("event") == "crew.changed" and l["data"]["crew"]["id"] == cid and l["data"]["crew"]["state"] == "closed", 60)
        out["closed"] = bool(closed)
        again = call(app, "crews.reopen", {"id": cid})
        out["reopened"] = again.get("crew", {}).get("state") if isinstance(again, dict) else again
    out["log_lines"] = len(call(app, "crews.log", {"id": cid}).get("lines", []))
    return out


SCENARIOS = {
    "proposal": (scenario_proposal, "task-proposal-clock", "allow"),
    "yourcall": (scenario_yourcall, "task-your-call", "allow"),
    # The terminal's task belt keeps quick_task off the model's tools; with it
    # off the session tree runs both verbs, which is the road quick tasks are on.
    "quick": (scenario_quick, "quick-undo", "allow", [], {"CODEAF_TASK_BELT": "off"}),
    "process": (scenario_process, "process-start-stop", "allow"),
    "long": (scenario_long, "turn-long-move", "allow", ["--turn-long", "10s"]),
    "worklog": (scenario_worklog, "worklog-20-calls", "allow"),
    "steer": (scenario_steer, "steer-consumed-fell-through", "allow"),
    "queue": (scenario_queue, "queue-edit-reorder", "allow"),
    "update": (scenario_update, "thinking-and-update", "allow"),
    "asks": (scenario_asks, "ask-choice-and-clarification", "allow"),
    "batch": (scenario_batch, "permission-batch", "prompt"),
    "tasknote": (scenario_tasknote, "task-note-pause-resume", "allow"),
    "inbox": (scenario_inbox, "inbox-answer-undo-dock", "prompt"),
    "crew": (scenario_crew, "crew-real", "allow"),
    "retry": (scenario_retry, "provider-retry", "allow", [], {"CODEAF_BASE_URL": "http://127.0.0.1:18503/v1"}),
    "keys": (scenario_keys, "keys-check-bad", "allow"),
    "budget": (scenario_budget, "turn-budget-ask", "allow"),
    "basic": (scenario_basic, "turn-basic", "allow"),
    "tool": (scenario_tool, "turn-tool", "allow"),
    "permission": (scenario_permission, "turn-permission", "prompt"),
    "tasks": (scenario_tasks, "turn-nested-tasks", "allow"),
    "stop": (scenario_stop, "turn-stop", "allow"),
    "latency": (scenario_latency, "latency", "allow"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", default=os.path.expanduser("~/.codeaf"))
    ap.add_argument("--scenario", action="append")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    results = {}
    for name in args.scenario or ["basic", "tool", "permission", "tasks", "stop"]:
        run, file, approval = SCENARIOS[name][:3]
        extra = SCENARIOS[name][3] if len(SCENARIOS[name]) > 3 else ()
        extra_env = SCENARIOS[name][4] if len(SCENARIOS[name]) > 4 else None
        if name == "retry":
            serve_busy(18503)
        home = os.path.join(args.out, "home-" + name)
        make_home(home, args.source, approval)
        app = App(args.bin, home, os.path.join(args.out, file + ".jsonl"), os.path.join(args.out, file + ".requests.jsonl"), extra, extra_env)
        try:
            results[name] = run(app)
        except Exception as err:
            results[name] = {"error": str(err)}
        finally:
            app.close()
            forget_key(home)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    sys.exit(main())
