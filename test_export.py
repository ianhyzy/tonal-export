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
print("ok")
