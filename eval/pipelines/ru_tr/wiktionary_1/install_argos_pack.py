"""
Download and install an Argos language pack for a given src→tgt pair.

Argos only publishes packs that involve English (e.g. ru→en, en→tr).
Pass exactly two codes — if neither is English, install both pivot legs.

    python -m eval.install_argos_pack ru tr
    # installs ru→en and en→tr automatically
"""

from __future__ import annotations

import sys

import argostranslate.package
import argostranslate.translate

_PIVOT = "en"


def _install_pair(available, src: str, tgt: str) -> bool:
    pkg = next(
        (p for p in available if p.from_code == src and p.to_code == tgt),
        None,
    )
    if pkg is None:
        return False
    print(f"Downloading {src}→{tgt} …")
    path = pkg.download()
    print("Installing …")
    argostranslate.package.install_from_path(path)
    print(f"  ✓  {src}→{tgt} installed.")
    return True


def install(src: str, tgt: str) -> None:
    print("Updating Argos package index …")
    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()

    # Try direct pair first
    if _install_pair(available, src, tgt):
        return

    # Fall back to pivot legs
    print(f"No direct {src}→{tgt} package. Trying pivot: {src}→{_PIVOT}→{tgt}")
    ok1 = _install_pair(available, src, _PIVOT)
    ok2 = _install_pair(available, _PIVOT, tgt)

    if ok1 or ok2:
        if not ok1:
            print(f"  (skipped {src}→{_PIVOT}: already installed or not found)")
        if not ok2:
            print(f"  (skipped {_PIVOT}→{tgt}: already installed or not found)")
        print(f"\nDone. Use '{src}→{_PIVOT}→{tgt}' pivot for translation.")
        return

    print(f"ERROR: No packages found for {src}→{tgt} (direct or via {_PIVOT}).")
    print("Available packages:")
    for p in sorted(available, key=lambda x: (x.from_code, x.to_code)):
        print(f"  {p.from_code} → {p.to_code}")
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m eval.install_argos_pack <src> <tgt>")
        sys.exit(1)
    install(sys.argv[1], sys.argv[2])
