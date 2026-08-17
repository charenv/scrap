"""Mide cuantas filas del corpus tienen el PDF cruzado con la ficha de otra.

El parser saca el uuid del enlace de descarga y los campos del payload JSON del
boton "Ver ficha". Son dos sitios distintos del mismo panel, y hasta ahora nadie
comprobaba que apuntasen a la misma resolucion. Este script vuelve a pedir una
muestra de paginas y compara los dos uuid panel a panel.

Solo lee: no descarga PDFs ni escribe nada en el sitio. Una peticion por pagina
de la muestra, con la misma pausa que usa cosecha.py.

Uso:
    python3 verificar_enlaces.py --paginas 40         # muestra al azar
    python3 verificar_enlaces.py --especialidad Penal --paginas 20
    python3 verificar_enlaces.py --pagina 1026 --especialidad Constitucional
"""

import argparse
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

import juris_parse as jp
from juris_client import ESPECIALIDADES, ClienteJuris


def uuids_de_la_pagina(html_texto):
    """(uuid_del_pdf, uuid_de_la_ficha, nro_expediente) por cada panel."""
    sopa = BeautifulSoup(html_texto, "html.parser")
    filas = []
    for panel in sopa.find_all("div", id=jp._ID_PANEL):
        enlace = panel.find("a", href=jp._HREF_DESCARGA)
        if not enlace:
            filas.append((None, None, None))  # panel sin PDF: se descarta al cosechar
            continue
        uuid_pdf = jp._HREF_DESCARGA.search(enlace["href"]).group(1)

        uuid_ficha = expediente = None
        ficha = panel.find("a", onclick=re.compile(r"\buuid\b"))
        if ficha:
            datos = jp._payload_a_dict(ficha.get("onclick", "")) or {}
            uuid_ficha = datos.get("uuid")
            expediente = datos.get("nroexp")
        filas.append((uuid_pdf, uuid_ficha, expediente))
    return filas


def revisar(cliente, especialidad, numero):
    html = cliente.pagina_con_reintentos(numero)
    resultado = Counter()
    problemas = []
    for uuid_pdf, uuid_ficha, expediente in uuids_de_la_pagina(html):
        if uuid_pdf is None:
            resultado["sin_pdf"] += 1
        elif uuid_ficha is None:
            resultado["ficha_ilegible"] += 1
        elif uuid_ficha == uuid_pdf:
            resultado["ok"] += 1
        else:
            resultado["descuadre"] += 1
            problemas.append((especialidad, numero, expediente, uuid_pdf, uuid_ficha))
    return resultado, problemas


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--especialidad", choices=sorted(ESPECIALIDADES))
    ap.add_argument("--paginas", type=int, default=30, help="tamaño de la muestra")
    ap.add_argument("--pagina", type=int, action="append", help="pagina concreta (repetible)")
    ap.add_argument("--pausa", type=float, default=2.0)
    ap.add_argument("--semilla", type=int, default=0)
    args = ap.parse_args()

    especialidades = [args.especialidad] if args.especialidad else sorted(ESPECIALIDADES)
    random.seed(args.semilla)

    total = Counter()
    problemas = []
    for especialidad in especialidades:
        cliente = ClienteJuris(especialidad=especialidad, pausa=args.pausa)
        primera = cliente.pagina_con_reintentos(1, primera=True)
        n = jp.num_paginas(primera) or 1

        if args.pagina:
            muestra = [p for p in args.pagina if p <= n]
        else:
            cuantas = max(1, args.paginas // len(especialidades))
            muestra = sorted(random.sample(range(1, n + 1), min(cuantas, n)))

        parcial = Counter()
        for numero in muestra:
            try:
                r, p = revisar(cliente, especialidad, numero)
            except Exception as e:
                print(f"  {especialidad} p{numero}: FALLO {type(e).__name__}: {e}")
                continue
            parcial += r
            problemas.extend(p)
        total += parcial
        revisadas = sum(parcial.values())
        print(f"{especialidad:36s} {len(muestra):4d} pag  {revisadas:5d} paneles  "
              f"ok={parcial['ok']:5d}  descuadre={parcial['descuadre']:4d}  "
              f"sin_pdf={parcial['sin_pdf']:4d}  ficha_ilegible={parcial['ficha_ilegible']:4d}")

    print("\nTOTAL")
    revisados = sum(total.values())
    for clave in ("ok", "descuadre", "sin_pdf", "ficha_ilegible"):
        pct = total[clave] / revisados * 100 if revisados else 0
        print(f"  {clave:16s} {total[clave]:6d}  ({pct:5.2f}%)")

    if problemas:
        print(f"\n{len(problemas)} PANELES CON EL PDF CRUZADO")
        for esp, pag, exp, upfd, uf in problemas[:40]:
            print(f"  {esp} p{pag}  exp={exp}  pdf={upfd}  ficha={uf}")
    else:
        print("\nNingun descuadre en la muestra: el uuid del PDF y el de la ficha coinciden.")

    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
