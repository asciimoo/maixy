import sqlite3
from .config import ROOT


def connect():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(str(ROOT / 'state.sqlite3'), timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS events (
      socket TEXT, pane TEXT, status TEXT, updated REAL, acknowledged REAL DEFAULT 0,
      agent TEXT, session TEXT, owner TEXT, PRIMARY KEY(socket,pane))''')
    db.execute('''CREATE TABLE IF NOT EXISTS slots (
      identity TEXT PRIMARY KEY, position INTEGER UNIQUE)''')
    db.execute('''CREATE TABLE IF NOT EXISTS observations (
      socket TEXT, pane TEXT, owner TEXT, status TEXT,
      PRIMARY KEY(socket,pane))''')
    db.execute('''CREATE TABLE IF NOT EXISTS session_events (
      agent TEXT, session TEXT, status TEXT, updated REAL,
      PRIMARY KEY(agent,session))''')
    db.commit()
    return db


def assign_slots(db, panes):
    identities = {p['identity'] for p in panes}
    with db:
        for row in db.execute('SELECT identity FROM slots').fetchall():
            if row['identity'] not in identities:
                db.execute('DELETE FROM slots WHERE identity=?', (row['identity'],))
        slots = {r['identity']: r['position'] for r in db.execute('SELECT * FROM slots')}
        used = set(slots.values())
        for p in panes:
            if p['identity'] not in slots:
                pos = next(i for i in range(len(panes) + len(used) + 1) if i not in used)
                db.execute('INSERT INTO slots VALUES (?,?)', (p['identity'], pos))
                slots[p['identity']] = pos
                used.add(pos)
            p['position'] = slots[p['identity']]
    return sorted(panes, key=lambda p: p['position'])


def pane_status(db, pane):
    row = db.execute('SELECT * FROM events WHERE socket=? AND pane=?',
                     (pane['socket'], pane['pane_id'])).fetchone()
    if not row or row['owner'] != pane['pane_pid'] or row['agent'] != pane['agent']:
        return 'idle'
    if row['status'] == 'done' and row['acknowledged'] >= row['updated']:
        return 'idle'
    return row['status']
