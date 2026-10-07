# Maixy

A physical dashboard for **local coding agents**, using the nine LCD keys on a Logitech MX Keypad. Agents can run in tmux, individual terminal windows, or editor terminals. Maixy discovers live processes rather than listing saved conversations.

Each key shows the agent's pane or terminal title, host/window name, and status. Active background subagents appear as a count in the parent's header (`+3` means three active children), rather than separate keys. Press a key to select the agent and acknowledge its completion.

| Background | Label | Meaning |
| --- | --- | --- |
| Light blue | `WORKING` | A prompt is in progress. |
| Light orange | `NEEDS INPUT` | The agent needs user input or approval. |
| Light green | `FINISHED` | The current turn finished and has not been acknowledged. |
| Pure black | `READY` | The agent is idle or its completion has been acknowledged. |
| Dark gray | `UNKNOWN` | The process is live, but its status cannot be resolved. |

Colored keys use black lettering, with calculated sRGB contrast ratios of 10.1:1 (blue), 12.6:1 (orange), and 13.3:1 (green). READY and UNKNOWN use white lettering; empty keys have a pure black background. Titles and status labels use bold text when a system bold font is available. Explicit status labels make the states readable without relying on color alone.

Green stays until successful navigation. Failed selection leaves the key green. Agents keep their key positions while they remain open. The two bottom buttons page through additional agents when more than nine are running.

## Requirements

- macOS or Linux with Python 3.9 or newer.
- Linux: `ps` from procps, a mounted `/proc`, and access to your own agent processes.
- A Logitech MX Keypad or MX Creative Keypad over USB (`046d:c354`).
- A supported local agent CLI (Codex, Claude Code, Gemini CLI, Aider, OpenCode), or a custom agent adapter.
- tmux on `PATH` and an attached client when navigating tmux agents. Standalone discovery works without tmux.

Maixy talks directly to the keypad; Logitech Options+ is not required. Avoid another application controlling the keypad at the same time. Linux needs access to the device's hidraw nodes and the system HID/libusb libraries required by `hidapi`. A typical desktop udev rule is:

```udev
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", ATTRS{idProduct}=="c354", TAG+="uaccess"
```

An administrator can install this in `/etc/udev/rules.d/70-maixy.rules`, reload udev rules, and reconnect the keypad. Permissions depend on the distribution; Maixy itself should run as your user.

On Debian/Ubuntu, install the runtime tools before running the installer:

```sh
sudo apt-get install python3 python3-venv procps libusb-1.0-0 fonts-dejavu-core
sudo apt-get install tmux       # when tracking tmux agents
sudo apt-get install wmctrl     # when navigating X11 windows
```

Sway needs `swaymsg`; Hyprland needs `hyprctl`, normally supplied by the compositor. Font rendering uses a system font when available and Pillow's bundled font otherwise.

## Install

From this checkout:

```sh
./scripts/install
~/bin/maixy
```

The installer creates an isolated Python environment, installs the project in editable mode, and adds **only `maixy`** to `~/bin`. Existing launchers are backed up before replacement. An old `logiai` compatibility launcher created by Maixy is removed. Ensure `~/bin` is on your `PATH`, or use the absolute command above.

