"""The `pantheon` command line.

An item is named `ITEM` (the repo around the current directory, or the only
repo that has it when outside git) or `repo/ITEM`. `list` and `inbox` look at
the current repo, or at every repo outside git or with `--all`.
"""

import argparse
import os
import subprocess
import sys
import time

from . import agents, board, dispatch, plans, sessions, store

ROLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "roles")
AGENT_ROLES = tuple(r for r in board.ROLES if r != "human")
BEAT_SECONDS = 30


class Failure(Exception):
    """An error reported to the user as `error: ...` with exit status 1."""


def identity(args, required=True):
    """(role, session record or None) of whoever runs the command.

    The session comes from `--session` or PANTHEON_SESSION and fixes the
    role. Without a session only the human acts, unless `required` is false
    (a read that only needs the role).
    """
    session_id = getattr(args, "session", None) or os.environ.get("PANTHEON_SESSION")
    role = getattr(args, "role", None)
    if session_id:
        try:
            record = sessions.require(session_id)
        except ValueError as e:
            raise Failure(e) from e
        if role and role != record["role"]:
            raise Failure(f"session {session_id} plays {record['role']}, not {role}")
        return record["role"], record
    if not role:
        raise Failure("name a --session (or --role for the human)")
    if required and role != "human":
        raise Failure(f"a {role} acts through a session: join first, then pass --session")
    return role, None


def role_contract(role):
    try:
        with open(os.path.join(ROLES_DIR, f"{role}.md"), encoding="utf-8") as source:
            return source.read().strip()
    except FileNotFoundError:
        return ""


def resolve(ref, explicit_repo=None):
    """(repo, item) named by `ref`."""
    try:
        if "/" in ref:
            slug, _, item = ref.partition("/")
            return store.check_name("repo", slug), store.check_name("item", item)
        store.check_name("item", ref)
        slug, common = store.current_repo(explicit_repo)
        if slug:
            store.repo_dir(slug, common)
            return slug, ref
    except ValueError as e:
        raise Failure(e) from e
    owners = [s for s in store.repos() if os.path.exists(os.path.join(store.boards_root(), s, f"{ref}.md"))]
    if len(owners) == 1:
        return owners[0], ref
    if not owners:
        raise Failure(f"no item {ref} in any repo")
    raise Failure(f"{ref} is in several repos ({', '.join(owners)}); name it repo/{ref}")


def scope(args):
    """The repos `list` and `inbox` look at."""
    if getattr(args, "all", False):
        return store.repos()
    slug, _ = store.current_repo(args.repo)
    return [slug] if slug else store.repos()


def read_existing(slug, item):
    try:
        return store.read(slug, item)
    except FileNotFoundError as e:
        raise Failure(f"no item {slug}/{item}") from e


def text_of(args):
    if getattr(args, "file", None):
        with open(args.file, encoding="utf-8") as source:
            return source.read()
    return getattr(args, "message", None) or ""


def plan_context(slug, header):
    """The plan an item belongs to, as text printed with the item, or ""."""
    if header.get("kind") == "plan":
        if header.get("plan_version", "0") == "0":
            return ""
        name, step = header.get("item", ""), ""
    elif header.get("plan"):
        name, step = header["plan"], header.get("step", "")
    else:
        return ""
    try:
        plan_header, _ = store.read(slug, name)
        text = store.read_plan(slug, name)
    except (OSError, ValueError):
        return f"===== plan {slug}/{name}: missing =====\n"
    version = plan_header.get("plan_version", "0")
    state = "approved" if board.plan_approved(plan_header) else "not approved"
    title = f"===== plan {slug}/{name} v{version} ({state})"
    lines = [title + (f" · this item is step {step} =====" if step else " =====")]
    if step:
        lines.append(f"Only step {step} is in scope; the other steps belong to their own items.")
        opened = header.get("plan_version", "")
        if opened and opened != version:
            lines.append(f"This item was opened at plan v{opened}.")
    lines.append(text)
    return "\n".join(lines) + "\n"


