"""Plain-assert checks for export_history: python3 -I test_export.py"""
import io, json, os, sys, tempfile, time, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_history as eh  # noqa: E402

time.sleep = lambda s: None  # no real backoff waits


def http(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


# mcp_creds: env vars win, then the .env file (quotes stripped), else None
with tempfile.TemporaryDirectory() as d:
    env = os.path.join(d, ".env")
    with open(env, "w") as f:
        f.write("TONAL_USERNAME=a@b.c\nTONAL_PASSWORD='p=w\"x'\n")
    os.environ.pop("TONAL_USERNAME", None), os.environ.pop("TONAL_PASSWORD", None)
    assert eh.mcp_creds(env) == ("a@b.c", 'p=w"x', env)
    assert eh.mcp_creds(os.path.join(d, "missing")) is None
    os.environ.update(TONAL_USERNAME="e@v", TONAL_PASSWORD="pw")
    assert eh.mcp_creds(env)[:2] == ("e@v", "pw")
    del os.environ["TONAL_USERNAME"], os.environ["TONAL_PASSWORD"]

# claude_creds: an MCP's env in Claude Code (user, then project scope) or Desktop config;
# unreadable, malformed and Tonal-less configs are skipped
with tempfile.TemporaryDirectory() as d:
    def cfg(name, data):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return p
    tonal = {"command": "node", "env": {"TONAL_USERNAME": "c@d", "TONAL_PASSWORD": "pw"}}
    bad = cfg("bad.json", '{"trunc')
    odd = cfg("odd.json", {"mcpServers": [], "projects": {"x": None, "y": {"mcpServers": {"s": {"env": "?"}}}}})
    none = cfg("none.json", {"mcpServers": {"other": {"env": {"TONAL_USERNAME": "only-user"}}}})
    proj = cfg("proj.json", {"projects": {"C:/p": {"mcpServers": {"tonal": tonal}}}})
    desk = cfg("desk.json", {"mcpServers": {"tonal": {**tonal, "env": {**tonal["env"], "TONAL_USERNAME": "e@f"}}}})
    assert eh.claude_creds([os.path.join(d, "missing"), bad, odd, none]) is None
    assert eh.claude_creds([bad, odd, none, proj, desk]) == ("c@d", "pw", proj)
    assert eh.claude_creds([desk, proj])[0] == "e@f"

# token file (non-macOS): missing reads as None, rewrites replace it, no .tmp left behind
plat, sys.platform = sys.platform, "win32"
with tempfile.TemporaryDirectory() as d:
    eh.TOKEN_FILE = os.path.join(d, "tonal-export", "refresh_token")
    assert eh.keychain_get() is None
    eh.keychain_set("rt1"), eh.keychain_set("rt2")
    assert eh.keychain_get() == "rt2" and os.listdir(os.path.dirname(eh.TOKEN_FILE)) == ["refresh_token"]
# unknown timezone (Windows without tzdata): same instant in this computer's zone; no tz = UTC
assert eh.to_local("2026-01-02T03:04:05Z", "No/Such") == eh.ts("2026-01-02T03:04:05Z")
assert eh.to_local("2026-01-02T03:04:05Z", None).strftime("%H:%M") == "03:04"
sys.platform = plat

# req: retries 503/timeouts, gives up on 404 at once, raises after the last try
calls = []


def fake_urlopen(outcomes):
    def f(r, timeout):
        calls.append(1)
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        resp = io.BytesIO(b'{"ok": 1}')
        resp.headers = {}
        return resp
    return f


eh.urllib.request.urlopen = fake_urlopen([http(503), TimeoutError(), "ok"])
assert eh.req("http://x")[0] == {"ok": 1} and len(calls) == 3
calls.clear()
eh.urllib.request.urlopen = fake_urlopen([http(404)])
try:
    eh.req("http://x")
    assert False
except urllib.error.HTTPError as e:
    assert e.code == 404 and len(calls) == 1
calls.clear()
eh.urllib.request.urlopen = fake_urlopen([http(500)] * 4)
try:
    eh.req("http://x")
    assert False
except urllib.error.HTTPError:
    assert len(calls) == 4

# fetch_details: failed download is skipped, in-progress one not cached, rest written
with tempfile.TemporaryDirectory() as d:
    eh.CACHE = d
    with open(os.path.join(d, "bad.json"), "w") as f:
        f.write('{"trunc')  # corrupt cache gets refetched
    replies = {"/v6/users/u/workout-activities/ok": {"id": "ok", "endTime": "t"},
               "/v6/users/u/workout-activities/bad": {"id": "bad", "endTime": "t"},
               "/v6/users/u/workout-activities/live": {"id": "live"}}

    def fake_api(path, headers=None):
        if path not in replies:
            raise urllib.error.URLError("down")
        return replies[path], {}
    eh.api = fake_api
    acts = [{"id": i} for i in ("ok", "bad", "live", "gone")]
    assert eh.fetch_details("u", acts) == (2, ["gone"])
    assert sorted(os.listdir(d)) == ["bad.json", "ok.json"]
    assert eh.read_json(os.path.join(d, "bad.json")) == {"id": "bad", "endTime": "t"}

# main: missing arg / folder give one line, not a traceback
assert eh.main(["x"])[0].startswith("usage:")
assert "not found" in eh.main(["x", "/nonexistent/dir"])[0]

# UTF-8 everywhere, whatever the locale (Windows defaults to cp1252): files, .env, and a
# redirected stderr printing a workout title it can't encode
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "t.json")
    eh.write(p, '{"t": "Arms 💪"}')
    with open(p, "rb") as f:
        assert f.read() == '{"t": "Arms 💪"}'.encode("utf-8")
    assert eh.read_json(p) == {"t": "Arms 💪"}
    env = os.path.join(d, ".env")
    with open(env, "w", encoding="utf-8") as f:
        f.write("TONAL_USERNAME=é@b.c\nTONAL_PASSWORD=pä💪\n")
    assert eh.mcp_creds(env)[:2] == ("é@b.c", "pä💪")
    with open(env, "wb") as f:
        f.write(b"TONAL_USERNAME=a\nTONAL_PASSWORD=\xe9\xff\n")  # not UTF-8: skipped, not a crash
    assert eh.mcp_creds(env) is None
    real, buf = sys.stderr, io.BytesIO()
    sys.stderr = io.TextIOWrapper(buf, encoding="cp1252")
    try:
        eh.utf8_stderr()
        print("Arms 💪", file=sys.stderr)
        sys.stderr.flush()
        logged = buf.getvalue()
    finally:
        sys.stderr = real
    assert logged.decode("utf-8").strip() == "Arms 💪"

