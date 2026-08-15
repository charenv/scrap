"""Comprueba que lo cosechado no tenga huecos, y dice como taparlos.

Una especialidad puede parecer terminada y no estarlo: si el sitio devuelve una
pagina vacia con un 200 valido, el tramo se guardaba como completo con menos
filas dentro, y la reanudacion ya no volvia a pedir esas paginas. Este script
compara las paginas que hay en disco contra el rango que deberia haber y saca
las que faltan.

Uso:
    python3 auditar.py            # informe
    python3 auditar.py --borrar   # ademas borra los tramos con huecos

Tras --borrar, relanza tu cosecha de siempre: rehara solo esos tramos.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

from juris_excel import SALIDA

TRAMO = 25


def leer_especialidad(carpeta):
    """(paginas -> filas, uuids, filas_sin_link, tramos_incompletos)."""
    por_pagina = collections.Counter()
    uuids = set()
    sin_link = 0
    incompletos = []

    for fichero in sorted((carpeta / "tramos").glob("p*.json")):
        try:
            datos = json.loads(fichero.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            incompletos.append(fichero)
            continue
        if not datos.get("completo"):
            incompletos.append(fichero)
        for fila in datos["filas"]:
            por_pagina[fila["Pagina"]] += 1
            uuids.add(fila["uuid"])
            if not fila["Link Resolucion"]:
                sin_link += 1

    return por_pagina, uuids, sin_link, incompletos


def tramos_de_paginas(carpeta, paginas, ultima):
    """Los ficheros de tramo que contienen esas paginas."""
    inicios = sorted({((p - 1) // TRAMO) * TRAMO + 1 for p in paginas})
    return [carpeta / "tramos" / f"p{i:05d}-{min(i + TRAMO - 1, ultima):05d}.json"
            for i in inicios]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--borrar", action="store_true",
                    help="borra los tramos con huecos para que la cosecha los rehaga")
    args = ap.parse_args()

    if not SALIDA.exists():
        print("no hay carpeta salida/: aun no has cosechado nada")
        return 1

    a_borrar = []
    con_problemas = []
    total_filas = total_faltan = 0

    for carpeta in sorted(SALIDA.iterdir()):
        if not (carpeta / "tramos").is_dir():
            continue

        por_pagina, uuids, sin_link, incompletos = leer_especialidad(carpeta)
        if not por_pagina:
            continue

        ultima = max(por_pagina)
        faltan = sorted(set(range(1, ultima + 1)) - set(por_pagina))
        total_filas += len(uuids)
        total_faltan += len(faltan)

        estado = "OK" if not faltan and not incompletos and not sin_link else "REVISAR"
        if estado == "REVISAR":
            con_problemas.append(carpeta.name)
        print(f"\n{carpeta.name}  [{estado}]")
        print(f"  {len(uuids)} resoluciones unicas en {len(por_pagina)} paginas (hasta la {ultima})")

        if sin_link:
            print(f"  !! {sin_link} filas sin link de PDF")
        if incompletos:
            print(f"  !! {len(incompletos)} tramos marcados como incompletos")
            a_borrar.extend(incompletos)
        if faltan:
            tramos = tramos_de_paginas(carpeta, faltan, ultima)
            print(f"  !! faltan {len(faltan)} paginas, en {len(tramos)} tramos")
            print(f"     paginas: {faltan if len(faltan) <= 40 else str(faltan[:40]) + ' ...'}")
            a_borrar.extend(t for t in tramos if t.exists())

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {total_filas} resoluciones unicas")

    # El veredicto sale de si se encontraron huecos, no de cuantos ficheros hay
    # que borrar. Son dos preguntas distintas: un tramo con hueco puede no estar
    # en disco (lo borro una pasada anterior y la cosecha aun no lo ha rehecho),
    # y entonces no hay nada que borrar pero el hueco sigue ahi.
    if not con_problemas:
        print("Sin huecos. Esto esta completo.")
        return 0

    print(f"\nCon problemas: {', '.join(con_problemas)}")

    if not a_borrar:
        print("Los tramos que faltan ni siquiera estan en disco: o la cosecha")
        print("los esta rehaciendo ahora mismo, o hay que relanzarla.")
        return 1

    a_borrar = sorted(set(a_borrar))
    print(f"\n{len(a_borrar)} tramos hay que rehacer ({total_faltan} paginas):")
    for t in a_borrar:
        print(f"  {t}")

    if args.borrar:
        for t in a_borrar:
            t.unlink()
        print(f"\nBorrados. Relanza tu cosecha de siempre y los rehara.")
    else:
        # python3 en Linux, python.exe en Windows: que el mensaje sea copiable
        # tal cual en la maquina donde se esta ejecutando.
        piton = Path(sys.executable).name
        print(f"\nPara taparlos:  {piton} auditar.py --borrar")
        print(f"y despues relanza tu cosecha de siempre.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
