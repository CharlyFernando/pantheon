"""The turn rules of an item, with no file access.

An item follows a flow: the roles that act on it in order, after which the
turn goes to the human. A change defaults to `coder,reviewer,qa` and a plan
to `planner,reviewer`. The first role of the flow produces the work; a review
that asks for changes (`CHANGES_REQUESTED`, `FAILED`) gives the turn back to
it, `NEEDS_DECISION` gives it to the human, and anything else moves it to the
next role of the flow. An agent may always escalate to the human instead;
the human may hand the turn to anyone.

Only the role named in `next` may post, except the human, who may always
post. A review that asks for changes hands the turn to the human instead when
the round limit is reached or when it repeats the previous findings. A plan is
approved only by the human; approving it records `approved_version` and leaves
the turn with the human. The reviewer and the QA of an item run a model other
than the one its coder ran.
"""

import datetime

ROLES = ("human", "orchestrator", "planner", "coder", "reviewer", "qa")
FLOW_ROLES = ("planner", "coder", "reviewer", "qa")
PRODUCERS = ("planner", "coder")
CHECKERS = ("reviewer", "qa")
PLAN_WRITERS = ("planner", "coder", "human")
DEFAULT_FLOWS = {"change": ("coder", "reviewer", "qa"), "plan": ("planner", "reviewer")}
ROLE_VERDICTS = {
    "reviewer": ("APPROVED", "CHANGES_REQUESTED", "NEEDS_DECISION"),
    "qa": ("PASSED", "FAILED", "NEEDS_DECISION"),
    "human": ("APPROVED", "CHANGES_REQUESTED", "NEEDS_DECISION", "PASSED", "FAILED"),
}
VERDICTS = ROLE_VERDICTS["human"]
SENDS_BACK = ("CHANGES_REQUESTED", "FAILED")


def now_stamp(now=None):
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_flow(value, kind="change"):
    """The flow as a tuple of roles, validated; "" or None gives the kind's default."""
    if not value:
        return DEFAULT_FLOWS[kind]
    flow = tuple(r.strip() for r in value.split(",") if r.strip())
    unknown = [r for r in flow if r not in FLOW_ROLES]
    if unknown:
        raise ValueError(f"flows use {', '.join(FLOW_ROLES)}, not {', '.join(unknown)}")
    if len(set(flow)) != len(flow):
        raise ValueError("a flow names each role once")
    if not flow or flow[0] not in PRODUCERS:
        raise ValueError(f"a flow starts with {' or '.join(PRODUCERS)}")
    if any(r in PRODUCERS for r in flow[1:]):
        raise ValueError("a flow has one producer, first")
    return flow


def flow_of(header):
    return parse_flow(header.get("flow"), header.get("kind", "change"))


def new_item(item, repo, title, max_rounds, now=None, flow=None, kind="change", **fields):
    """The header of an item at turn 0, with the first role of its flow to act."""
    roles = parse_flow(flow, kind)
    stamp = now_stamp(now)
    header = {
        "item": item,
        "repo": repo,
        "kind": kind,
        "title": title or "",
        "flow": ",".join(roles),
        "turn": "0",
        "next": roles[0],
        "round": "1",
        "max_rounds": str(max_rounds),
        "verdict": "",
        "findings": "",
        "reason": "",
        "created": stamp,
        "updated": stamp,
    }
    header.update({k: "" if v is None else str(v) for k, v in fields.items()})
    return header


def new_plan(item, repo, title, max_rounds, now=None, flow=None):
    header = new_item(item, repo, title, max_rounds, now, flow=flow, kind="plan")
    header.update({"plan_version": "0", "approved_version": ""})
    return header


def plan_approved(header):
    version = header.get("plan_version", "0")
    return version != "0" and header.get("approved_version") == version


def findings_of(value):
    return sorted({f.strip() for f in (value or "").split(",") if f.strip()})


def same_model(a, b):
    """Whether two model names are the same model, ignoring provider prefix and thinking level."""

    def bare(name):
        return (name or "").strip().lower().rsplit("/", 1)[-1].split(":", 1)[0]

    return bool(bare(a)) and bare(a) == bare(b)


def flow_next(header, role, verdict):
    """The role the flow hands the turn to after `role` posts `verdict`."""
    flow = flow_of(header)
    if verdict == "NEEDS_DECISION":
        return "human"
    if verdict in SENDS_BACK:
        return flow[0]
    if role in flow:
        index = flow.index(role)
        return flow[index + 1] if index + 1 < len(flow) else "human"
    return "human"


def next_role(header, role, wanted, verdict, findings):
    """The role that posts next, and why the human takes over when it does.

    The human goes where it says; with a verdict and no --next it follows the
    flow, and with neither the turn stays where it was (an answer in place).
    An agent follows the flow, or escalates with `wanted` = human.
    """
    computed = flow_next(header, role, verdict)
    if role == "human":
        if wanted:
            return wanted, ""
        return (computed if verdict else header.get("next") or computed), ""
    if wanted and wanted not in (computed, "human"):
        raise ValueError(f"the flow hands the turn to {computed}, not {wanted}; only the human redirects")
    if wanted == "human" and computed != "human":
        return "human", "escalated"
    if verdict not in SENDS_BACK:
        return computed, ""
    if int(header.get("round", "1")) >= int(header.get("max_rounds", "3")):
        return "human", "round limit reached"
    previous = findings_of(header.get("findings"))
    if findings and previous == findings and header.get("verdict") in SENDS_BACK:
        return "human", "the review repeats the previous findings"
    return computed, ""


