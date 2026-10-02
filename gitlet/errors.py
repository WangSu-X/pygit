"""Errors shared by command and storage layers."""


class GitletError(Exception):
    """An expected command failure, printed without a traceback by the CLI."""
