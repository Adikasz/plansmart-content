"""Pre-push safety check — a src/workers/main.py (vagy más belépési pont) teljes belső
import-fáját bejárja, és jelzi ha ezek közül BÁRMELYIK fájl untracked vagy uncommitted
git állapotban van.

Miért kell: kétszer történt meg, hogy egy main.py-ból importált modult a commit
kihagyott (a fájl csak lokálisan létezett), és a Railway deploy ImportError-ral
crash-loopolt éles környezetben (portrait cutout / reactions_bot esete). Ez a
script pontosan ezt a hibaosztályt fogja el push előtt.

Futtatás:
    python scripts/check_untracked_imports.py                     # alap: src/workers/main.py
    python scripts/check_untracked_imports.py --entry src/bots/telegram_bot.py

Kilépési kód: 0 ha minden import-fa fájl commitolva/tiszta, 1 ha van probléma.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENTRY = "src/workers/main.py"
INTERNAL_PREFIXES = ("src.", "scripts.")  # csak a projekt-belső importokat követjük


def _extract_module_names(file_path: Path) -> set[str]:
    """A fájlban lévő `import X` / `from X import Y` modulnevek (csak abszolút, level=0)."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"⚠️  Nem sikerult parse-olni: {file_path} ({exc})", file=sys.stderr)
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module)
                # "from src.bots import reactions_bot" — a reactions_bot lehet egy önálló
                # almodul-fájl (nem csak a src/bots csomag egy neve), ezt is jelöltként
                # próbáljuk feloldani.
                for alias in node.names:
                    if alias.name != "*":
                        modules.add(f"{node.module}.{alias.name}")
    return modules


def _resolve_module_to_path(module: str) -> Path | None:
    """Modulnév (pl. 'src.bots.reactions_bot') → repo-n belüli .py fájl útvonal, vagy None."""
    parts = module.split(".")
    as_module = PROJECT_ROOT.joinpath(*parts).with_suffix(".py")
    if as_module.is_file():
        return as_module
    as_package = PROJECT_ROOT.joinpath(*parts, "__init__.py")
    if as_package.is_file():
        return as_package
    return None


def walk_import_tree(entry: Path) -> set[Path]:
    """BFS a belépési pontból induló, projekt-belső (src./scripts.) import-fán."""
    visited: set[Path] = set()
    queue: list[Path] = [entry]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        for module in _extract_module_names(current):
            if not module.startswith(INTERNAL_PREFIXES):
                continue
            resolved = _resolve_module_to_path(module)
            if resolved is not None and resolved not in visited:
                queue.append(resolved)
    return visited


def _git_status_map() -> dict[str, str]:
    """relatív posix útvonal → 2-karakteres git status kód, minden untracked/piszkos fájlra."""
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    )
    status: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            continue
        code, path = line[:2], line[3:]
        if " -> " in path:  # rename/copy — az új útvonal számít
            path = path.split(" -> ", 1)[1]
        status[path.strip('"')] = code
    return status


_CODE_LABEL = {
    "??": "UNTRACKED — soha nem lett git add-olva",
    " M": "MODIFIED — vannak nem staged módosítások",
    "M ": "STAGED — staged, de nincs commitolva",
    "MM": "STAGED+MODIFIED — staged ÉS további nem staged módosítás is van",
    "A ": "ADDED — staged új fájl, de nincs commitolva",
    "AM": "ADDED+MODIFIED — staged, de azóta módosult",
}


def check(entry_rel: str) -> int:
    entry = PROJECT_ROOT / entry_rel
    if not entry.is_file():
        print(f"✖ Belépési pont nem található: {entry}", file=sys.stderr)
        return 1

    tree_files = walk_import_tree(entry)
    dirty = _git_status_map()

    problems: list[tuple[str, str]] = []
    for f in sorted(tree_files):
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        code = dirty.get(rel)
        if code:
            problems.append((rel, _CODE_LABEL.get(code, code)))

    print(f"Belépési pont: {entry_rel}")
    print(f"Projekt-belső import-fa mérete: {len(tree_files)} fájl")

    if not problems:
        print("✅ Minden import-fában lévő fájl commitolva és tiszta — push biztonságos.")
        return 0

    print(f"\n✖ {len(problems)} fájl az import-fában NEM commitolt / nem tiszta állapotú:\n")
    for rel, label in problems:
        print(f"  {rel}\n      → {label}")
    print(
        "\nEzek push után Railway-en ImportError-t / hibás futásidejű állapotot okozhatnak "
        "(lásd: portrait cutout + reactions_bot incidensek). Commitold őket a push előtt."
    )
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--entry", default=DEFAULT_ENTRY,
        help=f"belépési pont, repo-gyökérhez relatív (alap: {DEFAULT_ENTRY})",
    )
    args = ap.parse_args()
    return check(args.entry)


if __name__ == "__main__":
    raise SystemExit(main())
