"""
The games the user added by hand.

Scanning finds what the launchers know about. It does not find an emulator, a portable
build, a GOG installer's output dropped somewhere unusual, or anything else that arrived
without a manifest - and those are exactly the cases this tool exists for, because a game
that never shipped with DLSS is often a game that never shipped with a launcher either.

So there is a second source of games alongside the scanners: a list the user maintains.
The important thing about it is that it is written to disk. A manual addition that lives
only in memory is worse than no manual addition at all - the user does the work of finding
the executable, sees it appear, and finds it gone the next time the app opens, with no
indication that anything was ever saved.

Entries are keyed by executable path, case-insensitively and without touching the disk to
resolve it: `resolve()` blocks on a disconnected network drive, and an entry on a drive
that is currently unplugged must still be *listed* - marked as missing, not deleted. The
user decides when to remove something; a drive being unmounted is not that decision.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import CUSTOM_GAMES_FILE
from .library_scanner import DiscoveredGame

# Folder names that say where a binary sits rather than what it is, so a game in
# `RPCS3\bin\rpcs3.exe` is not offered to the user as "bin".
_GENERIC_DIRS = {
    "bin", "bin64", "binaries", "win64", "win32", "x64", "x86", "release", "debug",
    "game", "games", "build", "builds", "app", "application", "program", "programs",
    "retail", "shipping", "redist", "data", "files",
}

_SCHEMA_VERSION = 1


def _key(exe_path: Path | str) -> str:
    """The identity of an entry: its path, compared the way Windows compares paths.

    Deliberately does not call `resolve()`. Resolving reaches the filesystem, which blocks
    for as long as the OS takes to give up on a disconnected drive, and returns something
    different once a junction is followed - so two spellings of one path would stop
    matching precisely when the drive came back.
    """
    return os.path.normcase(os.path.normpath(str(exe_path)))


def suggest_name(exe_path: Path | str) -> str:
    """A first guess at what to call this game.

    The folder is usually a better name than the executable - "Metro 2033 Redux" against
    "metro.exe" - so it is preferred, unless the folder only says where the binary lives,
    in which case the search walks up until it finds something that names a thing.
    """
    exe = Path(exe_path)
    for parent in exe.parents:
        name = parent.name
        if not name or name.endswith(":\\") or name.endswith(":"):
            break                                    # reached the drive root
        if name.lower() not in _GENERIC_DIRS:
            return _prettify(name)
    return _prettify(exe.stem)


def _prettify(raw: str) -> str:
    """Turn a folder or file name into something meant to be read.

    Left alone if it already contains a capital letter and a space, on the grounds that
    whoever named it did so on purpose; title-casing "RPCS3" into "Rpcs3" is a downgrade.
    """
    cleaned = re.sub(r"[_\-]+", " ", raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return raw
    if any(c.isupper() for c in cleaned):
        return cleaned
    return cleaned.title()


@dataclass(frozen=True)
class CustomEntry:
    """One game the user added, as it is stored."""

    name: str
    exe_path: Path
    install_dir: Path
    added: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "exe_path": str(self.exe_path),
            "install_dir": str(self.install_dir),
            "added": self.added,
        }

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> Optional["CustomEntry"]:
        try:
            exe = Path(str(raw["exe_path"]))
        except (KeyError, TypeError, ValueError):
            return None
        name = str(raw.get("name") or "").strip() or suggest_name(exe)
        install = raw.get("install_dir")
        return cls(
            name=name,
            exe_path=exe,
            install_dir=Path(str(install)) if install else exe.parent,
            added=str(raw.get("added") or ""),
        )


class CustomLibrary:
    """The user's own list of games, persisted next to the app."""

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    @classmethod
    def entries(cls) -> List[CustomEntry]:
        """Every stored entry, in the order it was added."""
        raw = cls._read()
        out: List[CustomEntry] = []
        seen: set = set()
        for item in raw:
            entry = CustomEntry.from_json(item) if isinstance(item, dict) else None
            if entry is None:
                continue
            key = _key(entry.exe_path)
            if key in seen:
                continue
            seen.add(key)
            out.append(entry)
        return out

    @classmethod
    def load(cls) -> List[DiscoveredGame]:
        """The stored entries as games the rest of the app can use.

        `missing` is decided here, once per entry, rather than by whatever draws the row:
        the list is repainted continuously while it scrolls and a filesystem check per
        painted row would put a stat call in the middle of every frame.
        """
        games: List[DiscoveredGame] = []
        for entry in cls.entries():
            exists = False
            try:
                exists = entry.exe_path.is_file()
            except OSError:
                pass        # an unreachable drive answers this way; treat it as missing
            games.append(DiscoveredGame(
                name=entry.name,
                exe_path=entry.exe_path,
                install_dir=entry.install_dir,
                source="Custom",
                missing=not exists,
            ))
        return games

    @classmethod
    def contains(cls, exe_path: Path | str) -> bool:
        target = _key(exe_path)
        return any(_key(e.exe_path) == target for e in cls.entries())

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    @classmethod
    def add(cls, exe_path: Path | str, name: Optional[str] = None,
            install_dir: Optional[Path | str] = None) -> Tuple[bool, str]:
        """Add one executable. Returns (added, message).

        Refusing a duplicate is not a failure the user needs to act on, but they do need
        to be told, or adding the same game twice looks like the button doing nothing.
        """
        exe = Path(str(exe_path)).expanduser()
        if exe.suffix.lower() != ".exe":
            return False, f"{exe.name} is not an .exe."
        if not exe.is_file():
            return False, f"No executable at:\n{exe}"

        entries = cls.entries()
        target = _key(exe)
        if any(_key(e.exe_path) == target for e in entries):
            return False, f"{exe.name} is already in the library."

        entries.append(CustomEntry(
            name=(name or "").strip() or suggest_name(exe),
            exe_path=exe,
            install_dir=Path(str(install_dir)) if install_dir else exe.parent,
            added=datetime.now().isoformat(timespec="seconds"),
        ))
        cls._write(entries)
        return True, f"Added {exe.name} to the library."

    @classmethod
    def add_many(cls, games: Iterable[DiscoveredGame]) -> int:
        """Store a batch - a folder scan's results. Returns how many were new.

        One write for the whole batch rather than one per game, so a scan that turns up
        forty folders does not rewrite the file forty times.
        """
        entries = cls.entries()
        known = {_key(e.exe_path) for e in entries}
        added = 0
        for game in games:
            key = _key(game.exe_path)
            if key in known:
                continue
            known.add(key)
            entries.append(CustomEntry(
                # Tidied the same way a hand-added name is: a folder scan names entries
                # after their directory, and "dolphin-emu" is a path, not a title.
                name=_prettify(game.name),
                exe_path=Path(game.exe_path),
                install_dir=Path(game.install_dir),
                added=datetime.now().isoformat(timespec="seconds"),
            ))
            added += 1
        if added:
            cls._write(entries)
        return added

    @classmethod
    def remove(cls, exe_path: Path | str) -> bool:
        """Forget one entry. Returns whether there was one to forget."""
        target = _key(exe_path)
        entries = cls.entries()
        kept = [e for e in entries if _key(e.exe_path) != target]
        if len(kept) == len(entries):
            return False
        cls._write(kept)
        return True

    @classmethod
    def rename(cls, exe_path: Path | str, name: str) -> bool:
        """Give an entry a different name."""
        clean = name.strip()
        if not clean:
            return False
        target = _key(exe_path)
        entries = cls.entries()
        updated = [
            CustomEntry(clean, e.exe_path, e.install_dir, e.added)
            if _key(e.exe_path) == target else e
            for e in entries
        ]
        if updated == entries:
            return False
        cls._write(updated)
        return True

    # ------------------------------------------------------------------
    # The file
    # ------------------------------------------------------------------
    @classmethod
    def _read(cls) -> List[Any]:
        if not CUSTOM_GAMES_FILE.exists():
            return []
        try:
            data = json.loads(CUSTOM_GAMES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Unreadable or not JSON. Starting over is the only way forward, but the file
            # is the user's list of games and is moved aside rather than overwritten -
            # a hand-editable file will occasionally be hand-broken, and losing the whole
            # list to a stray comma is not a reasonable outcome.
            cls._quarantine()
            return []
        if isinstance(data, dict):
            games = data.get("games")
            return games if isinstance(games, list) else []
        return data if isinstance(data, list) else []

    @classmethod
    def _quarantine(cls) -> None:
        try:
            CUSTOM_GAMES_FILE.replace(CUSTOM_GAMES_FILE.with_suffix(".json.corrupt"))
        except OSError:
            pass

    @classmethod
    def _write(cls, entries: List[CustomEntry]) -> None:
        CUSTOM_GAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": _SCHEMA_VERSION, "games": [e.to_json() for e in entries]},
            indent=2,
        )
        # Written beside the target and moved into place, so an interrupted write leaves
        # the previous list intact instead of a half-written file that reads as empty.
        temp = CUSTOM_GAMES_FILE.with_suffix(".json.tmp")
        temp.write_text(payload, encoding="utf-8")
        temp.replace(CUSTOM_GAMES_FILE)
