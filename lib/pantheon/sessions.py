"""Sessions: an agent playing a role, registered in `<home>/sessions/<id>.json`.

A session is alive while it has not left and its heartbeat is younger than
the lease (`[sessions] lease_minutes` in the config, 30 by default) and, when
it recorded a pid, that process still runs. Every command that names a
session refreshes its heartbeat, and so does `wait` or `inbox` while it
blocks, so only a session that stopped working loses its items.

An item's `holder` is the session working on it. The hold lasts while the
holder is alive; a dead holder's items are free to claim again.
"""

import datetime
import json
import os
import secrets

from . import store

AGENTS = ("claude", "gentle", "opencode", "codex", "antigravity")
MODES = ("interactive", "headless")
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def directory():
    path = os.path.join(store.home(), "sessions")
    os.makedirs(path, exist_ok=True)
    return path


def known_agents():
    """The built-in agents plus any `[agents.<name>]` of the config."""
    return tuple(dict.fromkeys(AGENTS + tuple(store.load_config().get("agents", {}))))


def lease_seconds():
    minutes = store.load_config().get("sessions", {}).get("lease_minutes", 30)
    return float(minutes) * 60


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(moment=None):
    return (moment or utcnow()).strftime(STAMP)


def parse_stamp(value):
    try:
        return datetime.datetime.strptime(value, STAMP).replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def path_of(session_id):
    return os.path.join(directory(), f"{store.check_name('session', session_id)}.json")


def load(session_id):
    """The session record, or None when there is no such session."""
    try:
        with open(path_of(session_id), encoding="utf-8") as source:
            return json.load(source)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save(record):
    store.write_text(path_of(record["id"]), json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def new_id():
    while True:
        candidate = f"s-{secrets.token_hex(3)}"
        if not os.path.exists(path_of(candidate)):
            return candidate


def create(agent, role, repo=None, item=None, model=None, mode="interactive", harness_session=None, pid=None, cwd=None):
    if agent not in known_agents():
        raise ValueError(f"agents are {', '.join(known_agents())}")
    if mode not in MODES:
        raise ValueError(f"modes are {', '.join(MODES)}")
    now = stamp()
    record = {
        "id": new_id(),
        "agent": agent,
        "model": model or "",
        "role": role,
        "repo": repo or "",
        "item": item or "",
        "cwd": cwd or os.getcwd(),
        "mode": mode,
        "harness_session": harness_session or "",
        "pid": pid or 0,
        "state": "active",
        "started": now,
        "heartbeat": now,
    }
    save(record)
    return record


def pid_running(pid):
    if not pid:
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def alive(record, now=None):
    if not record or record.get("state") != "active":
        return False
    beat = parse_stamp(record.get("heartbeat"))
    if beat is None:
        return False
    age = ((now or utcnow()) - beat).total_seconds()
    return age < lease_seconds() and pid_running(record.get("pid"))


def status(record, now=None):
    """`alive`, `stale` (stopped beating) or `left`."""
    if record.get("state") == "left":
        return "left"
    return "alive" if alive(record, now) else "stale"


def touch(record, **changes):
    """Save the record with a fresh heartbeat and the given changes."""
    record = {**record, **changes, "heartbeat": stamp()}
    save(record)
    return record


def require(session_id):
    """The live record of `session_id`, refreshed, or ValueError."""
    record = load(session_id)
    if record is None:
        raise ValueError(f"no session {session_id}")
    if record.get("state") == "left":
        raise ValueError(f"session {session_id} has left; join again")
    return touch(record)


def holder_alive(header, now=None):
    """The id of the live session holding the item, or ""."""
    holder = header.get("holder", "")
    if not holder:
        return ""
    try:
        record = load(holder)
    except ValueError:
        return ""
    return holder if alive(record, now) else ""


def every():
    """Every session record, oldest first."""
    found = []
    for name in sorted(os.listdir(directory())):
        if name.endswith(".json") and not name.startswith("."):
            record = load(name[: -len(".json")])
            if record:
                found.append(record)
    return sorted(found, key=lambda r: r.get("started", ""))