Choose the Python interpreter with `PYTHON=/path/to/python3 ./scripts/install`, or install manually:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/maixy
```

Stop the dashboard with **Ctrl-C**. Only one instance can use a state directory at a time.

### Automatic startup on macOS

To run independently of a terminal, start at login, and restart automatically
if the dashboard exits:

```sh
# Stop an existing manually started dashboard first (Ctrl-C).
maixy install-autostart
```

This installs and starts a user LaunchAgent at
`~/Library/LaunchAgents/local.maixy.dashboard.plist`. It uses the current Python
environment and state directory, captures the selected tmux socket and any
supplied `--session`, `--client`, `--interval`, `--no-focus`, and `--navigator`
options, and preserves supported Maixy configuration variables. Shell-specific
variables and editor credentials are not copied into the service. Re-run the
command to change its startup options; a managed instance stops before its
replacement starts. An existing manual instance must be stopped first.

The LaunchAgent follows Apple's
[user-agent lifecycle](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).
It runs while you are logged in and is suspended with the computer during sleep.
USB keepalive writes run independently of discovery, terminal inspection, and
navigation. After a polling pause of five seconds or more, Maixy reopens the USB
connection and redraws all nine keys, even if stale HID handles did not report an error.
Assignments and completion acknowledgments remain in the same database.
This recovery also applies to manually started dashboards on macOS and Linux.

Logs are written to `dashboard.log` in your state directory. `maixy reload`
still reloads the managed instance while retaining its lock. Remove automatic
startup and stop the managed instance with:

```sh
maixy uninstall-autostart
```

Removing the LaunchAgent retains the state database and logs. Automatic service
installation currently supports macOS; Linux dashboards can be supervised by
your user service manager.

## Usage

```sh
maixy                         # dashboard: all supported local agents
maixy reload                  # reload installed code in the running dashboard
maixy install-autostart        # macOS: run at login and restart if the dashboard exits
maixy uninstall-autostart      # macOS: stop and remove the managed service
maixy list                    # assignments and current detectable statuses
maixy agents                  # registered agent adapters and status capabilities
maixy doctor                  # agents, USB interfaces, and setup
maixy jump 1                  # select the agent assigned to key 1
maixy --version
```

Inside tmux, Maixy inherits its server socket. Otherwise it uses the default server, alongside standalone discovery. The most recently active attached client is selected unless `--client` is supplied.

```sh
maixy --session work           # restrict discovery to this tmux session
maixy --socket /path/to/tmux.sock
maixy --client /dev/ttys000
maixy --no-focus               # select tmux panes without raising their host
maixy --interval 2
maixy --navigator sway         # explicit desktop backend
```

`--interval` controls discovery in seconds; status is polled every 250 ms. `jump` uses a one-based assignment number: `jump 10` selects the first agent on the second page. An explicitly supplied `--session` excludes standalone agents.

To set a tmux pane title:

```sh
tmux select-pane -t %43 -T 'Backend migration'
```

Agents may update their terminal titles during work; Maixy follows those changes. Standalone processes use the terminal title or session name when available, otherwise their working directory name.

### Reloading after an update

After editing this checkout (editable installations), or installing an updated
package into the dashboard's virtual environment, run `maixy reload` with the
same state directory (`MAIXY_HOME`) as the dashboard. It reloads installed code
using the same process ID, interpreter, startup arguments, and environment.
The exclusive instance lock stays held during reload. Assignments, status, and
completion acknowledgments persist in SQLite; the visible page resets to the
first page. The keypad briefly reconnects and all nine keys are redrawn.

Reload does not download updates or refresh the dashboard's environment from the
calling shell. Update dependencies before requesting reload if they changed.
`SIGHUP` to the dashboard process also requests a reload. A dashboard started
before reload support was installed needs one normal restart first. The command
reports that a reload was requested; completion is logged by the dashboard.

## Navigation support

Process inspection lives in separate macOS/Linux adapters under `src/maixy/systems/`. Graphical navigation lives in independently selectable adapters under `src/maixy/platforms/`. Discovery, native status events, key rendering, USB handling, and tmux pane selection do not depend on a particular window manager.

| Host | Selection |
| --- | --- |
| tmux | Exact session, window, and pane via stable IDs; OS backend raises the client window. |
| macOS Terminal | Exact tab by TTY, including standalone terminals. |
| iTerm2 | Exact session by TTY, including split panes. |
| VS Code / Insiders / Cursor integrated terminals | Exact terminal using the optional local editor bridge below. |
| Linux X11 | Existing window by process ancestry using `wmctrl`. |
| Sway | Existing container by process ancestry using `swaymsg`. |
| Hyprland | Existing window by process ancestry using `hyprctl`. |
| Other terminals, editors, or window managers | Custom navigation command. |

Linux window focus selects a window; it cannot select an arbitrary tab inside every terminal emulator. If several windows match, Maixy requires a custom navigator rather than guessing. Native editor sidebar agents can be tracked when their live process/session can be resolved, but selecting a particular sidebar conversation requires an editor-specific custom navigator. VS Code's bridge selects integrated terminals, not other extensions' private chat views. A process that cannot be navigated remains visible, and pressing its key reports the missing integration.

macOS may request Automation permission for Terminal/iTerm2. Raising a specific editor window also uses System Events and may require Accessibility permission. These permissions are only needed for the corresponding navigation backend.

The default `auto` navigator selects macOS, Sway (`SWAYSOCK`), Hyprland (`HYPRLAND_INSTANCE_SIGNATURE`), or X11 (`DISPLAY`). Unknown Wayland desktops use `headless` rather than treating XWayland's `DISPLAY` as access to native windows. Explicit selection takes precedence:

```sh
maixy --navigator x11
maixy --navigator sway
maixy --navigator hyprland
maixy --navigator headless --no-focus  # tmux navigation without a desktop
MAIXY_NAVIGATOR=sway maixy
maixy doctor                          # reports the chosen backend
```

Available built-ins are `macos`, `x11`, `sway`, `hyprland`, `custom`, and `headless`. Missing desktop tools produce an error when navigation is requested; discovery and status tracking continue. For SSH/headless sessions, `--no-focus` still selects the exact tmux pane in an attached client. Standalone terminal windows require a graphical or custom navigator.

### VS Code terminal bridge

The bundled extension reports terminal names and shell PIDs so Maixy can find the exact terminal even across editor windows. Install it locally:

```sh
./scripts/install-vscode
# Reload your VS Code windows afterwards.
```

For another compatible editor:

```sh
./scripts/install-vscode --extensions-dir ~/.vscode-insiders/extensions
./scripts/install-vscode --extensions-dir ~/.cursor/extensions
```

The bridge listens only on `127.0.0.1`, authenticates with a random token stored in a user-private descriptor, and exposes terminal metadata and terminal selection. It does not send keystrokes, submit prompts, approve actions, or transmit conversations. Close/reload editor windows to remove or refresh their bridge. Remote SSH/container agents are outside the local process namespace and are not supported by this local bridge.

The bridge uses the public [VS Code terminal API](https://code.visualstudio.com/api/references/vscode-api#Terminal).

### Other window managers and terminals

Navigation is separate from process discovery, status tracking, and USB handling. Built-in adapters live under `src/maixy/platforms/`. To supply your own adapter without editing Maixy:

```sh
export MAIXY_NAVIGATION_COMMAND='["/home/me/bin/focus-agent"]'
maixy
```

This is a JSON array of executable and arguments, invoked without a shell. The command receives agent metadata as JSON on stdin: `kind`, `agent`, `agent_pid`, `pane_tty`, `pane_current_path`, `pane_title`, `host`, `host_pid`, `host_chain`, and `foreground` where available; tmux targets also include their IDs. Editor bridge credentials are excluded. Exit zero only after successful selection; a nonzero exit preserves green and displays stderr. The timeout is five seconds. tmux pane selection happens before the OS adapter is called.

For an in-process Python adapter, implement a factory or class with no constructor arguments and a synchronous method:

```python
class Navigator:
    def focus(self, pane, foreground=True):
        # Use your window manager's API to select the existing target.
        # Return None after successful selection; raise RuntimeError on failure.
        ...
