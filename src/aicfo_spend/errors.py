"""Errors the person running the tool can fix, worded to say how."""


class UserError(Exception):
    """A problem with the person's files or command, not a bug. The CLI prints it and exits 1."""


class InputError(UserError):
    """A saved file or a command argument the tool can't use."""
