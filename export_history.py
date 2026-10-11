"""Export Tonal workout history (per-set weights/reps) from api.tonal.com.

Usage: python3 -I export_history.py OUT_DIR [--render]   (--render: skip download, rebuild from cache)
Writes tonal_workouts.csv, tonal_sets.csv, tonal_movement_progress.csv and
tonal_strength_scores.csv, tonal_custom_workouts.csv and tonal_custom_sets.csv
to OUT_DIR. Sets join workouts on activity_id. Weights
in lb; weight_lb is the set (base) weight, eccentric_lb/chains_lb are added load.
Power, velocity, ROM and 0-1 struggling/inconsistency scores are Tonal's raw
values. rest_before_sec = gap since previous set ended; set HR comes from the
workout's HR series during the set. modifiers/prs are ';'-separated.
On first run, signs in with TONAL_USERNAME/TONAL_PASSWORD from the environment or the
Tonal MCP's ~/Documents/ts-tonal-mcp/.env, else prompts (same Auth0 login the roni app
uses); the Auth0 refresh token is then kept in the macOS login keychain (service
"tonal-export") so later runs don't ask. Delete it with:
  security delete-generic-password -s tonal-export
Raw activity JSON is cached in ./history/ next to this script, so re-runs only fetch
new workouts. Exits non-zero (and posts a macOS notification when unattended) if
anything couldn't be fetched or written.
"""
import csv, getpass, io, json, os, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
import export_custom  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "history")
API = "https://api.tonal.com"
CLIENT_ID = "ERCyexW-xoVG_Yy3RDe-eV4xsOnRHP6L"  # Tonal's public Auth0 client id (from roni)
MCP_ENV = os.path.expanduser("~/Documents/ts-tonal-mcp/.env")
NET_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)  # URLError includes HTTPError


class Fatal(Exception):
    """Expected failure; str() is the one line telling the user what to fix."""


def req(url, token=None, body=None, headers=None, tries=4):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers=h)
    for n in range(tries):
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.load(resp), resp.headers
        except urllib.error.HTTPError as e:
            if n == tries - 1 or not (e.code == 429 or e.code >= 500):
                raise
        except NET_ERRORS:
            if n == tries - 1:
                raise
        time.sleep(2 ** n)


KEYCHAIN = ["-s", "tonal-export", "-a", "refresh_token"]


def keychain_get():
    r = subprocess.run(["security", "find-generic-password", *KEYCHAIN, "-w"], capture_output=True, text=True)
    return r.stdout.strip() or None


def keychain_set(token):
    # via `security -i` stdin so the token never appears in a process argv
    cmd = f'add-generic-password -U {" ".join(KEYCHAIN)} -w "{token}"\n'
    subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, check=True)


def mcp_creds(path=MCP_ENV):
    """(email, password, source) the Tonal MCP setup has: env vars, else its .env file. None if neither."""
    u, p = os.environ.get("TONAL_USERNAME"), os.environ.get("TONAL_PASSWORD")
    if u and p:
        return u, p, "TONAL_USERNAME/TONAL_PASSWORD env vars"
    env = {}
    try:
        for line in open(path):
            k, sep, v = line.strip().partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            if sep:
                env[k.strip()] = v
    except OSError:
        return None
    if env.get("TONAL_USERNAME") and env.get("TONAL_PASSWORD"):
        return env["TONAL_USERNAME"], env["TONAL_PASSWORD"], path
    return None


def auth(body):
    data, _ = req("https://tonal.auth0.com/oauth/token", body={"client_id": CLIENT_ID, **body})
    if data.get("refresh_token"):
        keychain_set(data["refresh_token"])
    return data["id_token"]


def login():
    """Keychain refresh token, else the MCP's credentials, else prompt (terminal only)."""
    rt = keychain_get()
    if rt:
        try:
            return auth({"grant_type": "refresh_token", "refresh_token": rt})
        except urllib.error.HTTPError as e:
            if e.code not in (400, 401, 403):
                raise  # transient: already retried by req(); keep the keychain token
            print("saved Tonal login expired, signing in again", file=sys.stderr)
    creds = mcp_creds()
    if creds:
        email, pw, src = creds
    elif sys.stdin.isatty():
        email, pw, src = input("Tonal email: "), getpass.getpass("Tonal password: "), "the prompt"
    else:
        raise Fatal(f"not signed in to Tonal: add TONAL_USERNAME/TONAL_PASSWORD to {MCP_ENV} "
                    "or run export_history.py once in a terminal")
    try:
        return auth({"grant_type": "http://auth0.com/oauth/grant-type/password-realm",
                     "username": email, "password": pw, "realm": "Username-Password-Authentication",
                     "scope": "openid profile email offline_access"})
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            raise Fatal(f"Tonal login rejected for the email/password from {src}")
        raise