```

Load it from an importable Python module without changing Maixy:

```sh
PYTHONPATH=/path/to/my/adapters maixy --navigator my_desktop:Navigator
```

To distribute an adapter as a package, register its factory in that package's `pyproject.toml`:

```toml
[project.entry-points."maixy.navigators"]
my-desktop = "my_desktop:Navigator"
```

Install the adapter into Maixy's virtual environment, then select it with `maixy --navigator my-desktop` or `MAIXY_NAVIGATOR=my-desktop`. Each desktop backend is responsible for window matching and focus; it receives the same target metadata as the custom command. OS adapters expose `inventory`, `argv`, `files`, `terminal_tabs`, and `screen` so another OS can be added separately from graphical navigation. The installed adapter runs with Maixy's user privileges.

## Agent support

Agent recognition, session matching, terminal status parsing, and lifecycle
parsing live in independent adapters under `src/maixy/agents/`. Navigation and
USB handling work with the same target metadata for every agent.

| Adapter | Live process recognition | Status support |
| --- | --- | --- |
| `codex` | Codex executable or its Node package; live root app-server threads | Native lifecycle logs, terminal display, optional hooks |
| `claude` | Claude/Claude Code executable or its Node package | Native lifecycle logs, terminal display, optional hooks |
| `gemini` | Gemini executable or `@google/gemini-cli` Node entrypoint | Detection only; `UNKNOWN` until status support is supplied |
| `aider` | Aider executable, Python script, or `python -m aider` / `aider.main` | Detection only; `UNKNOWN` until status support is supplied |
| `opencode` | OpenCode executable or `opencode-ai` entrypoint | Detection only; `UNKNOWN` until status support is supplied |

The extra CLI profiles recognize the commands published by
[Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/package.json),
[Aider](https://github.com/Aider-AI/aider/blob/main/pyproject.toml), and
[OpenCode](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/package.json).
They track terminal sessions, including editor terminals exposed by the bridge;
bare servers and child workers do not create additional keys. Detection does
not install an agent or start a replacement terminal. Status support for these
profiles requires an adapter or lifecycle hooks; Maixy does not infer readiness
from arbitrary conversation text.

### Add an agent with JSON

Place `agents.json` in your Maixy state directory (`MAIXY_HOME`, with the legacy
fallback described below), or select another file with `MAIXY_AGENTS_FILE`:

```json
{
  "agents": [
    {
      "name": "my-agent",
      "executables": ["my-agent", "my-agent-cli"],
      "scripts": ["*/node_modules/my-agent-package/cli.js"],
      "modules": ["my_agent"],
      "display": {
        "working": ["^Busy \\(esc to stop\\)$"],
        "waiting": ["^Approve action\\?$"],
        "idle": ["^My Agent ready>$"]
      }
    }
  ]
}
```

Only `name` and at least one matcher are required; omit `display` for detection
only. Names are lowercase identifiers, and `executables` are exact lowercase
basenames. `scripts` uses case-sensitive shell-style path globs, matched only
against the entrypoint of Node, Bun, or Python. `modules` matches Python's `-m`
module exactly. Prompt arguments, process environments, and saved conversations
are not used to identify agents.

Display patterns are case-insensitive regular expressions that must start with
`^`. They match stripped lines in the last 18 terminal lines; use precise footer
patterns from the agent's actual UI. Supported signals are `working`, `waiting`,
`interrupted`, and `idle`, in that precedence order. An idle screen at startup
stays ready. After activity, an idle screen must remain visible for 1.5 seconds
before completion turns green. An unrecognized or unreadable screen does not
imply readiness. Linux terminals and editor terminals often have no readable
screen, so use a Python adapter for native status there.

Malformed configuration, duplicate adapter names, and ambiguous process matches
report errors. Configuration is loaded once per invocation; run `maixy reload`
after changing it for a running dashboard. An explicit missing
`MAIXY_AGENTS_FILE` is an error. The default file is optional.

Use `"disabled": ["gemini"]` to exclude a built-in profile. To add status parsing
or a native plugin for an existing profile, disable that built-in and register
your replacement under the same name. Codex and Claude's native behavior remains
the default unless explicitly disabled.

### Add native status with a Python adapter

Subclass the public `maixy.agents.Agent` contract:

```python
from maixy.agents import Agent