def print_item(slug, header, text):
    print(store.render(header, ""), end="")
    print(text)
    context = plan_context(slug, header)
    if context:
        print()
        print(context, end="")


def cmd_init(args):
    explicit = args.repo
    try:
        slug, common = store.current_repo(explicit)
    except ValueError as e:
        raise Failure(e) from e
    if not slug:
        raise Failure("not inside a git repo; name the repo with --repo")
    store.check_name("item", args.item)
    store.repo_dir(slug, common)
    with store.locked(slug):
        path = store.item_path(slug, args.item)
        if os.path.exists(path):
            raise Failure(f"{slug}/{args.item} already exists")
        if args.kind == "plan":
            header = board.new_plan(args.item, slug, args.title, args.max_rounds, flow=args.flow)
            store.write(slug, args.item, header, f"\n# {args.item}\n")
            print(f"{slug}/{args.item}")
            return 0
        if (args.plan is None) != (args.step is None):
            raise Failure("--plan and --step go together")
        if args.step is not None and args.step < 1:
            raise Failure("steps are numbered from 1")
        fields = {}
        if args.plan:
            try:
                plan_header, _ = store.read(slug, args.plan)
            except (OSError, ValueError) as e:
                raise Failure(f"no plan {slug}/{args.plan}") from e
            if plan_header.get("kind") != "plan":
                raise Failure(f"{args.plan} is not a plan")
            if not board.plan_approved(plan_header):
                raise Failure(
                    f"plan {args.plan} v{plan_header.get('plan_version', '0')} is not approved by the human"
                )
            steps = plans.read_steps(plan_header.get("steps"))
            if steps and args.step not in steps:
                raise Failure(f"plan {args.plan} has no step {args.step}")
            pairs = [(name, h) for _, name, h, _ in store.items([slug])]
            if any(h.get("plan") == args.plan and h.get("step") == str(args.step) for _, h in pairs):
                raise Failure(f"step {args.step} of {args.plan} already has an item")
            missing = plans.missing_needs(plan_header, args.step, pairs)
            if missing and not args.ignore_needs:
                raise Failure(
                    f"step {args.step} needs step {', '.join(map(str, missing))} done first "
                    "(--ignore-needs opens it anyway)"
                )
            fields.update(
                plan=args.plan,
                step=args.step,
                needs=",".join(map(str, steps.get(args.step, ()))),
                plan_version=plan_header["plan_version"],
            )
        cwd = os.getcwd()
        if args.new_worktree:
            worktree, branch = new_worktree(slug, args.item, args.branch, args.base, common)
        else:
            worktree = args.worktree or (store.git(cwd, "rev-parse", "--show-toplevel") if common else None)
            branch = args.branch or (store.git(cwd, "branch", "--show-current") if common else None)
        if not worktree or not branch:
            raise Failure("an item needs --branch and --worktree")
        fields.update(
            branch=branch,
            worktree=os.path.abspath(os.path.expanduser(worktree)),
            forge=store.forge_of(common) if common else "",
            mr=args.mr or "",
            commit="",
        )
        header = board.new_item(args.item, slug, args.title, args.max_rounds, flow=args.flow, **fields)
        store.write(slug, args.item, header, f"\n# {args.item}\n")
    print(f"{slug}/{args.item}")
    return 0


def new_worktree(slug, item, branch, base, common):
    """Create the item's worktree at the configured path; return (path, branch)."""
    if not common:
        raise Failure("--new-worktree needs a git repo")
    template = store.load_config().get("worktrees", {}).get("path", "~/code/{repo}-worktrees/{item}")
    path = os.path.abspath(os.path.expanduser(template.format(repo=slug, item=item)))
    branch = branch or f"pantheon/{item.lower()}"
    if os.path.exists(path):
        raise Failure(f"{path} already exists")
    result = subprocess.run(
        ("git", "worktree", "add", "-b", branch, path, base or "HEAD"), capture_output=True, text=True
    )
    if result.returncode:
        raise Failure(f"git worktree add failed: {result.stderr.strip()}")
    return path, branch