_token = None


def api(path, headers=None):
    """GET from api.tonal.com; on 401 (token expired mid-run) sign in again once."""
    global _token
    if _token is None:
        _token = login()
    try:
        return req(API + path, _token, headers=headers)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        _token = login()
        return req(API + path, _token, headers=headers)


def write(path, text):
    """Atomic: a crash never leaves a truncated file."""
    with open(path + ".tmp", "w", newline="") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def read_json(path):
    """Parsed JSON, or None if the file is missing or corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(name, data):
    write(os.path.join(HERE, name + ".json"), json.dumps(data))


def load(name):
    data = read_json(os.path.join(HERE, name + ".json"))
    if data is None:
        print(f"no usable {name}.json yet (run without --render)", file=sys.stderr)
        return []
    return data


def fetch_details(uid, tonal):
    """Download activities not yet cached (or cached corrupt). Returns (fetched, skipped_ids)."""
    fetched, skipped = 0, []
    for i, a in enumerate(tonal, 1):
        path = os.path.join(CACHE, a["id"] + ".json")
        if read_json(path) is not None:
            continue
        try:
            detail = api(f"/v6/users/{uid}/workout-activities/{a['id']}")[0]
        except NET_ERRORS as e:
            print(f"  skipped {a['id']}: {e}", file=sys.stderr)
            skipped.append(a["id"])
            continue
        if not detail.get("endTime"):
            print(f"  {a['id']} still in progress, will fetch next run", file=sys.stderr)
            continue
        write(path, json.dumps(detail))
        fetched += 1
        print(f"  {i}/{len(tonal)} {detail.get('beginTime', '')[:10]} {(detail.get('contentCard') or {}).get('workoutTitle', '')}", file=sys.stderr)
    return fetched, skipped


def fetch():
    os.makedirs(CACHE, exist_ok=True)
    uid = api("/v6/users/userinfo")[0]["id"]
    acts, off = [], 0
    while True:
        page, hdr = api(f"/v6/users/{uid}/workout-activities", headers={"pg-offset": str(off), "pg-limit": "200"})
        acts += page
        off += len(page)
        if not page or off >= int(hdr.get("pg-total") or off):
            break
    strength, _ = api(f"/v6/users/{uid}/strength-scores/history?limit=1000")
    save("strength_history", strength)
    print(f"{len(strength)} strength score entries", file=sys.stderr)
    for name, path in (("movements", "/v6/movements"), ("coaches", "/v6/coaches"),
                       ("training_types", "/v6/training-types")):
        save(name, api(path)[0])
    custom = [api(f"/v6/user-workouts/{w['id']}")[0] for w in api("/v6/user-workouts")[0]]
    save("custom_workouts", custom)
    print(f"{len(custom)} custom workouts", file=sys.stderr)
    tonal = [a for a in acts if a.get("activityType") != "External"]
    print(f"{len(acts)} activities, {len(tonal)} Tonal workouts", file=sys.stderr)
    return fetch_details(uid, tonal)


def ts(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def local_date(v, tz):
    try:
        return ts(v).astimezone(ZoneInfo(tz or "UTC")).date().isoformat()
    except Exception:
        return v[:10]


def hr_window(series, begin, end):
    """(avg, max) heart rate over [begin, end] from the workout's HR time series."""
    try:
        b, e = ts(begin), ts(end)
    except Exception:
        return "", ""
    vals = [h for t, h in series if b <= t <= e]
    return (round(sum(vals) / len(vals)), max(vals)) if vals else ("", "")


# workout_type -> the app's Explore "Format" filter label
FORMATS = {"Linear": "Classic", "PT": "Programs+", "DailyLift": "Custom by Tonal", "Custom": "Custom Workouts"}


SESSION_GAP_MIN = 60  # activities starting within this many minutes of the previous one's end = one session


def sessions(details):
    """activity_id -> session_id (the first activity's id) for back-to-back activities."""
    out, sid, prev_end = {}, None, None
    for d in sorted(details, key=lambda d: d.get("beginTime", "")):
        try:
            b, e = ts(d["beginTime"]), ts(d["endTime"])
        except Exception:
            b = e = None
        if not (b and prev_end and (b - prev_end).total_seconds() <= SESSION_GAP_MIN * 60):
            sid = d["id"]
        out[d["id"]] = sid
        prev_end = max(prev_end, e) if prev_end and e else e
    return out


