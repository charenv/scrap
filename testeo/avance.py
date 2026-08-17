"""Progreso del lote en curso. Funciona igual en Linux y en Windows.

Sustituye a avance.sh, que dependia de find, pgrep y du.

Uso:
    python3 avance.py            # el año que este a medias
    python3 avance.py 2019
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd

BASE = Path(os.environ.get("CORPUS_BASE",
                           "/run/media/charen/charen/corpus-corte-suprema"))
MANIFIESTO = Path(os.environ.get(
    "CORPUS_MANIFIESTO",
    "/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/manifiesto-pdfs.csv"))
RITMO = 660


def cuenta(raiz, anio, patron):
    if not raiz.exists():
        return 0, 0
    n = tam = 0
    for esp in raiz.iterdir():
        carpeta = esp / str(anio)
        if not carpeta.is_dir():
            continue
        for f in carpeta.glob(patron):
            n += 1
            tam += f.stat().st_size
    return n, tam


def main():
    m = pd.read_csv(MANIFIESTO, usecols=["Anio"])
    if len(sys.argv) > 1:
        anio = int(sys.argv[1])
    else:
        # el año a medias: tiene PDFs pero no todos
        anio = None
        for a in sorted(m["Anio"].unique(), reverse=True):
            n, _ = cuenta(BASE / "pdfs", a, "*.pdf")
            if 0 < n < int((m["Anio"] == a).sum()):
                anio = a
                break
        if anio is None:
            print("no hay ningun año a medias. Usa: python3 avance.py AÑO")
            return 1

    total = int((m["Anio"] == anio).sum())
    npdf, tam = cuenta(BASE / "pdfs", anio, "*.pdf")
    nmd, tmd = cuenta(BASE / "markdown", anio, "*.md")

    print(f"  {anio}:  {npdf:,}/{total:,} PDFs ({npdf/total*100:.1f}%)   "
          f"{nmd:,} en markdown")
    print(f"  {tam/1e9:.2f} GB de PDF, {tmd/1e6:.0f} MB de markdown")
    if npdf < total:
        # ritmo real: cuantos han aparecido en los ultimos 90 segundos
        print("  midiendo el ritmo...", flush=True)
        time.sleep(90)
        n2, _ = cuenta(BASE / "pdfs", anio, "*.pdf")
        r = (n2 - npdf) / 90 * 3600
        if r > 0:
            print(f"  {r:.0f}/h   quedan ~{(total - n2)/r:.1f} h")
        else:
            print(f"  sin avance en 90 s: la descarga esta parada o el portal va lento")
    else:
        print("  año completo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
