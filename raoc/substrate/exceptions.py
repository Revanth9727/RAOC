"""All custom exceptions for RAOC.

Every component raises one of these typed exceptions so the coordinator
can route errors correctly and update job status accordingly.
"""


class RaocError(Exception):
    """Base exception for all RAOC errors."""


class ScopeViolationError(RaocError):
    """Raised when a path outside the workspace is accessed."""


class CommandBlockedError(RaocError):
    """Raised when a command contains a blocked pattern."""


class FileTooLargeError(RaocError):
    """Raised when a file exceeds MAX_FILE_SIZE_CHARS."""


class FileNotFoundInWorkspaceError(RaocError):
    """Raised when the target file is not found in the workspace."""


class FileLockedError(RaocError):
    """Raised when the target file is locked by another process."""


class UnsupportedFileTypeError(RaocError):
    """Raised when a non-text file is passed to a text-only operation."""


class LLMError(RaocError):
    """Raised when a Claude API call fails."""


class IntakeError(RaocError):
    """Raised when intake cannot produce a valid TaskObject."""


class ExtractionError(RaocError):
    """Raised when text cannot be extracted from a file for rewriting."""


class ZipFileDetectedError(RaocError):
    """Raised when a ZIP file is detected that is not a DOCX archive.

    Carries the zip path and the list of filenames inside so the caller
    can ask the user which entry to extract and rewrite.
    """

    def __init__(self, path, contents: list):
        self.path = path
        self.contents = contents
        super().__init__(f"ZIP file detected: {path.name}")


class AmbiguousZoneError(RaocError):
    """Raised when a path matches two zone entries at equal specificity.

    Carries the path that triggered the tie so PolicyAgent can build
    a meaningful reason string for the user.
    """

    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Ambiguous zone for path: {path}")