def human_only(command):
    if os.environ.get("PANTHEON_SESSION"):
        raise Failure(f"{command} is the human's; an agent session cannot run it")


def cmd_approve(args):
    human_only("approve")
    slug, item = resolve(args.item, args.repo)
    with store.locked(slug):
        header, history = read_existing(slug, item)
        try:
            cast = agents.complete_cast(board.flow_of(header), agents.parse_cast(args.cast or []))
        except ValueError as e:
            raise Failure(e) from e
        header["cast"] = agents.format_cast(cast)
        header["approved"] = board.now_stamp()
        header, history = board.apply_note(header, history, "human", f"Approved for dispatch. Cast: {header['cast']}")
        store.write(slug, item, header, history)
    print(f"approved {slug}/{item}: {header['cast']}")
    if args.dispatch:
        return dispatch_one(slug, item, False)
    return 0


def cmd_revoke(args):
    human_only("revoke")
    slug, item = resolve(args.item, args.repo)
    with store.locked(slug):
        header, history = read_existing(slug, item)
        header["approved"] = ""
        header, history = board.apply_note(header, history, "human", "Dispatch approval revoked.")
        store.write(slug, item, header, history)
    print(f"revoked {slug}/{item}")
    return 0


def dispatch_one(slug, item, dry_run):
    try:
        message = dispatch.run(slug, item, dry_run=dry_run)
    except dispatch.Refused as e:
        raise Failure(e) from e
    print(message)
    return 3 if message.startswith(dispatch.MANUAL) else 0


def cmd_dispatch(args):
    slug, item = resolve(args.item, args.repo)
    read_existing(slug, item)
    return dispatch_one(slug, item, args.dry_run)


def cmd_plan(args):
    slug, item = resolve(args.item, args.repo)
    with open(args.file, encoding="utf-8") as source:
        text = source.read()
    if not text.strip():
        raise Failure("the plan is empty")
    role, record = identity(args)
    if role not in board.PLAN_WRITERS:
        raise Failure(f"plans are written by {', '.join(board.PLAN_WRITERS)}")
    with store.locked(slug):
        header, history = read_existing(slug, item)
        try:
            header = board.apply_plan(
                header,
                role,
                session_id=record["id"] if record else None,
                live_holder=sessions.holder_alive(header),
            )
            header["steps"] = plans.format_steps(plans.parse_steps(text))
        except (PermissionError, ValueError) as e:
            raise Failure(e) from e
        store.write_text(store.plan_path(slug, item), text.strip() + "\n")
        store.write(slug, item, header, history)
    print(f"plan {slug}/{item} v{header['plan_version']}")
    return 0


def cmd_post(args):
    slug, item = resolve(args.item, args.repo)
    text = text_of(args)
    if not text.strip():
        raise Failure("a post needs -m TEXT or --file PATH")
    role, record = identity(args)
    with store.locked(slug):
        header, history = read_existing(slug, item)
        try:
            header, history = board.apply_post(
                header,
                history,
                role,
                args.next,
                text,
                commit=args.commit,
                verdict=args.verdict,
                findings=(args.findings or "").split(","),
                agent=record["agent"] if record else None,
                session_id=record["id"] if record else None,
                model=record.get("model") if record else None,
                live_holder=sessions.holder_alive(header),
            )
        except (PermissionError, ValueError) as e:
            raise Failure(e) from e
        if args.mr:
            header["mr"] = args.mr
        store.write(slug, item, header, history)
    if record:
        sessions.touch(record, repo=slug, item=item)
    print(f"turn {header['turn']}: next {header['next']}" + (f" ({header['reason']})" if header["reason"] else ""))
    return 0


def available(header, role, after_turn, record):
    """Whether the turn is `role`'s past `after_turn` and no other live session holds it."""
    if not board.ready(header, role, after_turn):
        return False
    if record and role in board.CHECKERS and board.same_model(record.get("model"), header.get("coder_model")):
        return False
    holder = sessions.holder_alive(header)
    return not holder or (record is not None and holder == record["id"])


