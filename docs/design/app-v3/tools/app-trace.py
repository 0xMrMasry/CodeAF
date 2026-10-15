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
    agent, lat = start(app, folder("long"), "Write ten files here, essay1.md to essay10.md, each a 400-word essay on a different sea creature, one write call per file. Do not use a task; do it yourself in this reply.", "trace-long-1")
    long = first(app, lambda l: l.get("event") == "turn.long", 300)
    out = {"turn_long": long}
    if long:
        rid, _ = app.send("agents.move_to_task", {"id": agent, "turn": long["turn"]})
        _, line = app.wait(response(rid), 200)
        out["moved"] = line.get("result") or line.get("error")
    app.wait(event("turn.done", agent=agent), 600)
    return out


SCENARIOS = {
    "proposal": (scenario_proposal, "task-proposal-clock", "allow"),
    "yourcall": (scenario_yourcall, "task-your-call", "allow"),
    # The terminal's task belt keeps quick_task off the model's tools; with it
    # off the session tree runs both verbs, which is the road quick tasks are on.
    "quick": (scenario_quick, "quick-undo", "allow", [], {"CODEAF_TASK_BELT": "off"}),
    "process": (scenario_process, "process-start-stop", "allow"),
    "long": (scenario_long, "turn-long-move", "allow", ["--turn-long", "20s"]),
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
