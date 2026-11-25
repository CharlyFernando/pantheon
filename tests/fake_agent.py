"""A stand-in for a headless agent, launched by dispatch in the tests.

argv: fake_agent.py launch|resume <conversation id> [--model M] <prompt>

It reads its role and item from the prompt, takes its turn through the real
CLI, and appends `mode conversation model role` to $FAKE_RECORD. A reviewer or
QA posts the verdicts listed in $FAKE_<ROLE> (comma separated, one per run,
the last one repeating).
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "pantheon.py")


def main():
    args = sys.argv[1:]
    mode, conversation = args[0], args[1]
    model = args[args.index("--model") + 1] if "--model" in args else ""
    text = args[-1]
    role = re.search(r"Eres el (\w+) del ítem", text).group(1)
    item = re.search(r"ítem (\S+) en Pantheon", text).group(1)
    record = os.environ["FAKE_RECORD"]
    with open(record, "a", encoding="utf-8") as target:
        target.write(f"{mode} {conversation} {model} {role} {os.getcwd()}\n")
    runs = sum(1 for line in open(record, encoding="utf-8") if f" {role} " in line)
    command = [sys.executable, "-I", SCRIPT, "post", item]
    if role == "coder":
        command += ["--commit", f"c{runs}", "-m", f"round {runs}"]
    else:
        verdicts = os.environ.get(f"FAKE_{role.upper()}", "APPROVED" if role == "reviewer" else "PASSED").split(",")
        verdict = verdicts[min(runs, len(verdicts)) - 1]
        command += ["--verdict", verdict, "-m", f"{role} says {verdict}"]
        if verdict in ("CHANGES_REQUESTED", "FAILED"):
            command += ["--findings", "fix"]
    sys.exit(subprocess.run(command).returncode)


if __name__ == "__main__":
    main()
