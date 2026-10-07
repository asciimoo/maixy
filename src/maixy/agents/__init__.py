"""Agent registry: built-ins, local JSON definitions, and Python plugins."""
from functools import lru_cache
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import re

from ..config import ROOT
from .base import Agent
from .claude import Claude
from .codex import Codex


def _strings(value, field):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError(field + ' must be an array of nonempty strings')
    return value


def _definition(obj):
    if not isinstance(obj, dict) or set(obj) - {'name', 'executables', 'scripts', 'modules', 'display'}:
        raise ValueError('agent definitions accept name, executables, scripts, modules, and display')
    matchers = {key: _strings(obj.get(key, []), key) for key in ('executables', 'scripts', 'modules')}
    if not any(matchers.values()):
        raise ValueError('an agent needs at least one executable, script, or module matcher')
    if any('/' in name or name != name.lower() for name in matchers['executables']):
        raise ValueError('executables must be lowercase basenames')
    display = obj.get('display', {})
    if not isinstance(display, dict) or set(display) - {'working', 'waiting', 'idle', 'interrupted'}:
        raise ValueError('display accepts working, waiting, idle, and interrupted patterns')
    for status, patterns in display.items():
        if any(not p.startswith('^') for p in _strings(patterns, 'display.' + status)):
            raise ValueError('display patterns must start with ^ to match terminal status lines')
    return Agent(obj.get('name'), display=display, **matchers)


class Registry:
    def __init__(self, adapters):
        self.adapters = {}
        for adapter in adapters:
            if not isinstance(adapter, Agent) or not isinstance(adapter.name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]*', adapter.name):
                raise RuntimeError('Agent adapters must extend Agent and have a lowercase name')
            if adapter.name in self.adapters:
                raise RuntimeError('Duplicate agent adapter: ' + adapter.name)
            self.adapters[adapter.name] = adapter

    def get(self, name):
        return self.adapters.get(name)

    def match(self, executable, script='', module=''):
        matches = [a.name for a in self.adapters.values() if a.matches(executable, script, module)]
        if len(matches) > 1:
            raise RuntimeError('Ambiguous agent process match: ' + ', '.join(matches))
        return matches[0] if matches else None


@lru_cache(maxsize=1)
def registry():
    adapters = [Codex(), Claude(),
                Agent('gemini', ('gemini',), ('*/@google/gemini-cli/*',)),
                Agent('aider', ('aider',), modules=('aider', 'aider.main')),
                Agent('opencode', ('opencode',), ('*/opencode-ai/bin/opencode',))]
    override = os.environ.get('MAIXY_AGENTS_FILE')
    path = Path(override).expanduser() if override else ROOT / 'agents.json'
    try:
        obj = json.loads(path.read_text()) if override or path.exists() else {}
        if not isinstance(obj, dict) or set(obj) - {'agents', 'plugins', 'disabled'}:
            raise ValueError('configuration accepts agents, plugins, and disabled')
        disabled = _strings(obj.get('disabled', []), 'disabled')
        unknown = set(disabled) - {adapter.name for adapter in adapters}
        if unknown:
            raise ValueError('unknown built-in adapters in disabled: ' + ', '.join(sorted(unknown)))
        adapters = [adapter for adapter in adapters if adapter.name not in disabled]
        definitions = obj.get('agents', [])
        if not isinstance(definitions, list):
            raise ValueError('agents must be an array')
        adapters.extend(_definition(item) for item in definitions)
        for spec in _strings(obj.get('plugins', []), 'plugins'):
            module, factory = spec.split(':', 1)
            adapters.append(getattr(importlib.import_module(module), factory)())
        entries = metadata.entry_points()
        plugins = entries.select(group='maixy.agents') if hasattr(entries, 'select') else entries.get('maixy.agents', ())
        for entry in sorted(plugins, key=lambda e: e.name):
            adapters.append(entry.load()())
    except (OSError, ValueError, TypeError, AttributeError, ImportError, re.error) as error:
        raise RuntimeError('Cannot load agent adapters from ' + str(path) + ': ' + str(error)) from error
    return Registry(adapters)


def process_agent(record, read_argv):
    """Match a command or interpreter entrypoint, never arbitrary arguments."""
    executable = Path(record['name']).name.lower()
    agents = registry()
    direct = agents.match(executable)
    if direct:
        return direct
    python = re.fullmatch(r'python(?:\d+(?:\.\d+)?)?', executable)
    if executable not in ('node', 'nodejs', 'bun') and not python:
        return None
    argv = read_argv(record['pid'])
    args = list(argv[1:])
    while args and args[0] in ('-u', '-B', '-E', '-s', '-S', '-I') and python:
        args.pop(0)
    if python and len(args) >= 2 and args[0] == '-m':
        return agents.match('', module=args[1])
    if args and not args[0].startswith('-'):
        return agents.match('', script=args[0])
    return None