class Beat:
    """Refreshes a waiting session's heartbeat every BEAT_SECONDS."""

    def __init__(self, record):
        self.record, self.last = record, time.monotonic()

    def __call__(self):
        if self.record and time.monotonic() - self.last >= BEAT_SECONDS:
            current = sessions.load(self.record["id"]) or self.record
            if current.get("state") == "active":
                self.record = sessions.touch(current)
            self.last = time.monotonic()


def cmd_note(args):
    slug, item = resolve(args.item, args.repo)
    text = text_of(args)
    role, record = identity(args)
    with store.locked(slug):
        header, history = read_existing(slug, item)
        try:
            header, history = board.apply_note(
                header,
                history,
                role,
                text,
                agent=record["agent"] if record else None,
                session_id=record["id"] if record else None,
            )
        except ValueError as e:
            raise Failure(e) from e
        store.write(slug, item, header, history)
    print(f"note on {slug}/{item}")
    return 0


def cmd_wait(args):
    slug, item = resolve(args.item, args.repo)
    role, record = identity(args, required=False)
    beat = Beat(record)
    deadline = time.monotonic() + args.timeout if args.timeout else None
    while True:
        try:
            header, history = store.read(slug, item)
        except (OSError, ValueError):
            header, history = {}, ""
        if header and available(header, role, args.after_turn, record):
            print_item(slug, header, board.last_post(history))
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            print(f"timeout waiting for {role} on {slug}/{item}", file=sys.stderr)
            return 2
        beat()
        time.sleep(args.interval)


def holders(slugs, roles):
    """The turn of every item whose turn one of `roles` holds, by repo/item."""
    return {
        f"{slug}/{name}": header.get("turn", "")
        for slug, name, header, _ in store.items(slugs)
        if any(board.ready(header, role, 0) for role in roles)
    }


def cmd_inbox(args):
    role, record = identity(args, required=False)
    beat = Beat(record)
    deadline = time.monotonic() + args.timeout if args.timeout else None
    handed = args.handed_to or []
    already = holders(scope(args), handed)
    while True:
        slugs = scope(args)
        current = holders(slugs, handed) if handed else {}
        pending = [
            (slug, name, header, history)
            for slug, name, header, history in store.items(slugs)
            if available(header, role, 0, record)
            or (f"{slug}/{name}" in current and already.get(f"{slug}/{name}") != current[f"{slug}/{name}"])
        ]
        if pending:
            for slug, name, header, history in pending:
                print(f"===== {slug}/{name} =====")
                print_item(slug, header, board.last_post(history))
                print()
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            print(f"timeout waiting for any item for {role}", file=sys.stderr)
            return 2
        beat()
        time.sleep(args.interval)


def take(slug, item, role, record):
    """Make `record` the holder of the item, or explain why it cannot be."""
    read_existing(slug, item)
    try:
        sessions.hold(slug, item, role, record)
    except PermissionError as e:
        raise Failure(e) from e


def cmd_join(args):
    if args.item:
        slug, item = resolve(args.item, args.repo)
        read_existing(slug, item)
    else:
        slug, _ = store.current_repo(args.repo)
        item = None
    try:
        record = sessions.create(
            args.agent,
            args.role,
            repo=slug,
            item=item,
            model=args.model,
            mode=args.mode,
            harness_session=args.harness_session,
            pid=args.pid,
        )
    except ValueError as e:
        raise Failure(e) from e
    state = "no item yet: wait with inbox"
    if item:
        try:
            take(slug, item, args.role, record)
            state = f"holding {slug}/{item}"
        except Failure as e:
            state = f"waiting on {slug}/{item}: {e}"
    print(f"session {record['id']} · {board.signature(args.role, args.agent)} · {state}")
    print(f"export PANTHEON_SESSION={record['id']}")
    contract = role_contract(args.role)
    if contract:
        print()
        print(contract)
    return 0


def cmd_claim(args):
    slug, item = resolve(args.item, args.repo)
    role, record = identity(args)
    if not record:
        raise Failure("the human does not claim items")
    take(slug, item, role, record)
    sessions.touch(record, repo=slug, item=item)
    print(f"{record['id']} holds {slug}/{item}")
    return 0


