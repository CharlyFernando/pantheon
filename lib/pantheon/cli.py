"""The `pantheon` command line.

An item is named `ITEM` (the repo around the current directory, or the only
repo that has it when outside git) or `repo/ITEM`. `list` and `inbox` look at
the current repo, or at every repo outside git or with `--all`.
"""

import argparse
import os
import sys
import time

from . import board, store


class Failure(Exception):
    """An error reported to the user as `error: ...` with exit status 1."""


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
            header = board.new_plan(args.item, slug, args.title, args.max_rounds)
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
            fields.update(plan=args.plan, step=args.step, plan_version=plan_header["plan_version"])
        cwd = os.getcwd()
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
        header = board.new_item(args.item, slug, args.title, args.max_rounds, **fields)
        store.write(slug, args.item, header, f"\n# {args.item}\n")
    print(f"{slug}/{args.item}")
    return 0


def cmd_plan(args):
    slug, item = resolve(args.item, args.repo)
    with open(args.file, encoding="utf-8") as source:
        text = source.read()
    if not text.strip():
        raise Failure("the plan is empty")
    with store.locked(slug):
        header, history = read_existing(slug, item)
        try:
            header = board.apply_plan(header, args.role)
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
    with store.locked(slug):
        header, history = read_existing(slug, item)
        try:
            header, history = board.apply_post(
                header,
                history,
                args.role,
                args.next,
                text,
                commit=args.commit,
                verdict=args.verdict,
                findings=(args.findings or "").split(","),
            )
        except (PermissionError, ValueError) as e:
            raise Failure(e) from e
        if args.mr:
            header["mr"] = args.mr
        store.write(slug, item, header, history)
    print(f"turn {header['turn']}: next {header['next']}" + (f" ({header['reason']})" if header["reason"] else ""))
    return 0


def cmd_wait(args):
    slug, item = resolve(args.item, args.repo)
    deadline = time.monotonic() + args.timeout if args.timeout else None
    while True:
        try:
            header, history = store.read(slug, item)
        except (OSError, ValueError):
            header, history = {}, ""
        if header and board.ready(header, args.role, args.after_turn):
            print_item(slug, header, board.last_post(history))
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            print(f"timeout waiting for {args.role} on {slug}/{item}", file=sys.stderr)
            return 2
        time.sleep(args.interval)


def holders(slugs, roles):
    """The turn of every item whose turn one of `roles` holds, by repo/item."""
    return {
        f"{slug}/{name}": header.get("turn", "")
        for slug, name, header, _ in store.items(slugs)
        if any(board.ready(header, role, 0) for role in roles)
    }


def cmd_inbox(args):
    deadline = time.monotonic() + args.timeout if args.timeout else None
    handed = args.handed_to or []
    already = holders(scope(args), handed)
    while True:
        slugs = scope(args)
        current = holders(slugs, handed) if handed else {}
        pending = [
            (slug, name, header, history)
            for slug, name, header, history in store.items(slugs)
            if board.ready(header, args.role, 0)
            or (f"{slug}/{name}" in current and already.get(f"{slug}/{name}") != current[f"{slug}/{name}"])
        ]
        if pending:
            for slug, name, header, history in pending:
                print(f"===== {slug}/{name} =====")
                print_item(slug, header, board.last_post(history))
                print()
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            print(f"timeout waiting for any item for {args.role}", file=sys.stderr)
            return 2
        time.sleep(args.interval)


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
            h.get("verdict", ""),
            mr_cell(h),
            plan_cell(h),
            h.get("updated", ""),
        )
        for slug, name, h, _ in store.items(scope(args))
        if not args.role or h.get("next") == args.role
    ]
    columns = ("ITEM", "TITLE", "TURN", "NEXT", "VERDICT", "MR", "PLAN", "UPDATED")
    widths = [max(len(str(row[i])) for row in [columns] + rows) for i in range(len(columns))]
    for row in [columns] + rows:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip())
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
    init.set_defaults(run=cmd_init)

    plan = commands.add_parser("plan", parents=[common], help="write or revise a plan's text")
    plan.add_argument("item")
    plan.add_argument("--role", required=True, choices=board.PLAN_WRITERS)
    plan.add_argument("--file", required=True)
    plan.set_defaults(run=cmd_plan)

    post = commands.add_parser("post", parents=[common], help="take a turn and hand it over")
    post.add_argument("item")
    post.add_argument("--role", required=True, choices=board.ROLES)
    post.add_argument("--next", required=True, choices=board.ROLES)
    post.add_argument("--commit")
    post.add_argument("--verdict", choices=board.VERDICTS)
    post.add_argument("--findings")
    post.add_argument("--mr", help="record the MR or PR number")
    post.add_argument("-m", "--message")
    post.add_argument("--file")
    post.set_defaults(run=cmd_post)

    wait = commands.add_parser("wait", parents=[common], help="block until the turn is yours")
    wait.add_argument("item")
    wait.add_argument("--role", required=True, choices=board.ROLES)
    wait.add_argument("--after-turn", type=int, required=True)
    wait.add_argument("--timeout", type=float)
    wait.add_argument("--interval", type=float, default=2.0)
    wait.set_defaults(run=cmd_wait)

    show = commands.add_parser("show", parents=[common], help="print an item with its history")
    show.add_argument("item")
    show.set_defaults(run=cmd_show)

    inbox = commands.add_parser("inbox", parents=[common], help="block until any item is yours")
    inbox.add_argument("--role", required=True, choices=board.ROLES)
    inbox.add_argument("--handed-to", action="append", choices=board.ROLES)
    inbox.add_argument("--all", action="store_true", help="every repo")
    inbox.add_argument("--timeout", type=float)
    inbox.add_argument("--interval", type=float, default=2.0)
    inbox.set_defaults(run=cmd_inbox)

    listing = commands.add_parser("list", parents=[common], help="every item's turn without waiting")
    listing.add_argument("--role", choices=board.ROLES)
    listing.add_argument("--all", action="store_true", help="every repo")
    listing.set_defaults(run=cmd_list)

    repos = commands.add_parser("repos", help="every repo with a board")
    repos.set_defaults(run=cmd_repos, repo=None)
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
