"""The failures a caller can act on.

Kept apart from `io` so that anything may raise one without importing the seam
(which would be a cycle).
"""


class ScutchError(Exception):
    """Base for every failure a caller can act on."""


class Unanswered(ScutchError):
    """The double was asked more often than it was given answers."""


class Unapproved(ScutchError):
    """The effects about to happen are not the effects that were approved."""


class UnknownEffect(ScutchError):
    """An ``io`` kind outside the known set."""


class Failed(ScutchError):
    """An effect ran and the world refused: no route, non-zero exit, bad reply.

    Translated at the seam so an operation reads as ordinary code and a caller
    gets one sentence instead of a traceback from inside urllib or subprocess.
    """


class Missing(ScutchError):
    """A read named a file that is not there.

    Translated at the seam so an operation never has to guard a read with
    try/except, and a person sees a sentence rather than a traceback.
    """
