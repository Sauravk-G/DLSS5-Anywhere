# Contributing

Thanks for looking. This is a small project with a few rules that are not obvious from
reading the code, and one of them matters a great deal — please read that one at least.

## Never commit `components/` or `builds/`

Both are in `.gitignore`. Keep them there, and do not add them with `git add -f`.

`components/` holds NVIDIA's `nvngx_dlssnr.dll` and `nvngx_dlss.dll` runtimes, the RenoDX
DLSS 5 add-on, and the wrappers this tool downloads — roughly a gigabyte of other people's
binaries. This repository is an installer and a configuration generator: it redistributes
none of that, and the user supplies their own. Committing those files would make the
README untrue, put third-party binaries under this project's MIT licence, and redistribute
NVIDIA's runtimes without the right to do so.

`builds/` holds staged output for real games on a real machine. The folder names alone say
which games the developer owns.

Also gitignored, for the same reason: `profiles/*.json` (regenerated from
`BUILTIN_PRESETS` on first run) and `library/` (the user's own list of games — their
paths, on their PC).

**Before pushing, check what you are about to send:**

```bash
git status --short
```

If anything under `components/`, `builds/`, `profiles/` or `library/` appears, stop.

Personal paths do not belong in tracked files either. No `C:\Users\<you>\...`, no real
Steam or GOG library paths, in code, comments, docs or tests.

## Running the tests

```bash
python -m unittest discover -s tests
```

63 tests, about a second, no display needed — the suite imports nothing from Qt.

**They must pass on a clean checkout**, with `components/` empty. That is what every new
contributor and every CI run has. Two tests used to assert that the Vulkan interop layer
gets staged, which only happens if that component has been downloaded; they passed for
whoever had already pressed Download and failed for everyone else. If a test needs a
component, stage a fake one — `TestInstaller.downloaded_vulkan_layer` shows the pattern.

One test is skipped by default because it needs a real game binary. To run it:

```bash
set DLSS5_TEST_EXE=D:\Games\Something\game.exe
python -m unittest discover -s tests
```

## Keep `core/` free of the UI

```
dlss5_anywhere/core/  +  config.py  +  cli.py     ~6,700 lines   no UI imports
dlss5_anywhere/qtgui/                             ~4,800 lines   PySide6
tests/                                               ~1,200 lines   headless
```

Detection, strategy, configuration generation, downloading and installation know nothing
about how they are displayed. That is not decoration: the interface was rewritten from
Tkinter to PySide6 and the entire logic layer came across untouched, along with every
test. Keeping the boundary means the next such change stays cheap, and it is why the CLI
runs without Qt installed at all.

So: no `PySide6` import under `core/`, and no game-detection or install logic inside a
view. A view collects input, hands it to `core`, and displays what comes back.

## Setup

Python 3.10 or newer on Windows.

```bash
pip install -r requirements.txt
python main.py            # GUI
python main.py detect "D:\Games\Something\game.exe"   # CLI
```

PySide6 is the only dependency the GUI needs and by far the largest (~250 MB). The CLI
imports it lazily, so command-line work does not load it.

## Long jobs

Anything that touches the disk or the network — a library scan, a PE read, a download —
runs off the GUI thread through `qtgui/workers.run_async`, which delivers the result back
on the GUI thread and drops it if the view has since been closed. Do not call `core`
directly from a button handler if it might block.

## Style

Match what is there. The codebase explains *why* a thing is done, not what the line does —
particularly where the obvious approach was tried and did not work. Those comments are the
record of what has already been ruled out; if you replace such a piece of code, replace
its explanation too rather than deleting it.

## Anti-cheat

This tool injects DLLs into game processes. The warnings in the interface, the confirmation
dialog and the README exist because a ban lands on the user and cannot be undone. Do not
weaken, bypass or auto-accept them, and do not add anything aimed at making an injection
harder to detect.

## Pull requests

Say what changed and why. If it fixes something, describe how it failed. Run the tests on
a clean checkout first, and mention anything you could not test — a GPU you do not have,
a game you could not try — rather than leaving it implied.
