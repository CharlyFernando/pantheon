"""Where Pantheon keeps its files and how it reads and writes them.

Every repo has a board directory, `<home>/boards/<repo>/`, holding one
`ITEM.md` per item and `ITEM.plan` next to a plan. The repo is identified by
its git common dir, so every worktree of a repo shares its board; `.repo` in
the board directory records that common dir, and a second repo with the same
name is refused instead of mixing boards.

An item file is a `key: value` header between `---` lines followed by an
append-only history, one `## turn N` section per post. Writes go to a
temporary file renamed over the target, so a reader never sees half a post,
and writers of one repo serialize on `.lock`.
"""

import contextlib
import fcntl
import os
import re
import subprocess
import tomllib

HEADER_KEYS = (
    "item",
    "repo",
    "kind",
    "title",
    "turn",
    "next",
    "holder",
    "round",
    "max_rounds",
    "branch",
    "worktree",
    "forge",
    "mr",
    "commit",
    "verdict",
    "findings",
    "reason",
    "plan",
    "step",
    "plan_version",
    "approved_version",
    "created",
    "updated",
)

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def home():
    """Pantheon's data directory: PANTHEON_HOME or ~/.local/share/pantheon."""
    override = os.environ.get("PANTHEON_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data, "pantheon")


def config_path():
    """The config file: PANTHEON_CONFIG or ~/.config/pantheon/config.toml."""
    override = os.environ.get("PANTHEON_CONFIG")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "pantheon", "config.toml")


def load_config():
    """The parsed config, or {} when there is none."""
    try:
        with open(config_path(), "rb") as source:
            return tomllib.load(source)
    except FileNotFoundError:
        return {}


def boards_root():
    return os.path.join(home(), "boards")


def check_name(kind, value):
    if not value or not NAME.match(value):
        raise ValueError(f"invalid {kind} name {value!r}")
    return value


def git(cwd, *args):
    """Stdout of a git command run in `cwd`, or None when it fails."""
    try:
        result = subprocess.run(
            ("git", *args), cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def common_dir(cwd=None):
    """The absolute git common dir of the repo around `cwd`, or None outside git."""
    return git(cwd or os.getcwd(), "rev-parse", "--path-format=absolute", "--git-common-dir")


def slug_of(common):
    """The repo name of a git common dir: `.../myrepo/.git` and `.../myrepo.git` give `myrepo`."""
    path = os.path.normpath(common)
    base = os.path.basename(path)
    if base == ".git":
        base = os.path.basename(os.path.dirname(path))
    elif base.endswith(".git"):
        base = base[: -len(".git")]
    return base


def forge_of(common):
    """`gitlab`, `github` or "" for the origin remote of the repo, by its host.

    github.com and gitlab.com are known; any other host is looked up in the
    `[forges]` table of the config (`"code.example.org" = "gitlab"`).
    """
    url = git(common, "--git-dir", common, "remote", "get-url", "origin") or ""
    match = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^:/]+)", url)
    if not match:
        return ""
    host = match.group(1).lower()
    known = {"github.com": "github", "gitlab.com": "gitlab"}
    known.update({k.lower(): v for k, v in load_config().get("forges", {}).items()})
    return known.get(host, "")


def current_repo(explicit=None):
    """(slug, common dir) of the repo to act on, or (None, None) when there is none.

    An explicit `--repo` only names the board: the git dir is still the one
    around the current directory, if any, so a repo whose name clashes with
    another gets its own board under a name of its choosing.
    """
    common = common_dir()
    if explicit:
        return check_name("repo", explicit), common
    if not common:
        return None, None
    return slug_of(common), common


def repo_dir(slug, common=None):
    """The board directory of `slug`, created on first use and bound to `common`."""
    path = os.path.join(boards_root(), check_name("repo", slug))
    os.makedirs(path, exist_ok=True)
    marker = os.path.join(path, ".repo")
    if common:
        try:
            with open(marker, encoding="utf-8") as source:
                bound = source.read().strip()
        except FileNotFoundError:
            write_text(marker, common + "\n")
            bound = common
        if os.path.normpath(bound) != os.path.normpath(common):
            raise ValueError(
                f"the board {slug!r} belongs to {bound}, not {common}; "
                "name this repo with --repo"
            )
    return path


def repos():
    """Every repo that has a board, by name."""
    try:
        names = sorted(os.listdir(boards_root()))
    except FileNotFoundError:
        return []
    return [n for n in names if NAME.match(n) and os.path.isdir(os.path.join(boards_root(), n))]


def item_path(slug, item):
    return os.path.join(repo_dir(slug), f"{check_name('item', item)}.md")


def plan_path(slug, item):
    return item_path(slug, item)[: -len(".md")] + ".plan"


def write_text(path, text):
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as target:
        target.write(text)
    os.replace(temporary, path)


@contextlib.contextmanager
def locked(slug):
    """Hold the write lock of a repo's board."""
    with open(os.path.join(repo_dir(slug), ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def parse(text):
    """Split an item file into its header dict and its history text."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("the item does not start with a --- header")
    header = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return header, "".join(lines[index + 1 :])
        key, sep, value = line.partition(":")
        if sep:
            header[key.strip()] = value.strip()
    raise ValueError("the item header is not closed")


def render(header, history):
    keys = [k for k in HEADER_KEYS if k in header] + sorted(set(header) - set(HEADER_KEYS))
    body = "".join(f"{key}: {header[key]}\n" for key in keys)
    return f"---\n{body}---\n{history}"


def read(slug, item):
    with open(item_path(slug, item), encoding="utf-8") as source:
        return parse(source.read())


def write(slug, item, header, history):
    write_text(item_path(slug, item), render(header, history))


def read_plan(slug, item):
    with open(plan_path(slug, item), encoding="utf-8") as source:
        return source.read().strip()


def items(slugs):
    """Every readable item of the given repos as (repo, name, header, history).

    Hidden and temporary files are left out, and so is a file that cannot be
    read or parsed right now: a reader never fails on the board's state.
    """
    found = []
    for slug in slugs:
        directory = os.path.join(boards_root(), slug)
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if name.startswith(".") or not name.endswith(".md"):
                continue
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as source:
                    header, history = parse(source.read())
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            found.append((slug, name[: -len(".md")], header, history))
    return found
