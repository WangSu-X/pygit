"""Argument checking and dispatch; all command behavior lives in Repository."""

import sys

from .repository import GitletError, Repository


COMMANDS = {
    "init": 0, "add": 1, "commit": 1, "log": 0,
    "global-log": 0, "find": 1, "status": 0, "checkout": None,
    "branch": 1, "rm-branch": 1, "reset": 1, "merge": 1,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if not args:
            raise GitletError("Please enter a command.")
        command, operands = args[0], args[1:]
        if command not in COMMANDS:
            raise GitletError("No command with that name exists.")
        if command == "checkout":
            valid = (len(operands) == 1
                     or (len(operands) == 2 and operands[0] == "--")
                     or (len(operands) == 3 and operands[1] == "--"))
            if not valid:
                raise GitletError("Incorrect operands.")
        elif len(operands) != COMMANDS[command]:
            raise GitletError("Incorrect operands.")
        repository = Repository()
        if command != "init":
            repository.require_initialized()
        if command == "checkout":
            if len(operands) == 1:
                repository.checkout_branch(operands[0])
            elif len(operands) == 2:
                repository.checkout_file(operands[1])
            else:
                repository.checkout_file(operands[2], operands[0])
        else:
            getattr(repository, command.replace("-", "_"))(*operands)
    except GitletError as error:
        print(error)
    # Match the course's System.exit(0), including specified user errors.
    return 0
