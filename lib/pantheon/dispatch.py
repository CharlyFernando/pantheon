"""Launching the agent whose turn it is, once the human approved the item.

`approve` records the cast and the human's approval in the item header
(`cast`, `approved`). `dispatch` then starts the agent the cast names for the
role in `next`, in the item's worktree, without an interface:

- it refuses an item that is not approved, whose turn is the human's or that
  a live session holds;
- it continues the conversation of the session that played that role with
  that agent and model before, when the agent can resume, so each round keeps
  its context; otherwise it starts a new session;
- the agent gets PANTHEON_SESSION, holds the item, and writes its output to
  `<home>/logs/<session>.log`; the session's pid ties its life to the process.

An agent that is not headless (Antigravity) is never launched: dispatch says
how to join it from its own interface instead.
"""

import os
import subprocess
import uuid

from . import agents, board, sessions, store

MANUAL = "manual"


class Refused(Exception):
    """The item cannot be dispatched now; the message says why."""


def logs_dir():
    path = os.path.join(store.home(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def prompt(slug, item, role, session_id, header, resumed):
    command = f"python3 -I {agents.SKILL_DIR}/pantheon.py"
    lines = [
        f"Eres el {role} del ítem {slug}/{item} en Pantheon, lanzado sin interfaz.",
        f"Tu sesión es {session_id}: ya está en PANTHEON_SESSION y ya tienes el ítem tomado. No hagas join.",
        f"Pantheon se usa como: {command} <comando>.",
        "",
        f"1. Lee tu contrato: {command} role {role}",
        f"2. Lee el ítem completo: {command} show {slug}/{item}",
        f"3. Haz tu turno según el contrato, en el worktree {header.get('worktree', '')}.",
        f"4. Publica el resultado con {command} post {slug}/{item} --session {session_id} … (el flujo decide a quién va).",
        "5. Termina después del post. No ejecutes wait ni inbox: Pantheon te relanzará cuando vuelva a ser tu turno.",
        "",
        "Si algo necesita una decisión del humano o una mutación externa (producción, AWS, Keycloak, GitLab, GitHub), "
        "postea con --next human y explica qué hace falta.",
    ]
    if resumed:
        lines.insert(0, "Es una nueva ronda de un ítem en el que ya trabajaste: lee los hallazgos del último turno.\n")
    return "\n".join(lines)


def previous_session(slug, item, role, agent, model):
    """The latest session that played `role` on the item with this agent and model."""
    matches = [
        r
        for r in sessions.every()
        if r.get("repo") == slug
        and r.get("item") == item
        and r.get("role") == role
        and r.get("agent") == agent
        and r.get("model", "") == model
        and r.get("harness_session")
    ]
    return matches[-1] if matches else None


def plan(slug, item):
    """(role, agent, model, spec, header) for the turn to dispatch, or Refused."""
    header, _ = store.read(slug, item)
    if not header.get("approved"):
        raise Refused(f"{slug}/{item} is not approved: the human runs `approve {slug}/{item} --cast …` first")
    role = header.get("next", "")
    if role == "human":
        raise Refused(f"{slug}/{item} waits on the human")
    cast = agents.parse_cast(header.get("cast", ""))
    if role not in cast:
        raise Refused(f"the cast of {slug}/{item} has no {role}")
    holder = sessions.holder_alive(header)
    if holder:
        raise Refused(f"{slug}/{item} is held by session {holder}")
    agent, model = cast[role]
    spec = agents.definition(agent)
    if not spec.get("enabled", True):
        raise Refused(f"agent {agent} is disabled in the config")
    return role, agent, model, spec, header


def run(slug, item, dry_run=False):
    """Launch the agent of the current turn; return a line describing what happened.

    Returns MANUAL as the first word when a human must join the agent.
    """
    role, agent, model, spec, header = plan(slug, item)
    if not spec.get("headless", True):
        return (
            f"{MANUAL}: {agent} is joined from its own interface. In it, run: "
            f"python3 -I {agents.SKILL_DIR}/pantheon.py join {slug}/{item} --role {role} --agent {agent} --model {model or '<model>'}"
        )
    worktree = header.get("worktree", "")
    if not worktree or not os.path.isdir(worktree):
        raise Refused(f"the worktree of {slug}/{item} does not exist: {worktree or '(none)'}")
    previous = previous_session(slug, item, role, agent, model)
    if previous and previous.get("pid") and sessions.alive(previous):
        raise Refused(f"session {previous['id']} is still running for {role} on {slug}/{item}")
    resumes = bool(previous and spec.get("resume"))
    harness = previous["harness_session"] if resumes else str(uuid.uuid4())
    session_id = previous["id"] if resumes else "<new>"
    template = spec["resume"] if resumes else spec["launch"]
    if not template:
        raise Refused(f"agent {agent} has no launch command in the config")
    if dry_run:
        text = prompt(slug, item, role, session_id, header, resumes)
        command = agents.argv(template, text, harness, model)
        return f"would run in {worktree}: {agents.describe(command)}"

    if resumes:
        record = sessions.touch(previous, state="active", pid=0)
    else:
        record = sessions.create(
            agent, role, repo=slug, item=item, model=model, mode="headless", harness_session=harness, cwd=worktree
        )
    try:
        sessions.hold(slug, item, role, record)
    except PermissionError as e:
        sessions.save({**record, "state": "left"})
        raise Refused(str(e)) from e
    text = prompt(slug, item, role, record["id"], header, resumes)
    command = agents.argv(template, text, harness, model)
    log_path = os.path.join(logs_dir(), f"{record['id']}.log")
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n===== {sessions.stamp()} {board.signature(role, agent, record['id'])} on {slug}/{item}\n")
        log.write(f"$ {agents.describe(command)}\n")
        log.flush()
        try:
            process = subprocess.Popen(
                command,
                cwd=worktree,
                env={**os.environ, "PANTHEON_SESSION": record["id"]},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as e:
            sessions.save({**record, "state": "left"})
            release(slug, item, record["id"])
            raise Refused(f"could not start {agent}: {e}") from e
    sessions.touch(record, pid=process.pid, log=log_path)
    verb = "resumed" if resumes else "launched"
    return f"{verb} {board.signature(role, agent, record['id'])} on {slug}/{item} (pid {process.pid}, log {log_path})"


def release(slug, item, session_id):
    with store.locked(slug):
        header, history = store.read(slug, item)
        if header.get("holder") == session_id:
            header["holder"] = ""
            store.write(slug, item, header, history)
