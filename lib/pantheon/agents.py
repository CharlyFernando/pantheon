"""How to launch each agent without an interface, and the cast of an item.

An agent is defined by built-in defaults merged with `[agents.<name>]` of the
config. `launch` starts a new conversation and `resume` continues one; both
are argv lists whose items may hold these placeholders:

    {prompt}   the instructions for this turn
    {session}  the agent's own conversation id (a UUID Pantheon chooses)
    {model}    the model of the role, from the cast
    {skill}    the Pantheon skill directory

An argv item that is exactly "{model}" is dropped, with the flag before it,
when the cast names no model. `headless = false` marks an agent that only a
human joins from its own interface; `enabled = false` keeps an agent out of
any cast.

The cast of an item maps each role of its flow to `agent` or `agent:model`,
stored in the header as `cast: coder=claude:claude-opus-5-5 reviewer=gentle`.
"""

import os
import shlex

from . import board, store

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUILTIN = {
    "claude": {
        "enabled": True,
        "headless": True,
        "model": "",
        "launch": ["claude", "-p", "--session-id", "{session}", "--model", "{model}", "{prompt}"],
        "resume": ["claude", "-p", "--resume", "{session}", "--model", "{model}", "{prompt}"],
    },
    "gentle": {
        "enabled": True,
        "headless": True,
        "model": "",
        "launch": ["gentle-shell", "--", "-p", "--session-id", "{session}", "--model", "{model}", "--skill", "{skill}", "{prompt}"],
        "resume": ["gentle-shell", "--", "-p", "--session-id", "{session}", "--model", "{model}", "--skill", "{skill}", "{prompt}"],
    },
    "opencode": {
        "enabled": True,
        "headless": True,
        "model": "",
        "launch": ["opencode", "run", "--session", "{session}", "--model", "{model}", "{prompt}"],
        "resume": ["opencode", "run", "--session", "{session}", "--model", "{model}", "{prompt}"],
    },
    "codex": {
        "enabled": False,
        "headless": True,
        "model": "",
        "launch": ["codex", "exec", "--model", "{model}", "{prompt}"],
        "resume": [],
    },
    "antigravity": {"enabled": True, "headless": False, "model": "", "launch": [], "resume": []},
}


def definitions():
    """Every agent: built-in defaults updated by the config."""
    configured = store.load_config().get("agents", {})
    merged = {}
    for name in dict.fromkeys(list(BUILTIN) + list(configured)):
        merged[name] = {**{"enabled": True, "headless": True, "model": "", "launch": [], "resume": []}, **BUILTIN.get(name, {}), **configured.get(name, {})}
    return merged


def definition(name):
    agents = definitions()
    if name not in agents:
        raise ValueError(f"unknown agent {name!r}; agents are {', '.join(agents)}")
    return agents[name]


def parse_cast(value):
    """{role: (agent, model)} from a header value or a list of `role=agent[:model]`."""
    tokens = value.split() if isinstance(value, str) else list(value or [])
    cast = {}
    for token in tokens:
        role, sep, who = token.partition("=")
        if not sep or not who:
            raise ValueError(f"cast entries are role=agent[:model], not {token!r}")
        agent, _, model = who.partition(":")
        cast[role.strip()] = (agent.strip(), model.strip())
    return cast


def format_cast(cast):
    return " ".join(f"{role}={agent}:{model}" if model else f"{role}={agent}" for role, (agent, model) in cast.items())


def default_cast():
    return {role: (agent, "") for role, agent in store.load_config().get("defaults", {}).get("cast", {}).items()}


def complete_cast(flow, given):
    """The cast for every role of `flow`: `given`, then the config defaults, then each agent's model.

    Refuses an unknown or disabled agent, a role outside the flow, a role
    with no agent, and a reviewer or QA running the coder's model.
    """
    defaults = default_cast()
    stray = [r for r in given if r not in flow]
    if stray:
        raise ValueError(f"the flow is {','.join(flow)}; it has no {', '.join(stray)}")
    cast = {}
    for role in flow:
        agent, model = given.get(role) or defaults.get(role) or ("", "")
        if not agent:
            raise ValueError(f"no agent for {role}: add --cast {role}=<agent>[:model]")
        spec = definition(agent)
        if not spec.get("enabled", True):
            raise ValueError(f"agent {agent} is disabled in the config")
        cast[role] = (agent, model or spec.get("model", ""))
    producer = cast.get(flow[0], ("", ""))[1]
    for role in board.CHECKERS:
        if role in cast and board.same_model(cast[role][1], producer):
            raise ValueError(f"{role} and {flow[0]} both run {producer}; a {role} runs another model")
    return cast


def argv(template, prompt, session, model, skill=SKILL_DIR):
    """The command line of `template` with its placeholders filled."""
    values = {"prompt": prompt, "session": session, "model": model, "skill": skill}
    filled = []
    for part in template:
        if part == "{model}" and not model:
            if filled and filled[-1].startswith("-"):
                filled.pop()
            continue
        filled.append(part.format(**values))
    return filled


def describe(command):
    """The command line for a log, with the prompt shortened."""
    return " ".join(shlex.quote(p if len(p) < 80 else p[:77] + "...") for p in command)
