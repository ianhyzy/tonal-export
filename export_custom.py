"""Custom-workout tables. Used by export_history.py (API data); also works standalone on
the companion app's persisted redux state:

Usage: adb exec-out cat /data/data/com.tonal.companion/app_flutter/redux > redux
       python3 -I export_custom.py redux OUT_DIR
Writes tonal_custom_workouts.csv and tonal_custom_sets.csv (join on workout_id).
weight_pct is % of working weight; modifiers is ';'-separated.
Each workout must have been opened once in the app so its sets are cached.
"""
import csv, json, os, sys

FLAGS = ["warmUp", "dropSet", "burnout", "eccentric", "chains", "spotter", "smartFlex", "progressive", "finalSet"]


def export(d):
    mv = d["movements"]
    workouts = []
    wcols = ["workout_id", "title", "target_area", "body_regions", "duration_min", "set_count", "created", "source"]
    srows = []
    for u in d["userWorkouts"]:
        w = d["workouts"].get(u["id"], u)
        sets = sorted(w.get("sets", []), key=lambda s: s.get("sortOrder", 0))
        if not sets:
            print(f"warning: no sets cached for {w['title']} — open it in the app", file=sys.stderr)
        workouts.append([w["id"], w["title"], w.get("targetArea", ""), ";".join(w.get("bodyRegions", [])),
                        round(w.get("duration", 0) / 60, 1), len(sets), w.get("publishedAt", "")[:10],
                        w.get("createdSource", "")])
        for i, s in enumerate(sets, 1):
            m = mv.get(s["movementId"], {})
            srows.append([w["id"], w["title"], i, s["blockNumber"], s["round"], s["repetitionTotal"],
                          m.get("name", s["movementId"]), s.get("prescribedReps", ""),
                          s.get("prescribedDuration", ""), s.get("weightPercentage", ""),
                          ";".join(f for f in FLAGS if s.get("flex" if f == "smartFlex" else f)), s.get("description", "")])
    scols = ["workout_id", "workout", "seq", "block", "round", "rounds_total", "movement", "reps",
             "duration_sec", "weight_pct", "modifiers", "notes"]
    return {"tonal_custom_workouts": (wcols, workouts), "tonal_custom_sets": (scols, srows)}


if __name__ == "__main__":
    for name, (cols, rows) in export(json.load(open(sys.argv[1]))).items():
        with open(os.path.join(sys.argv[2], name + ".csv"), "w", newline="") as f:
            csv.writer(f).writerows([cols, *rows])
        print(f"{name}.csv: {len(rows)} rows", file=sys.stderr)