def release_all(session_id, only=None):
    """Clear `session_id` as holder of every item, or of the (repo, item) in `only`."""
    released = []
    for slug in [only[0]] if only else store.repos():
        with store.locked(slug):
            for _, name, header, history in store.items([slug]):
                if header.get("holder") == session_id and (not only or name == only[1]):
                    header["holder"] = ""
                    store.write(slug, name, header, history)
                    released.append(f"{slug}/{name}")
    return released


def cmd_release(args):
    slug, item = resolve(args.item, args.repo)
    _, record = identity(args)
    if not record:
        raise Failure("name the --session that holds the item")
    released = release_all(record["id"], (slug, item))
    print(f"released {', '.join(released)}" if released else f"{record['id']} does not hold {slug}/{item}")
    return 0


def cmd_heartbeat(args):
    try:
        record = sessions.require(args.session)
    except ValueError as e:
        raise Failure(e) from e
    print(f"{record['id']} alive")
    return 0


def cmd_leave(args):
    record = sessions.load(args.session)
    if record is None:
        raise Failure(f"no session {args.session}")
    sessions.save({**record, "state": "left", "heartbeat": sessions.stamp()})
    released = release_all(record["id"])
    print(f"{record['id']} left" + (f"; released {', '.join(released)}" if released else ""))
    return 0


def cmd_sessions(args):
    rows = []
    for record in sessions.every():
        state = sessions.status(record)
        if state == "left" and not args.all:
            continue
        where = f"{record['repo']}/{record['item']}" if record.get("item") else record.get("repo", "")
        rows.append(
            (
                record["id"],
                record["agent"],
                record.get("model", ""),
                record["role"],
                where,
                record.get("mode", ""),
                state,
                record.get("heartbeat", ""),
            )
        )
    table(("SESSION", "AGENT", "MODEL", "ROLE", "ITEM", "MODE", "STATE", "HEARTBEAT"), rows)
    return 0


def cmd_role(args):
    contract = role_contract(args.name)
    if not contract:
        raise Failure(f"no contract for {args.name}")
    print(contract)
    return 0


def holder_cell(header):
    holder = header.get("holder", "")
    if not holder:
        return ""
    return holder if sessions.holder_alive(header) else f"{holder} (stale)"


def table(columns, rows):
    widths = [max(len(str(row[i])) for row in [columns] + rows) for i in range(len(columns))]
    for row in [columns] + rows:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip())


def plan_cell(header):
    if header.get("kind") == "plan":
        state = "approved" if board.plan_approved(header) else "draft"
        return f"v{header.get('plan_version', '0')} {state}"
    if header.get("plan"):
        return f"{header['plan']}#{header.get('step', '')}"
    return ""


def mr_cell(header):
    number = header.get("mr", "")
    if not number:
        return ""
    return f"{'#' if header.get('forge') == 'github' else '!'}{number}"


def short(text, width):
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_list(args):
    rows = [
        (
            f"{slug}/{name}",
            short(h.get("title", ""), 32),
            h.get("turn", ""),
            h.get("next", ""),
            holder_cell(h),
            h.get("verdict", ""),
            mr_cell(h),
            plan_cell(h),
            h.get("updated", ""),
        )
        for slug, name, h, _ in store.items(scope(args))
        if not args.role or h.get("next") == args.role
    ]
    table(("ITEM", "TITLE", "TURN", "NEXT", "HOLDER", "VERDICT", "MR", "PLAN", "UPDATED"), rows)
    return 0


def cmd_show(args):
    slug, item = resolve(args.item, args.repo)
    header, history = read_existing(slug, item)
    print(store.render(header, history), end="")
    context = plan_context(slug, header)
    if context:
        print()
        print(context, end="")
    return 0