def check_claim(header, role, session_id, live_holder, model=None):
    """Refuse a session acting on an item that is not its turn, that another live session holds,
    or whose coder ran the same model.

    `live_holder` is the id of the live session holding the item, or "".
    """
    if role == "human":
        return
    if header.get("next") != role:
        raise PermissionError(f"it is {header.get('next')}'s turn, not {role}'s")
    if live_holder and live_holder != session_id:
        raise PermissionError(f"{header.get('item')} is held by session {live_holder}")
    if role in CHECKERS and same_model(model, header.get("coder_model")):
        raise PermissionError(
            f"the coder of {header.get('item')} ran {header.get('coder_model')}; a {role} runs another model"
        )


def signature(role, agent=None, session_id=None):
    """How a post names its author: `coder@claude [s-1a2b3c]`, or the bare role."""
    name = f"{role}@{agent}" if agent else role
    return f"{name} [{session_id}]" if session_id else name


def check_verdict(role, verdict):
    if not verdict:
        return
    allowed = ROLE_VERDICTS.get(role, ())
    if verdict not in allowed:
        if not allowed:
            raise ValueError(f"a {role} posts no verdict; escalate with --next human")
        raise ValueError(f"a {role} posts {', '.join(allowed)}, not {verdict}")


def apply_post(
    header,
    history,
    role,
    wanted,
    text,
    commit=None,
    verdict=None,
    findings=None,
    now=None,
    agent=None,
    session_id=None,
    model=None,
    live_holder="",
):
    """Return the header and history after `role` posts `text`.

    `wanted` is the role the poster names with --next, or None to follow the
    flow. The post releases the item: whoever takes the next turn claims it.
    """
    if role not in ROLES or (wanted and wanted not in ROLES):
        raise ValueError(f"roles are {', '.join(ROLES)}")
    check_verdict(role, verdict)
    check_claim(header, role, session_id, live_holder, model)
    if not text.strip():
        raise ValueError("a post needs text")
    found = findings_of(",".join(findings or []))
    following, reason = next_role(header, role, wanted, verdict, found)
    approves_plan = role == "human" and verdict == "APPROVED" and header.get("kind") == "plan"
    if approves_plan:
        if header.get("plan_version", "0") == "0":
            raise ValueError("the plan has no text yet; write it with `plan`")
        following = "human"
    header = dict(header)
    turn = int(header.get("turn", "0")) + 1
    if role in PRODUCERS and header.get("verdict") in SENDS_BACK:
        header["round"] = str(int(header.get("round", "1")) + 1)
    stamp = now_stamp(now)
    header.update({"turn": str(turn), "next": following, "reason": reason, "updated": stamp, "holder": ""})
    if commit:
        header["commit"] = commit
    if role == "coder" and model:
        header["coder_model"] = model
    if role in CHECKERS or (role == "human" and verdict):
        header["verdict"] = verdict or ""
        header["findings"] = ",".join(found)
    if approves_plan:
        header["approved_version"] = header["plan_version"]
    details = [f"model: {model}"] if model else []
    if commit:
        details.append(f"commit: {commit}")
    if verdict:
        details.append(f"verdict: {verdict}")
    if found:
        details.append(f"findings: {', '.join(found)}")
    if reason:
        details.append(f"handed to the human: {reason}")
    meta = "".join(f"- {d}\n" for d in details)
    title = f"## turn {turn} · {signature(role, agent, session_id)} → {following} · {stamp}"
    post = f"\n{title}\n\n{meta}{chr(10) if meta else ''}{text.strip()}\n"
    return header, history.rstrip("\n") + "\n" + post


def apply_note(header, history, role, text, now=None, agent=None, session_id=None):
    """Append a note that takes no turn: the turn, holder and verdict stay as they are."""
    if not text.strip():
        raise ValueError("a note needs text")
    header = dict(header)
    stamp = now_stamp(now)
    header["updated"] = stamp
    title = f"## note · {signature(role, agent, session_id)} · {stamp}"
    return header, history.rstrip("\n") + f"\n\n{title}\n\n{text.strip()}\n"


def apply_plan(header, role, now=None, session_id=None, live_holder=""):
    """The header of a plan after `role` replaces its text, bumping its version.

    Until it is approved, the plan is written on the writer's turn (the human
    writes any time). Revising an approved plan reopens it: the turn goes to
    whoever revised it and it needs a new approval. A coder that reopens a
    plan is outside the plan's flow, so its post hands the plan to the human.
    """
    if header.get("kind") != "plan":
        raise ValueError(f"{header.get('item')} is not a plan")
    if role not in PLAN_WRITERS:
        raise ValueError(f"plans are written by {', '.join(PLAN_WRITERS)}")
    reopens = plan_approved(header)
    if not reopens:
        check_claim(header, role, session_id, live_holder)
    header = dict(header)
    header["plan_version"] = str(int(header.get("plan_version", "0")) + 1)
    header["updated"] = now_stamp(now)
    if reopens:
        header.update({"next": role, "verdict": "", "findings": "", "reason": ""})
    return header


def ready(header, role, after_turn):
    """Whether `role` holds the turn of the item at a turn past `after_turn`."""
    try:
        turn = int(header.get("turn", "0"))
    except ValueError:
        return False
    return header.get("next") == role and turn > after_turn


def last_post(history):
    """The last turn and any notes after it."""
    start = history.rfind("\n## turn ")
    if start < 0:
        return history.strip()
    return history[start:].strip()
