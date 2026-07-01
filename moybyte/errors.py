"""Moybyte error types.

This module avoids external dependencies so the public runtime stays easy to
port to a future device runtime.
"""

import traceback


class MoybyteError(Exception):
    """Base Moybyte exception."""


class ManifestError(MoybyteError):
    """Raised when a project manifest is invalid."""


class PermissionDenied(MoybyteError):
    """Raised when a project uses a disabled capability."""


class FriendlyError:
    def __init__(
        self,
        error_type,
        title,
        message,
        file=None,
        line=None,
        hint=None,
        raw_traceback=None,
    ):
        self.type = error_type
        self.title = title
        self.message = message
        self.file = file
        self.line = line
        self.hint = hint
        self.raw_traceback = raw_traceback

    def to_dict(self):
        return {
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "hint": self.hint,
            "raw_traceback": self.raw_traceback,
        }

    def __str__(self):
        location = ""
        if self.file is not None:
            location = self.file
            if self.line is not None:
                location += ":" + str(self.line)
            location = " (" + location + ")"
        return self.title + location + ": " + self.message


class MoybyteRuntimeError(MoybyteError):
    def __init__(self, friendly_error):
        self.friendly_error = friendly_error
        super().__init__(str(friendly_error))


def friendly_from_exception(exc, project_file="main.py"):
    raw = traceback.format_exc()
    line = None
    file_name = project_file
    tb = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_filename:
            file_name = code.co_filename
        line = tb.tb_lineno
        tb = tb.tb_next

    hint = None
    message = str(exc)
    if isinstance(exc, NameError):
        hint = "Check whether a name is misspelled or was created before use."
    elif isinstance(exc, PermissionDenied):
        hint = "Enable the capability in the project manifest to use this API."

    return FriendlyError(
        "MoybyteRuntimeError",
        "Your project crashed",
        message,
        file=file_name,
        line=line,
        hint=hint,
        raw_traceback=raw,
    )
