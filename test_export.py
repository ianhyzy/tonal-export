"""Plain-assert checks for export_history: python3 -I test_export.py"""
import io, os, sys, tempfile, time, urllib.error

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