def cmd_ready(args):
    if args.plan:
        slug, name = resolve(args.plan, args.repo)
        targets = [(slug, name, read_existing(slug, name)[0])]
        if targets[0][2].get("kind") != "plan":
            raise Failure(f"{args.plan} is not a plan")
    else:
        targets = [(s, n, h) for s, n, h, _ in store.items(scope(args)) if h.get("kind") == "plan"]
    for index, (slug, name, header) in enumerate(targets):
        if index:
            print()
        state = "approved" if board.plan_approved(header) else "not approved"
        print(f"===== plan {slug}/{name} v{header.get('plan_version', '0')} ({state}) =====")
        try:
            titles = {n: t for n, (t, _) in plans.parse_steps(store.read_plan(slug, name)).items()}
        except (OSError, ValueError):
            titles = {}
        pairs = [(n, h) for _, n, h, _ in store.items([slug])]
        rows = [
            (str(step), short(titles.get(step, ""), 40), ",".join(map(str, needs)), item, state_, detail)
            for step, needs, item, state_, detail in plans.step_states(header, pairs)
        ]
        if not rows:
            print("no steps: write them as a numbered list under ## Steps")
            continue
        table(("STEP", "TITLE", "NEEDS", "ITEM", "STATE", "DETAIL"), rows)
    if not targets:
        print("no plans")
    return 0


