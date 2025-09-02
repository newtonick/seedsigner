import re
import subprocess
import sys
from pathlib import Path

POT = Path("l10n/messages.pot")


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def strip_metadata(lines):
    # Removes lines starting with the POT-Creation-Date or Generated-By
    meta_re = re.compile(r'^"(POT-Creation-Date|Generated-By):')
    return [line for line in lines if not meta_re.match(line)]


def main():
    if not POT.exists():
        print(f"{POT} does not exist. Make sure that your local repo has fetched the `seedsigner-translations` submodule.")
        sys.exit(1)

    original = POT.read_text().splitlines(keepends=True)

    res = run("python3 setup.py extract_messages")
    if res.returncode != 0:
        print(f"Error running extract_messages:\n\n{res.stderr}")
        sys.exit(1)

    new = POT.read_text().splitlines(keepends=True)

    if strip_metadata(original) == strip_metadata(new):
        print("All strings are up to date.")
        POT.write_text("".join(original))
        sys.exit(0)


if __name__ == "__main__":
    main()
