class StaleParent(Exception):
    """propose(parent=...) did not match the current head."""


class InvariantError(Exception):
    """I1–I7 failed after a commit; process-fatal, no auto-repair."""


class WorldExists(Exception):
    """init() refused because <root>/.spaceten/ already exists."""


class WorldLocked(Exception):
    """Exclusive flock on .spaceten/lock is held. Fail immediately; do not wait."""


class TruncatedLog(Exception):
    """Last events.jsonl line is torn. Refuse load unless truncate_partial."""


class DirtySpace(Exception):
    """Act dest does not match digest and no staged file can finish the write."""
