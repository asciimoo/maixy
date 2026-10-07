# Agent instructions

## Project

Maixy is a local dashboard for Codex and Claude Code agents on the nine LCD keys
of a Logitech MX Keypad. It discovers agents in tmux, standalone terminals, and
local editor terminals, tracks their status, and selects them when a key is
pressed. Support macOS and Linux. Read `README.md` for setup, capabilities, and
known limitations.

The executable is `maixy`. Do not add a `logiai` alias. Legacy state storage and
`LOGIAI_HOME` compatibility are intentional; preserve them when changing paths.

## Code organization

- `src/maixy/discovery.py`, `processes.py`, and `systems/`: live process discovery
  and OS-specific process, file, and terminal access.
- `src/maixy/status.py`, `hooks.py`, and `state.py`: lifecycle detection,
  optional hooks, stable assignments, and completion acknowledgments.
- `src/maixy/navigation.py` and `platforms/`: navigation dispatch and independent
  desktop/window-manager adapters.
- `src/maixy/dashboard.py`, `device.py`, `config.py`, and `render.py`: polling,
  USB protocol, configuration, and key images.
- `editors/vscode/` and `src/maixy/editor.py`: optional local editor bridge.
- `tests/`: Python unit/integration tests and Node bridge tests.

Keep OS process access separate from graphical navigation. New window managers
should use a navigation adapter, custom command, or plugin rather than adding
desktop-specific logic to discovery, status tracking, or USB handling. Preserve
Python 3.9 compatibility and avoid unnecessary dependencies.

## Behavior to preserve

- Track live local agents, not saved conversations. Deduplicate tmux and
  standalone discoveries, exclude internal Codex subagent threads, and protect
  assignments against PID reuse.
- Keep agent key positions stable. Page through additional agents when needed.
- Use lifecycle events for status where available. An idle agent at startup
  must not appear finished. Show `UNKNOWN` when status cannot be determined;
  do not imply that an unreadable terminal or unmatched log means `READY`.
- `WORKING`: light blue (`#79b8ff`) with black text.
- `NEEDS INPUT`: light orange (`#ffbc66`) with black text.
- `FINISHED`: light green (`#7ee2a8`) with black text, retained until successful
  selection acknowledges completion. Failed navigation must preserve green.
- `READY`: pure black (`#000000`) with white text. Empty keys also have a pure
  black background. `UNKNOWN` uses dark gray with white text.
- Show the pane/terminal title and an explicit status label. Preserve bold
  titles/status labels, cross-platform font fallback, and strong text contrast.
- Select exact tmux IDs or the supported terminal target. Report ambiguous or
  unsupported navigation rather than guessing or opening a replacement terminal.
- Keep tracking through USB disconnects and repaint all nine keys on reconnect.

## Local integrations

Navigation selects existing agents; it must not submit prompts, send keystrokes,
or approve agent actions. Keep custom commands as JSON argument arrays executed
without a shell. Do not pass editor bridge credentials to custom navigators or
include credentials/conversation content in diagnostic output.

Keep the editor bridge bound to loopback, authenticated, and limited to terminal
metadata and selection. Preserve existing user settings and hooks when changing
installation logic. Keep tests isolated from real user state and live agents.

## Development and verification

Create a project environment if needed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Run checks appropriate to the change:

```sh
.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
node --test tests/vscode-bridge.test.js
```

Use focused Python tests for localized changes. Run the Node tests when changing
the editor bridge. Documentation-only changes do not require runtime tests.
For Linux-specific changes, use Linux or the test container:

```sh
docker build -f tests/linux/Dockerfile -t maixy-linux-test .
docker run --rm --network none maixy-linux-test
```

For packaging changes, verify the wheel:

```sh
.venv/bin/python -m pip wheel --no-deps . -w dist
```

Rendering changes should check the actual 118×118 JPEG output, text fit,
foreground/background contrast, black empty/ready backgrounds, and font
fallback. When refreshing a running dashboard, use `maixy reload` with the same
state directory so every key is redrawn. Only one instance should control the
keypad and state directory; preserve the exclusive lock across reload. Processes
started before reload support was installed need one graceful restart first.

Distinguish mocked checks from live platform/device verification in reports.
Update the README when behavior or supported integrations change. Retain the
MIT license and keypad protocol attribution in `THIRD-PARTY.txt`.