class MyAgent(Agent):
    def __init__(self):
        super().__init__("my-agent", executables=("my-agent",))

    def discover(self, pane, record, files, context):
        # Use live process/session metadata to attach session_path/session_id.
        # files contains cwd and JSONL paths held open by this live process.
        return super().discover(pane, record, files, context)

    def event_status(self, obj):
        # Translate this agent's lifecycle records; ignore conversation text.
        return {"turn_started": "working", "turn_finished": "done"}.get(obj.get("type"))
```

The example illustrates the interface; implement session matching for the
agent's format before native events can be read. `resolve(pane)` returns the
matched JSONL path, or uses `pane['session_path']` by default. Records use an ISO
`timestamp`; `event_status(obj)` returns a lifecycle signal or `None`. Shared
incremental log handling establishes a ready baseline for old completions,
tracks new turns, and preserves acknowledgments.

`prepare()` creates context for one discovery pass. `enrich(pane, context)`
attaches metadata to tmux targets. `discover(pane, record, files, context)`
returns zero or more standalone targets for a live process; overrides must
exclude internal subagents and avoid listing stored sessions without evidence
that they are live. `display_status(text, title)` handles agent-specific terminal
chrome. `session_id(pane)` links optional session hooks (the base implementation
uses a standalone target's `session_id`). `hook_status(event, payload)` translates
hook events; `hook_path()` and `hook_events` optionally enable the existing JSON
hook installer. The installer expects Codex/Claude-style settings structure and
preserves unrelated settings and hooks.

Load an importable factory/class from the same configuration file:

```json
{"plugins": ["my_agents:MyAgent"]}
```

Or register it in an installed package:

```toml
[project.entry-points."maixy.agents"]
my-agent = "my_agents:MyAgent"
```

Factories take no arguments and return an `Agent` instance. Install packages
into Maixy's environment, or use `PYTHONPATH` for local modules. Installed
`maixy.agents` entry points load automatically. Python adapters run with Maixy's
user privileges and should leave OS access to the process adapters and target
selection to the navigation adapters.

Any registered agent can emit standard lifecycle events through
`maixy hook my-agent UserPromptSubmit`, `PermissionRequest`, or `Stop`, with a JSON
payload on stdin. The default translator also accepts the other Codex/Claude
lifecycle events. A matching tmux process identifies the owner; standalone hooks
need a `session_id` that the adapter resolves. Hooks never submit prompts or
approve actions. `install-hooks` configures only adapters with a hook settings
path, which are Codex and Claude by default.

## Status tracking

Codex and Claude adapters read local lifecycle events without requiring hooks or restarting agents:

- Claude sessions are matched using their live process and session IDs.
- Claude local commands such as `/model` return to `READY`; they do not count as assistant turns or produce `FINISHED`.
- Claude's `AskUserQuestion` calls show `NEEDS INPUT` until the recorded answer returns, including hosts without readable terminal screens.
- A parent's key stays `WORKING` after its prompt returns while linked Codex or Claude subagents are still working. Child sessions do not occupy keys. The parent finishes when its turn and all tracked children have finished; explicit input waits still show `NEEDS INPUT`.
- Claude child completion includes explicit turn-ending tool results such as `SubagentHandback`. Reloading recovers newer completions for sessions Maixy had already tracked as active.
- Claude assistant activity and tool results return the key to `WORKING`, including automatic continuation after a background task without a new user prompt. Child completion cannot finish a parent that has resumed work.
- Codex threads are matched by a unique thread name and working directory, or by session logs actually held open by a live Codex process. Internal subagent threads and archived/saved history are excluded.
- Background `codex exec` sessions do not occupy keys, including detached workers whose original parent process has exited.
- Agents already found in tmux are not added again as standalone processes. Process start times protect external assignments from PID reuse.
- tmux, macOS Terminal, and iTerm2 screens supplement lifecycle events with approval/input detection. Unmatched sessions use their terminal display when available; a ready screen must remain visible for 1.5 seconds after activity before that fallback marks completion.
- Codex screen-based input waits must persist for 0.75 seconds before turning orange, avoiding flashes during redraws or automatic review. Lifecycle hooks report waits directly.
- Hosts without a readable screen use native logs for working/completed states and Claude question dialogs. **Approval waits require optional hooks in these hosts**, because approvals are not reliably present in the session log. The editor bridge handles navigation, not status extraction.
- When neither session data nor a supported screen is available, Maixy shows `UNKNOWN` instead of claiming the agent is ready.

Green means **the agent finished its turn**, not that every requested task succeeded. An already idle agent does not become green merely because the dashboard first discovers it. Local session formats and terminal UI may change between agent releases.

### Optional hooks

Hooks supplement detection, including user-input waits outside tmux:

```sh
maixy install-hooks
maixy uninstall-hooks
```

Installation preserves existing hooks and backs up settings. Optional Codex hooks need review and trust through `/hooks`; existing Claude sessions may need restarting/resuming to load new hooks. Session IDs associate events with the correct agent even without `TMUX_PANE`. Hooks do not submit prompts or approve actions.

## USB disconnects

Leave Maixy running when the keypad is unplugged. It retries the connection, continues tracking agents, and redraws all nine keys when the keypad reconnects. Assignments and completion acknowledgments are retained. Software reconnection is covered by simulated-device tests; a physical unplug/replug has not yet been verified.

The keypad receives a keepalive every second on a separate thread, so slow
discovery or terminal navigation does not let its three-second timeout expire.
If that deadline is missed, Maixy redraws all nine keys. It also refreshes the
entire visible page every 15 seconds to recover from silent firmware resets
(such as the pulsing Logi logo) without waiting for each agent's status to change.
These redraws preserve assignments and completion acknowledgments.

## Local state

New installations store state, the installer's virtual environment, and backups in `~/.local/share/maixy`. Existing installations with a standalone logiai state database reuse `~/.local/share/logiai`, preserving assignments and acknowledgments. This storage compatibility does not install a `logiai` executable.

Override with `MAIXY_HOME`. The older `LOGIAI_HOME` remains supported; `MAIXY_HOME` takes precedence. The installer accepts `MAIXY_BIN_DIR` for the launcher directory. Configure the same state directory in the editor environment when using the bridge.

```sh
MAIXY_HOME="$HOME/.local/share/maixy-test" maixy
```

Maixy stores assignment/status metadata locally. It does not copy conversation content into its database or send it to an external service. Installation downloads dependencies; operation uses local processes, files, terminal automation, the optional loopback editor bridge, tmux, and USB.

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
node --test tests/vscode-bridge.test.js
.venv/bin/python -m pip wheel --no-deps . -w dist
```