def render(details, movements, coaches, types):
    sess = sessions(details)
    details = sorted(details, key=lambda d: d.get("beginTime", ""), reverse=True)
    wcols = ["activity_id", "session_id", "date", "start_time", "workout", "workout_type", "format", "training_type", "coach", "target_area", "level",
             "program", "program_week", "program_day", "duration_min", "active_min", "rest_min", "total_movements", "total_sets", "total_reps",
             "total_volume_lb", "total_work_kj", "percent_completed", "avg_hr", "min_hr", "max_hr", "hr_source",
             "calories_watch", "calories_tonal", "workout_id"]
    scols = ["activity_id", "session_id", "date", "workout", "block", "round", "movement", "side", "weight_lb",
             "avg_weight_lb", "max_weight_lb", "eccentric_lb", "chains_lb", "suggested_weight_lb", "reps",
             "rep_goal", "duration_sec", "rest_before_sec", "volume_lb", "work_j", "est_1rm_lb",
             "reps_in_reserve", "peak_power", "velocity_at_peak", "weight_at_peak_lb", "rom", "total_rom_in",
             "struggling_score", "inconsistency_score", "avg_hr", "max_hr", "spotter", "modifiers", "prs",
             "begin_time"]
    workouts = []
    sets = []
    for d in details:
        c = d.get("contentCard") or {}
        tz = d.get("timezone")
        date = local_date(d.get("beginTime", ""), tz)
        try:
            start = ts(d["beginTime"]).astimezone(ZoneInfo(tz or "UTC")).strftime("%H:%M")
        except Exception:
            start = ""
        title = c.get("workoutTitle", "")
        hr = d.get("workoutHeartRate") or {}
        series = []
        for h in hr.get("workoutHeartRateValues") or []:
            try:
                series.append((ts(h["timestamp"]), h["heartRate"]))
            except Exception:
                pass
        cal = {x.get("algorithm"): round(x.get("caloriesBurned", 0)) for x in d.get("calories") or []}
        workouts.append([d["id"], sess[d["id"]], date, start, title, d.get("workoutType", ""),
                         FORMATS.get(d.get("workoutType"), ""),
                         ";".join(types.get(t, t) for t in c.get("trainingTypeIds") or []), coaches.get(c.get("coachId"), ""),
                        c.get("targetArea", ""), c.get("level", ""), c.get("programName", ""),
                        c.get("programWeek") or "", c.get("programDay") or "",
                        round((d.get("totalDuration") or 0) / 60, 1), round((d.get("activeDuration") or 0) / 60, 1),
                        round((d.get("restDuration") or 0) / 60, 1), d.get("totalMovements", ""),
                        d.get("totalSets", ""), d.get("totalReps", ""), d.get("totalVolume", ""),
                        round((d.get("totalConcentricWork") or 0) / 1000, 1), d.get("percentCompleted", ""),
                        hr.get("avgHeartRate") or "", hr.get("minHeartRate") or "", hr.get("maxHeartRate") or "",
                        hr.get("heartRateSource", ""), cal.get("AppleWatch", ""), cal.get("Tonal", ""),
                        d.get("workoutId", "")])
        prev_end = None
        for s in sorted(d.get("workoutSetActivity") or [], key=lambda s: s.get("beginTime", "")):
            mods = ";".join(n for n, k in (("warmUp", "warmUp"), ("dropSet", "dropSet"), ("burnout", "burnout"),
                                           ("eccentric", "eccentric"), ("chains", "chains"), ("smartFlex", "flex"))
                            if s.get(k))
            r = lambda k, n=1: "" if s.get(k) is None else round(s[k], n)
            try:
                rest = round((ts(s["beginTime"]) - prev_end).total_seconds()) if prev_end else ""
                prev_end = ts(s["endTime"])
            except Exception:
                rest = ""
            sets.append([d["id"], sess[d["id"]], date, title, s.get("blockNumber", ""), s.get("round", ""),
                         movements.get(s["movementId"], s["movementId"]),
                         "" if s.get("movementSide") == "Both" else s.get("movementSide", ""),
                         r("baseWeight"), r("avgWeight"), r("maxWeight"), r("eccentricWeight"), r("chainsWeight"),
                         r("suggestedWeight"), s.get("repCount", ""), s.get("prescribedReps", ""),
                         s.get("duration", ""), rest, s.get("volume", ""), r("totalConcentricWork", 0),
                         r("oneRepMax"), r("repsInReserve"), r("maxConPower", 0), r("velAtMaxConPower"),
                         r("weightAtMaxConPower"), r("rom"), r("romLengthIn"), r("strugglingScore", 2),
                         r("inconsistencyScore", 2), *hr_window(series, s.get("beginTime", ""), s.get("endTime", "")),
                         s.get("spotterMode", ""), mods, ";".join(s.get("prs") or []), s.get("beginTime", "")])
    return {"tonal_workouts": (wcols, workouts), "tonal_sets": (scols, sets),
            "tonal_movement_progress": progress(scols, sets)}