# main: a failed download with an empty cache leaves existing CSVs alone; with a cache it
# rebuilds them (as UTF-8)
with tempfile.TemporaryDirectory() as d:
    out, eh.HERE, eh.CACHE = os.path.join(d, "out"), d, os.path.join(d, "history")
    os.makedirs(out), os.makedirs(eh.CACHE)
    csv_path = os.path.join(out, "tonal_workouts.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("last good export")

    def no_login():
        raise eh.Fatal("not signed in")
    eh.fetch = no_login
    probs = eh.main(["x", out])
    assert probs == [f"not signed in; CSVs in {out} left untouched"], probs
    assert eh.main(["x", out, "--render"])[0].endswith("left untouched")
    assert os.listdir(out) == ["tonal_workouts.csv"]
    with open(csv_path, encoding="utf-8") as f:
        assert f.read() == "last good export"
    eh.write(os.path.join(eh.CACHE, "a.json"), json.dumps(
        {"id": "a", "beginTime": "2026-01-02T03:04:05Z", "endTime": "2026-01-02T04:00:00Z",
         "contentCard": {"workoutTitle": "Arms 💪"}}))
    assert eh.main(["x", out]) == ["not signed in; CSVs rebuilt from cache"]
    with open(csv_path, encoding="utf-8") as f:
        assert "Arms 💪" in f.read()
    assert len(os.listdir(out)) == 6

# log: runs older than LOG_DAYS are dropped (0 keeps all), then stderr goes to both the
# terminal and the log; a log that can't be opened or written never stops the run
now = eh.datetime(2026, 10, 11, 12)
old, recent = "2026-10-01T03:00:00\nold run\n", "2026-10-08T03:00:00\nrecent run\nTraceback: x\n"
assert eh.prune_log(old + recent, 7, now) == recent
assert eh.prune_log(old + recent, 0, now) == old + recent
assert eh.prune_log(old + recent, 1, now) == ""
with tempfile.TemporaryDirectory() as d:
    log = os.path.join(d, "export.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write(old + recent)
    real, term = sys.stderr, io.StringIO()
    sys.stderr = term
    try:
        eh.start_log(log, 7, now)
        print("this run 💪", file=sys.stderr)
        sys.stderr.log.close()
        sys.stderr.write("log closed, terminal still works\n")
    finally:
        sys.stderr = real
    with open(log, encoding="utf-8") as f:
        assert f.read() == recent + "this run 💪\n"
    assert term.getvalue() == "this run 💪\nlog closed, terminal still works\n"
    assert os.listdir(d) == ["export.log"]
    sys.stderr = term
    eh.start_log(os.path.join(d, "missing", "export.log"), 7, now)
    assert sys.stderr is term
    sys.stderr = real

# notify on Windows: PowerShell toast with the message only in the environment, never argv;
# a missing powershell.exe is ignored
plat, run, ran = sys.platform, eh.subprocess.run, []
sys.platform = "win32"
eh.subprocess.run = lambda args, **kw: ran.append((args, kw))
msg = "it's \"quoted\"; $(evil) `x`"
eh.notify(msg)
args, kw = ran[0]
assert args[0] == "powershell.exe" and kw["env"]["TONAL_EXPORT_MESSAGE"] == msg
script = eh.base64.b64decode(args[-1]).decode("utf-16-le")
assert all(msg not in a for a in args) and msg not in script and "$env:TONAL_EXPORT_MESSAGE" in script


def missing(*a, **kw):
    raise FileNotFoundError("powershell.exe")
eh.subprocess.run = missing
eh.notify(msg)
sys.platform, eh.subprocess.run = plat, run

# interactive: stdin from NUL / /dev/null is not someone at a terminal (on Windows NUL is a tty)
r = eh.subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                       "import export_history; print(export_history.interactive())", os.path.dirname(eh.__file__)],
                      stdin=eh.subprocess.DEVNULL, capture_output=True, text=True)
assert r.stdout.strip() == "False", r
print("ok")
