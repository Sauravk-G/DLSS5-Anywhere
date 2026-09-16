"""
Starred games.

A library of several hundred titles is a scroll bar, and the two or three games somebody
is actually modding this week are somewhere in the middle of it. Starring them puts those
few at the top and behind their own filter.

Kept separate from `CustomLibrary` even though both are small JSON files in the same
directory, because they answer different questions. The custom library decides which games
*exist* as far as this tool is concerned; this decides which of them the user cares about
right now. A game can be starred whether it was discovered by a scanner or added by hand,
and un-starring one must never remove it from the library.
"""

import json
import os
from pathlib import Path
from typing import Iterable, List, Set

from ..config import LIBRARY_DIR

FAVOURITES_FILE = LIBRARY_DIR / "favourites.json"

_SCHEMA_VERSION = 1


def _key(exe_path: Path | str) -> str:
    """The identity of a game: its path, compared the way Windows compares paths.

    Matches `custom_library._key` deliberately - a starred game and a manually added one
    have to agree on what counts as the same executable, or a game added by hand can be
    starred twice under two spellings of the same path.
    """
    return os.path.normcase(os.path.normpath(str(exe_path)))


class Favourites:
    """The set of starred executables, persisted next to the app."""

    @classmethod
    def load(cls) -> Set[str]:
        """Every starred path, as comparison keys.

        A set rather than a list: the only questions ever asked of this are "is this game
        starred" and "how many are", both of which a list answers slowly and a set answers
        at once. The library view asks the first one for every visible row on every repaint.
        """
        try:
            raw = json.loads(FAVOURITES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        if isinstance(raw, dict):
            raw = raw.get("games", [])
        if not isinstance(raw, list):
            return set()
        return {_key(entry) for entry in raw if entry}

    @classmethod
    def paths(cls) -> List[Path]:
        """The starred paths as they were stored, for anything that needs to show them."""
        try:
            raw = json.loads(FAVOURITES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if isinstance(raw, dict):
            raw = raw.get("games", [])
        return [Path(str(entry)) for entry in raw if entry] if isinstance(raw, list) else []

    @classmethod
    def contains(cls, exe_path: Path | str) -> bool:
        return _key(exe_path) in cls.load()

    @classmethod
    def toggle(cls, exe_path: Path | str) -> bool:
        """Star or un-star one game. Returns whether it is starred afterwards."""
        target = _key(exe_path)
        stored = cls.paths()
        remaining = [p for p in stored if _key(p) != target]
        if len(remaining) == len(stored):
            remaining.append(Path(str(exe_path)))
            cls._write(remaining)
            return True
        cls._write(remaining)
        return False

    @classmethod
    def add(cls, exe_path: Path | str) -> None:
        stored = cls.paths()
        if not any(_key(p) == _key(exe_path) for p in stored):
            stored.append(Path(str(exe_path)))
            cls._write(stored)

    @classmethod
    def remove(cls, exe_path: Path | str) -> bool:
        target = _key(exe_path)
        stored = cls.paths()
        remaining = [p for p in stored if _key(p) != target]
        if len(remaining) == len(stored):
            return False
        cls._write(remaining)
        return True

    @classmethod
    def prune(cls, known: Iterable[Path | str]) -> int:
        """Drop stars for games that are no longer in the library at all.

        Not called automatically. A game missing from one scan is usually a drive that is
        not plugged in, and quietly forgetting somebody's stars because a USB disk was
        asleep is the same mistake as deleting their manually added entries.
        """
        alive = {_key(p) for p in known}
        stored = cls.paths()
        remaining = [p for p in stored if _key(p) in alive]
        dropped = len(stored) - len(remaining)
        if dropped:
            cls._write(remaining)
        return dropped

    # ------------------------------------------------------------------
    @classmethod
    def _write(cls, paths: List[Path]) -> None:
        FAVOURITES_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": _SCHEMA_VERSION, "games": [str(p) for p in paths]}, indent=2
        )
        # Written beside the target and moved into place, so an interrupted write leaves
        # the previous list intact rather than a truncated file that reads as empty.
        temp = FAVOURITES_FILE.with_suffix(".json.tmp")
        temp.write_text(payload, encoding="utf-8")
        temp.replace(FAVOURITES_FILE)
