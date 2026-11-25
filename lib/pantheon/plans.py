"""The steps of a plan and their dependencies, with no file access.

The planner numbers the steps of the plan's `## Steps` section (`## Pasos`
works too) and ends each with `Depende de: 1, 2` (or `Depends on:`,
`Needs:`); `nada`, `none` or no such line means it depends on nothing.
Writing the plan parses the steps, refuses unknown, self and circular
dependencies, and stores them in the plan header as `steps: 1 2<1 3<1,2`.

A step is `done` once its item finished its flow with a positive verdict
(APPROVED or PASSED with the turn back to the human), `active` while its
item is open, `ready` when it has no item yet and every step it needs is
done, and `blocked` otherwise.
"""

import re

from . import board

STEP_LINE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
NEEDS = re.compile(r"(?i)\b(?:depende de|depends on|needs)\s*:\s*([^.;\n]*)")
SECTION = re.compile(r"(?im)^##\s+(?:steps|pasos)\s*$")


def steps_section(text):
    match = SECTION.search(text)
    if not match:
        return text
    rest = text[match.end() :]
    following = re.search(r"(?m)^##\s", rest)
    return rest[: following.start()] if following else rest


def parse_steps(text):
    """{step: (title, needs)} from a plan's text, in order; ValueError when inconsistent."""
    blocks = []
    for line in steps_section(text).splitlines():
        match = STEP_LINE.match(line)
        if match:
            blocks.append([int(match.group(1)), [match.group(2)]])
        elif blocks:
            blocks[-1][1].append(line.strip())
    steps = {}
    for number, lines in blocks:
        if number in steps:
            raise ValueError(f"step {number} appears twice")
        body = " ".join(lines)
        found = NEEDS.search(body)
        needs = tuple(int(n) for n in re.findall(r"\d+", found.group(1))) if found else ()
        steps[number] = (title_of(lines[0]), tuple(dict.fromkeys(needs)))
    check(steps)
    return steps


def title_of(line):
    text = re.sub(r"\*\*|`", "", line).strip()
    text = re.split(r"(?<=[.:])\s", text, maxsplit=1)[0].rstrip(".:")
    return text


def check(steps):
    for number, (_, needs) in steps.items():
        for need in needs:
            if need == number:
                raise ValueError(f"step {number} depends on itself")
            if need not in steps:
                raise ValueError(f"step {number} depends on step {need}, which the plan does not have")
    state = {}

    def visit(number, path):
        if state.get(number) == "done":
            return
        if state.get(number) == "visiting":
            cycle = " → ".join(str(n) for n in path[path.index(number) :] + [number])
            raise ValueError(f"the steps depend on each other in a circle: {cycle}")
        state[number] = "visiting"
        for need in steps[number][1]:
            visit(need, path + [number])
        state[number] = "done"

    for number in steps:
        visit(number, [])


def format_steps(steps):
    """The `steps` header value: `1 2<1 3<1,2`."""
    return " ".join(f"{n}<{','.join(map(str, needs))}" if needs else str(n) for n, (_, needs) in steps.items())


def read_steps(value):
    """{step: needs} from a `steps` header value."""
    steps = {}
    for token in (value or "").split():
        number, _, needs = token.partition("<")
        steps[int(number)] = tuple(int(n) for n in needs.split(",") if n)
    return steps


def finished(header):
    """Whether an item ran its whole flow and ended with a positive verdict."""
    return (
        header.get("next") == "human"
        and header.get("verdict") in ("APPROVED", "PASSED")
        and header.get("reason", "") == ""
        and header.get("kind", "change") == "change"
    ) or header.get("state") == "merged"


def step_items(plan_name, items):
    """{step: (name, header)} of the items opened for the plan's steps."""
    found = {}
    for name, header in items:
        if header.get("plan") == plan_name and header.get("step", "").isdigit():
            found.setdefault(int(header["step"]), (name, header))
    return found


def step_states(plan_header, items):
    """[(step, needs, name or "", state, detail)] for every step of the plan.

    `items` are (name, header) pairs of the plan's repo.
    """
    steps = read_steps(plan_header.get("steps"))
    opened = step_items(plan_header.get("item"), items)
    done = {n for n, (_, header) in opened.items() if finished(header)}
    rows = []
    for number, needs in steps.items():
        missing = [n for n in needs if n not in done]
        if number in opened:
            name, header = opened[number]
            if number in done:
                rows.append((number, needs, name, "done", ""))
            else:
                rows.append((number, needs, name, "active", f"next {header.get('next', '')}"))
        elif missing:
            rows.append((number, needs, "", "blocked", "needs " + ",".join(map(str, missing))))
        else:
            rows.append((number, needs, "", "ready", ""))
    return rows


def missing_needs(plan_header, step, items):
    """The steps `step` needs that are not done yet."""
    steps = read_steps(plan_header.get("steps"))
    opened = step_items(plan_header.get("item"), items)
    done = {n for n, (_, header) in opened.items() if finished(header)}
    return [n for n in steps.get(step, ()) if n not in done]


def approved(plan_header):
    return board.plan_approved(plan_header)