def cmd_repos(args):
    for slug in store.repos():
        try:
            with open(os.path.join(store.boards_root(), slug, ".repo"), encoding="utf-8") as source:
                bound = source.read().strip()
        except FileNotFoundError:
            bound = ""
        print(f"{slug}  {bound}".rstrip())
    return 0


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="the repo's board, instead of the repo around the current directory")
    actor = argparse.ArgumentParser(add_help=False)
    actor.add_argument("--session", help="your session (or PANTHEON_SESSION); it fixes your role")
    actor.add_argument("--role", choices=board.ROLES, help="without a session: only human")

    root = argparse.ArgumentParser(prog="pantheon", description="Shared turn board for agents and the human.")
    commands = root.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", parents=[common], help="open an item or a plan")
    init.add_argument("item")
    init.add_argument("--kind", choices=("change", "plan"), default="change")
    init.add_argument("--title")
    init.add_argument("--branch")
    init.add_argument("--worktree")
    init.add_argument("--mr", help="MR (GitLab) or PR (GitHub) number")
    init.add_argument("--max-rounds", type=int, default=3)
    init.add_argument("--plan")
    init.add_argument("--step", type=int)
    init.add_argument("--new-worktree", action="store_true", help="create the worktree at [worktrees] path of the config")
    init.add_argument("--base", help="with --new-worktree: the commit to branch from (default HEAD)")
    init.add_argument("--ignore-needs", action="store_true", help="open a step before the steps it needs are done")
    init.add_argument("--flow", help="roles in order, e.g. coder,reviewer (default: coder,reviewer,qa; plans planner,reviewer)")
    init.set_defaults(run=cmd_init)

    plan = commands.add_parser("plan", parents=[common, actor], help="write or revise a plan's text")
    plan.add_argument("item")
    plan.add_argument("--file", required=True)
    plan.set_defaults(run=cmd_plan)

    post = commands.add_parser("post", parents=[common, actor], help="take a turn and hand it over")
    post.add_argument("item")
    post.add_argument("--next", choices=board.ROLES, help="default: the flow; agents may only escalate to human")
    post.add_argument("--commit")
    post.add_argument("--verdict", choices=board.VERDICTS)
    post.add_argument("--findings")
    post.add_argument("--mr", help="record the MR or PR number")
    post.add_argument("-m", "--message")
    post.add_argument("--file")
    post.set_defaults(run=cmd_post)

    wait = commands.add_parser("wait", parents=[common, actor], help="block until the turn is yours")
    wait.add_argument("item")
    wait.add_argument("--after-turn", type=int, required=True)
    wait.add_argument("--timeout", type=float)
    wait.add_argument("--interval", type=float, default=2.0)
    wait.set_defaults(run=cmd_wait)

    show = commands.add_parser("show", parents=[common], help="print an item with its history")
    show.add_argument("item")
    show.set_defaults(run=cmd_show)

    inbox = commands.add_parser("inbox", parents=[common, actor], help="block until any item is yours")
    inbox.add_argument("--handed-to", action="append", choices=board.ROLES)
    inbox.add_argument("--all", action="store_true", help="every repo")
    inbox.add_argument("--timeout", type=float)
    inbox.add_argument("--interval", type=float, default=2.0)
    inbox.set_defaults(run=cmd_inbox)

    listing = commands.add_parser("list", parents=[common], help="every item's turn without waiting")
    listing.add_argument("--role", choices=board.ROLES)
    listing.add_argument("--all", action="store_true", help="every repo")
    listing.set_defaults(run=cmd_list)

    ready = commands.add_parser("ready", parents=[common], help="the steps of each approved plan and which can start")
    ready.add_argument("plan", nargs="?", help="one plan; default: every plan in scope")
    ready.add_argument("--all", action="store_true", help="every repo")
    ready.set_defaults(run=cmd_ready)

    repos = commands.add_parser("repos", help="every repo with a board")
    repos.set_defaults(run=cmd_repos, repo=None)

    join = commands.add_parser("join", parents=[common], help="start a session: an agent taking a role")
    join.add_argument("item", nargs="?", help="the item to work on; without it, wait with inbox")
    join.add_argument("--role", required=True, choices=AGENT_ROLES)
    join.add_argument("--agent", required=True)
    join.add_argument("--model", required=True, help="the model the agent runs, e.g. claude-opus-5-5")
    join.add_argument("--mode", choices=sessions.MODES, default="interactive")
    join.add_argument("--harness-session", help="the CLI's own session id, to resume it later")
    join.add_argument("--pid", type=int, help="the agent's process, when Pantheon launched it")
    join.set_defaults(run=cmd_join)

    note = commands.add_parser("note", parents=[common, actor], help="add a note without taking the turn")
    note.add_argument("item")
    note.add_argument("-m", "--message")
    note.add_argument("--file")
    note.set_defaults(run=cmd_note)

    approve = commands.add_parser("approve", parents=[common], help="the human approves an item for dispatch, with its cast")
    approve.add_argument("item")
    approve.add_argument("--cast", action="append", metavar="ROLE=AGENT[:MODEL]", help="repeat per role; defaults come from [defaults.cast]")
    approve.add_argument("--dispatch", action="store_true", help="launch the current turn right away")
    approve.set_defaults(run=cmd_approve)

    revoke = commands.add_parser("revoke", parents=[common], help="withdraw an item's dispatch approval")
    revoke.add_argument("item")
    revoke.set_defaults(run=cmd_revoke)

    launch = commands.add_parser("dispatch", parents=[common], help="launch the agent whose turn it is (needs approve)")
    launch.add_argument("item")
    launch.add_argument("--dry-run", action="store_true", help="print the command instead of running it")
    launch.set_defaults(run=cmd_dispatch)

    claim = commands.add_parser("claim", parents=[common, actor], help="hold an item whose turn is yours")
    claim.add_argument("item")
    claim.set_defaults(run=cmd_claim)

    release = commands.add_parser("release", parents=[common, actor], help="stop holding an item")
    release.add_argument("item")
    release.set_defaults(run=cmd_release)

    heartbeat = commands.add_parser("heartbeat", help="keep a session alive during long work")
    heartbeat.add_argument("session")
    heartbeat.set_defaults(run=cmd_heartbeat)

    leave = commands.add_parser("leave", help="end a session and release its items")
    leave.add_argument("session")
    leave.set_defaults(run=cmd_leave)

    listing_sessions = commands.add_parser("sessions", help="every live or stale session")
    listing_sessions.add_argument("--all", action="store_true", help="also the sessions that left")
    listing_sessions.set_defaults(run=cmd_sessions)

    role = commands.add_parser("role", help="print a role's contract")
    role.add_argument("name", choices=board.ROLES)
    role.set_defaults(run=cmd_role)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.run(args) or 0
    except (Failure, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
