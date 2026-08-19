"""Comprueba que lo descargado cuadra con el manifiesto, especialidad a especialidad.

Cuenta los ficheros del disco y los contrasta con lo que el manifiesto dice que
deberia haber. Un año puede parecer terminado y faltarle documentos: la descarga
acaba igual y nadie se entera hasta juntar el corpus entre los tres.

Usa el manifiesto CSV y no el Excel: leer el .xlsx son dos minutos y aqui no
hace falta nada de lo que trae de mas.

Uso:
    python3 verificar.py              # todos los años que tengan algo
    python3 verificar.py 2025
"""

import os
import sys
from pathlib import Path

import pandas as pd

BASE = Path(os.environ.get("CORPUS_BASE",
                           "/run/media/charen/charen/corpus-corte-suprema"))
MANIFIESTO = Path(os.environ.get(
    "CORPUS_MANIFIESTO",
    "/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/manifiesto-pdfs.csv"))


def main():
    m = pd.read_csv(MANIFIESTO, usecols=["Anio", "Especialidad", "ruta", "ruta_md"])
    anios = [int(sys.argv[1])] if len(sys.argv) > 1 else sorted(
        m["Anio"].unique(), reverse=True)

    pdfs, mds = BASE / "pdfs", BASE / "markdown"
    print(f"base: {BASE}\n")
    algo = False

    for anio in anios:
        sub = m[m["Anio"] == anio]
        # Se comprueba fichero a fichero, no contando carpetas: asi un PDF con
        # nombre raro o en la carpeta equivocada se detecta en vez de cuadrar.
        hay_pdf = [(pdfs / r).exists() for r in sub["ruta"]]
        hay_md = [(mds / r).exists() for r in sub["ruta_md"]]
        n_pdf, n_md = sum(hay_pdf), sum(hay_md)
        if not n_pdf and not n_md:
            continue
        algo = True

        falta_pdf, falta_md = len(sub) - n_pdf, len(sub) - n_md
        estado = ("COMPLETO" if not falta_pdf and not falta_md
                  else f"faltan {falta_pdf} pdf / {falta_md} md")
        print(f"{anio}  {len(sub):,} documentos  ->  {estado}")
        print(f"{'  especialidad':<38}{'manif':>7}{'pdfs':>7}{'md':>7}  ")

        sub = sub.assign(_pdf=hay_pdf, _md=hay_md)
        for esp, g in sub.groupby("Especialidad"):
            p, d = int(g._pdf.sum()), int(g._md.sum())
            marca = "" if p == len(g) and d == len(g) else \
                    f"  <-- faltan {len(g)-p} pdf, {len(g)-d} md"
            print(f"  {esp:<36}{len(g):>7,}{p:>7,}{d:>7,}{marca}")
        print()

    if not algo:
        print("no hay nada descargado todavia")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
