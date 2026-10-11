# tonal-export

Downloads your full Tonal workout history from Tonal's (unofficial) API and writes it as CSV files an AI model or a spreadsheet can read: every workout, every set with its weight, reps, rest and heart rate, weekly progress per movement, and strength scores.

It's the historical half of a setup where Claude plans workouts with the [Tonal MCP server](https://github.com/dlwiest/ts-tonal-mcp). The MCP handles recent workouts and building custom workouts on the device. This script gives the model your full history in one pass. It signs in with the same Tonal login as the MCP, so if the MCP is already set up, you won't need to enter anything.

## Quick start

1. Install the [Tonal MCP server](https://github.com/dlwiest/ts-tonal-mcp) as its README describes, giving it your Tonal login with `claude mcp add … -e TONAL_USERNAME=you@example.com -e TONAL_PASSWORD=…`. This script reads the same login.
2. Run the export once in a terminal, so you can see that it signs in and where the files go (on Windows, `py -3` instead of `python3`):

   ```bash
   python3 -I export_history.py ~/path/to/output-folder
   ```

3. Optionally, [run it nightly](#running-it-nightly).

## Per-set detail in the MCP

My pull request, [dlwiest/ts-tonal-mcp#11](https://github.com/dlwiest/ts-tonal-mcp/pull/11), adds more per-set detail to the MCP's `get_workout_activity_details` tool. Tonal's API already returns these fields, but the MCP didn't show them:

- prescribed reps and duration, to compare with what you actually did
- the base weight you selected, plus any added eccentric or chains load
- active modifiers (warm-up, drop set, burnout, Smart Flex, spotter…)
- Tonal's estimated reps in reserve
- personal records hit on the set
- set duration and rest before each set

Until it's merged, use the `feat/per-set-activity-detail` branch of [my fork](https://github.com/ianhyzy/ts-tonal-mcp/tree/feat/per-set-activity-detail) if you want this detail through the MCP. The CSVs from this script include all of these except prescribed duration.

## Requirements

- macOS: the login is saved in the Keychain, and failures post a notification.
- Windows: the login is saved in a file in `%LOCALAPPDATA%\tonal-export\`, and there are no notifications. Use `py -3` where this README says `python3`. Without the `tzdata` package Python has no timezone database on Windows, so dates and start times use the computer's timezone instead of each workout's.
- Python 3.10 or newer. Standard library only, nothing to install.

## Usage

```bash
python3 -I export_history.py ~/path/to/output-folder
```

The first run downloads everything. Later runs fetch only new workouts (raw JSON is cached in `history/` next to the script) and rewrite the CSVs. Add `--render` to rebuild the CSVs from the cache without going online.

### Signing in

The script tries these in order:

1. A saved refresh token in the macOS Keychain (service `tonal-export`), or on Windows in `%LOCALAPPDATA%\tonal-export\refresh_token`.
2. The MCP's credentials: the `TONAL_USERNAME` / `TONAL_PASSWORD` environment variables, the MCP's `.env` file, or the `env` you gave the MCP in Claude's config (`claude mcp add … -e`, which ends up in `~/.claude.json`, or Claude Desktop's `claude_desktop_config.json`). The `.env` path is set in `MCP_ENV` near the top of the script; change it if your MCP lives somewhere other than `~/Documents/ts-tonal-mcp`.
3. An email and password prompt, only when run in a terminal.

After a successful sign-in, the refresh token is saved to the Keychain and your password is never stored. To sign out:

```bash
security delete-generic-password -s tonal-export
```

On Windows, delete `%LOCALAPPDATA%\tonal-export\refresh_token` instead.

### Running it nightly

Save this as `~/Library/LaunchAgents/com.example.tonal-export.plist`, replacing the paths (use `which python3` for the first):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>com.example.tonal-export</string>
	<key>ProgramArguments</key>
	<array>
		<string>/path/to/python3</string>
		<string>-I</string>
		<string>/path/to/tonal-export/export_history.py</string>
		<string>/path/to/output-folder</string>
	</array>
	<key>StartCalendarInterval</key>
	<dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
	<key>StandardErrorPath</key><string>/path/to/tonal-export/export.log</string>
</dict>
</plist>
```

Then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.example.tonal-export.plist
```

#### Windows

Create a Task Scheduler task from PowerShell, replacing the three paths at the top:

```powershell
$script = 'C:\path\to\tonal-export\export_history.py'
$out = 'C:\path\to\output-folder'
$log = 'C:\path\to\tonal-export\export.log'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"py -3 -I `"$script`" `"$out`" <NUL >>`"$log`" 2>&1`""
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
$trigger.StartBoundary = (Get-Date 3am).ToString('s')  # local time, so it stays 3 AM across daylight saving
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName tonal-export -Action $action -Trigger $trigger -Settings $settings
```

`<NUL` keeps it from waiting at a login prompt, and the log gets the same lines launchd's `StandardErrorPath` does. `-StartWhenAvailable` runs a missed 3 AM start when the computer wakes, like launchd. The task runs only while you're signed in, and a console window flashes when it starts. Run it now with `Start-ScheduledTask tonal-export`, and remove it with `Unregister-ScheduledTask tonal-export`.

Network errors and Tonal server errors are retried. A workout that still can't be downloaded is skipped and fetched again on the next run. If the download fails, the CSVs are still rebuilt from the cache; if there's no cache yet either (say, a first run that couldn't sign in), the existing CSVs are left untouched. The script exits non-zero, writes a one-line reason to the log and, when run unattended, posts a macOS notification. Windows has no notification, so there failures only show up in `export.log`.

## Output

| File | One row per |
|---|---|
| `tonal_workouts.csv` | workout (activity), with duration, volume, heart rate and calories |
| `tonal_sets.csv` | set: weights, reps, prescribed reps, rest, reps in reserve, power, range of motion, heart rate, modifiers, PRs |
| `tonal_movement_progress.csv` | movement per ISO week: working sets, volume, max weight, best estimated 1RM |
| `tonal_strength_scores.csv` | strength score update (overall, upper, lower, core) |
| `tonal_custom_workouts.csv`, `tonal_custom_sets.csv` | your custom workout templates and their sets |

Sets join workouts on `activity_id`. Weights are in pounds, per handle. Back-to-back activities share a `session_id`, so count sessions rather than rows when measuring training frequency.

`export_custom.py` builds the custom-workout tables. It can also be run on its own against the companion app's cached state; see its docstring.

## Tests

```bash
python3 -I test_export.py
```

## Disclaimer

This project is not affiliated with Tonal. It uses the same private API as the Tonal app, which can change without notice.