Tests cover discovery across hosts, daemon/subagent deduplication, process reuse, native status transitions, input waits, completion acknowledgments, exact editor terminal selection, bridge authentication, OS navigation, stable assignments, USB packets/reconnection, and rendered colors. Tests use temporary state, simulated devices, and loopback test servers. Linux process/status tracking, tmux, rendering, and X11 window focus are verified in a Linux container. Sway and Hyprland are covered by protocol mocks; physical Linux USB access and those compositors have not been exercised live.

Run the Linux suite in its reusable container:

```sh
docker build -f tests/linux/Dockerfile -t maixy-linux-test .
docker run --rm --network none maixy-linux-test
```

This exercises live Linux `/proc` inspection, a standalone agent stub in a real PTY, lifecycle transitions, a real tmux server, and X11 window selection using Xvfb/Openbox. It sends no AI prompts and needs no USB device. CI runs the Python suite on Linux/macOS with Python 3.9 and 3.13, and checks the editor bridge separately.

```text
src/maixy/
  agents/        agent recognition, session matching, status, and plugins
  autostart.py   optional macOS user LaunchAgent installation
  cli.py         commands and argument handling
  config.py      state location and colors
  dashboard.py   discovery, status polling, and redraw loop
  device.py      USB HID protocol and button reports
  discovery.py   live agents across tmux, terminals, and editors
  editor.py      optional authenticated editor bridge client
  hooks.py       optional agent lifecycle hooks
  hosts.py       host identification and terminal screen access
  navigation.py  navigation dispatcher
  platforms/     independent macOS, X11, Sway, Hyprland, headless,
                 and custom navigation adapters
  processes.py   OS process inventory and session file ownership
  render.py      key images and labels
  runtime.py     dependency checks
  state.py       persistent assignments and acknowledgments
  status.py      lifecycle events and terminal fallback
  systems/       OS process/file/terminal adapters for Linux and macOS
  tmux.py        pane enumeration and stable-ID selection
editors/vscode/  local integrated-terminal bridge
```

## License

Maixy is released under the [MIT license](LICENSE), copyright Adam Tauber. The keypad image packet generator includes code adapted from Julian Waller's MIT-licensed implementation; its attribution and license are retained in [THIRD-PARTY.txt](THIRD-PARTY.txt).

This is an independent project and is not an official Logitech integration.