def progress(scols, sets):
    """Per movement per ISO week: working sets only (reps > 0, not warm-up)."""
    i = {c: n for n, c in enumerate(scols)}
    agg = {}
    for r in sets:
        if not r[i["reps"]] or "warmUp" in r[i["modifiers"]]:
            continue
        y, w, _ = datetime.fromisoformat(r[i["date"]]).isocalendar()
        a = agg.setdefault((f"{y}-W{w:02d}", r[i["movement"]]), {"sessions": set(), "sets": 0, "reps": 0, "vol": 0,
                                                               "wt": 0, "orm": 0, "pwr": 0})
        a["sessions"].add(r[i["activity_id"]])
        a["sets"] += 1
        a["reps"] += r[i["reps"]]
        a["vol"] += r[i["volume_lb"]] or 0
        a["wt"] = max(a["wt"], r[i["weight_lb"]] or 0)
        a["orm"] = max(a["orm"], r[i["est_1rm_lb"]] or 0)
        a["pwr"] = max(a["pwr"], r[i["peak_power"]] or 0)
    cols = ["week", "movement", "sessions", "working_sets", "total_reps", "total_volume_lb", "max_weight_lb",
            "best_est_1rm_lb", "best_peak_power"]
    rows = [[wk, m, len(a["sessions"]), a["sets"], a["reps"], round(a["vol"]), a["wt"], a["orm"], a["pwr"]]
            for (wk, m), a in sorted(agg.items(), reverse=True)]
    return cols, rows


def strength_table(entries):
    cols = ["date", "activity_time", "overall", "upper", "lower", "core", "activity_id"]
    rows = [[(e.get("activityTime") or "")[:10], e.get("activityTime", ""), e.get("overall", ""), e.get("upper", ""),
             e.get("lower", ""), e.get("core", ""), e.get("workoutActivityId", "")]
            for e in sorted(entries, key=lambda e: e.get("activityTime", ""), reverse=True)]
    return cols, rows


def notify(msg):
    subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title "Tonal export failed"'],
                   capture_output=True)


def main(argv):
    """Returns the list of problems; empty means everything was fetched and written."""
    if len(argv) < 2 or argv[1].startswith("-"):
        return ["usage: python3 -I export_history.py OUT_DIR [--render]"]
    out = argv[1]
    if not os.path.isdir(out):
        return [f"output folder not found: {out} (is Google Drive running?)"]
    problems, fetched, skipped = [], 0, []
    if "--render" not in argv:
        try:
            fetched, skipped = fetch()
        except Fatal as e:
            problems.append(f"{e}; CSVs rebuilt from cache")
        except NET_ERRORS as e:
            problems.append(f"Tonal download failed ({e}); CSVs rebuilt from cache, data may be stale")
        if skipped:
            problems.append(f"skipped {len(skipped)} activities after retries: {' '.join(skipped)}")
    movements = {m["id"]: m for m in load("movements")}
    coaches = {c["id"]: c["name"] for c in load("coaches")}
    details = []
    for f in sorted(os.listdir(CACHE)) if os.path.isdir(CACHE) else []:
        if f.endswith(".json"):
            d = read_json(os.path.join(CACHE, f))
            if d is None:
                problems.append(f"corrupt history/{f} left out of the CSVs (next fetch replaces it)")
            else:
                details.append(d)
    types = {t["id"]: t["name"] for t in load("training_types")}
    tables = render(details, {k: m["name"] for k, m in movements.items()}, coaches, types)
    tables["tonal_strength_scores"] = strength_table(load("strength_history"))
    custom = load("custom_workouts")
    tables.update(export_custom.export({"userWorkouts": custom, "workouts": {w["id"]: w for w in custom},
                                        "movements": movements}))
    rows = 0
    for name, (cols, data) in tables.items():
        buf = io.StringIO()
        csv.writer(buf).writerows([cols, *data])
        write(os.path.join(out, name + ".csv"), buf.getvalue())
        rows += len(data)
        print(f"{name}.csv: {len(data)} rows", file=sys.stderr)
    print(f"done: {fetched} activities fetched, {len(skipped)} skipped, {rows} rows written", file=sys.stderr)
    return problems


if __name__ == "__main__":
    print(datetime.now().isoformat(timespec="seconds"), file=sys.stderr)
    problems = main(sys.argv)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        if not sys.stdin.isatty():
            notify(problems[0])
        sys.exit(1)
