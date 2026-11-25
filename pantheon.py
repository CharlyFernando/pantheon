"""Entry point: python3 -I ~/.agents/skills/pantheon/pantheon.py <command> ...

`-I` leaves the script's directory out of sys.path, so the package
directory is added from this file's own location.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib"))

from pantheon.cli import main

if __name__ == "__main__":
    sys.exit(main())
