"""The turn rules of an item, with no file access.

Only the role named in `next` may post, except the human, who may always
post. A reviewer post that asks for changes hands the turn to the human
instead when the round limit is reached or when it repeats the findings of
the previous review. A plan is approved only by the human; approving it
records `approved_version` and leaves the turn with the human.
"""

import datetime

ROLES = ("coder", "reviewer", "human")
PLAN_WRITERS = ("coder", "human")
VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "NEEDS_DECISION")


def now_stamp(now=None):
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def new_item(item, repo, title, max_rounds, now=None, **fields):
    """The header of an item at turn 0, with the coder to act first."""
    stamp = now_stamp(now)
    header = {
        "item": item,
        "repo": repo,
        "kind": "change",
        "title": title or "",
        "turn": "0",
        "next": "coder",
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


def new_plan(item, repo, title, max_rounds, now=None):
    header = new_item(item, repo, title, max_rounds, now)
    header.update({"kind": "plan", "plan_version": "0", "approved_version": ""})
    return header


def plan_approved(header):
    version = header.get("plan_version", "0")
    return version != "0" and header.get("approved_version") == version


def findings_of(value):
    return sorted({f.strip() for f in (value or "").split(",") if f.strip()})


def next_role(header, role, wanted, verdict, findings):
    """The role that posts next, and why the human takes over when it does."""
    if role != "reviewer" or verdict != "CHANGES_REQUESTED":
        return wanted, ""
    if int(header.get("round", "1")) >= int(header.get("max_rounds", "3")):
        return "human", "round limit reached"
    previous = findings_of(header.get("findings"))
    if findings and previous == findings and header.get("verdict") == "CHANGES_REQUESTED":
        return "human", "the review repeats the previous findings"
    return wanted, ""


def apply_post(header, history, role, wanted, text, commit=None, verdict=None, findings=None, now=None):
    """Return the header and history after `role` posts `text` handing over to `wanted`."""
    if role not in ROLES or wanted not in ROLES:
        raise ValueError(f"roles are {', '.join(ROLES)}")
    if verdict and verdict not in VERDICTS:
        raise ValueError(f"verdicts are {', '.join(VERDICTS)}")
    if role != "human" and header.get("next") != role:
        raise PermissionError(f"it is {header.get('next')}'s turn, not {role}'s")
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
    if role == "coder" and header.get("verdict") == "CHANGES_REQUESTED":
        header["round"] = str(int(header.get("round", "1")) + 1)
    stamp = now_stamp(now)
    header.update({"turn": str(turn), "next": following, "reason": reason, "updated": stamp})
    if commit:
        header["commit"] = commit
    if role == "reviewer" or (role == "human" and verdict):
        header["verdict"] = verdict or ""
        header["findings"] = ",".join(found)
    if approves_plan:
        header["approved_version"] = header["plan_version"]
    details = [f"commit: {commit}"] if commit else []
    if verdict:
        details.append(f"verdict: {verdict}")
    if found:
        details.append(f"findings: {', '.join(found)}")
    if reason:
        details.append(f"handed to the human: {reason}")
    meta = "".join(f"- {d}\n" for d in details)
    title = f"## turn {turn} · {role} → {following} · {stamp}"
    post = f"\n{title}\n\n{meta}{chr(10) if meta else ''}{text.strip()}\n"
    return header, history.rstrip("\n") + "\n" + post


def apply_plan(header, role, now=None):
    """The header of a plan after `role` replaces its text, bumping its version.

    Revising an approved plan reopens it: the turn goes to whoever revised it
    and it needs a new approval.
    """
    if header.get("kind") != "plan":
        raise ValueError(f"{header.get('item')} is not a plan")
    if role not in PLAN_WRITERS:
        raise ValueError(f"plans are written by {', '.join(PLAN_WRITERS)}")
    reopens = plan_approved(header)
    if role != "human" and not reopens and header.get("next") != role:
        raise PermissionError(f"it is {header.get('next')}'s turn, not {role}'s")
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
    start = history.rfind("\n## turn ")
    if start < 0:
        return history.strip()
    return history[start:].strip()
