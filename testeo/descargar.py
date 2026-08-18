"""Descarga los PDFs del manifiesto, reanudable y a prueba de cortes.

El servlet del PJ da 20 PDFs por ventana de ~60 segundos, y la cuota va por IP:
renovar la cookie no la esquiva (medido). Agotada la cuota responde 200 OK con
text/plain vacio en vez de un error, asi que un descargador que mire
r.status_code guardaria miles de ficheros de cero bytes creyendo que fue bien.
Aqui se valida la cabecera %PDF y se duerme lo que le quede a la ventana.

Ese techo son 1160 PDFs/hora por IP: el corpus entero, 209 179 documentos, son
unos 7,5 dias seguidos. Repartirlo entre varias IPs es lo unico que lo acorta.

Todo lo ya bajado se salta, asi que se puede cortar y retomar sin perder nada.
Los fallos quedan en fallos-<año>.csv para reclamarlos despues.

Uso:
    python3 descargar.py --destino /ruta/pdfs
    python3 descargar.py --destino /ruta/pdfs --especialidad Penal
    python3 descargar.py --destino /ruta/pdfs --anio 2024      # un lote de un año
    python3 descargar.py --destino /ruta/pdfs --desde 2017     # toda la fase 1
    python3 descargar.py --destino /ruta/pdfs --limite 45      # prueba corta
    python3 descargar.py --destino /ruta/pdfs --reintentar     # solo los fallidos
"""

import os

# Rutas configurables por variable de entorno, para que cada maquina del equipo
# use las suyas sin tocar el codigo:
#   export CORPUS_BASE=/ruta/al/usb/corpus-corte-suprema
#   export CORPUS_MANIFIESTO=/ruta/al/manifiesto-pdfs.csv
import argparse
import csv
import sys
import time
from pathlib import Path

import pandas as pd
import requests

MANIFIESTO = Path(os.environ.get(
    "CORPUS_MANIFIESTO",
    "/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/manifiesto-pdfs.csv"))
CABECERAS = {"User-Agent": "Mozilla/5.0"}
MINIMO = 1000  # bytes: por debajo de esto no es un PDF real aunque lo parezca

# Fallos seguidos antes de abandonar. Sin esto una caida de internet no para
# nada: cada documento gasta 4 intentos de hasta 90 s, se anota como fallo y se
# pasa al siguiente, asi toda la noche. Como todo es reanudable, parar y
# relanzar sale siempre mas barato que seguir arando en seco.
CORTE_FALLOS = 10


def sesion_nueva():
    s = requests.Session()
    s.headers.update(CABECERAS)
    return s


def ya_esta(ruta):
    """True si el fichero existe y es un PDF valido (no un corte guardado)."""
    if not ruta.exists() or ruta.stat().st_size < MINIMO:
        return False
    with ruta.open("rb") as fh:
        return fh.read(5) == b"%PDF-"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--destino", type=Path, required=True)
    ap.add_argument("--manifiesto", type=Path, default=MANIFIESTO)
    ap.add_argument("--especialidad")
    ap.add_argument("--anio", type=int, help="solo un año concreto")
    ap.add_argument("--desde", type=int, help="año minimo (incluido)")
    ap.add_argument("--hasta", type=int, help="año maximo (incluido)")
    ap.add_argument("--orden", choices=("reciente", "antiguo", "manifiesto"),
                    default="reciente",
                    help="por defecto empieza por lo mas nuevo: es lo que no necesita OCR")
    ap.add_argument("--cada", type=int, default=20, help="descargas por ventana (medido: 20)")
    ap.add_argument("--ventana", type=float, default=62.0, help="segundos de la ventana (medido: ~60)")
    ap.add_argument("--pausa", type=float, default=0.0, help="segundos entre descargas")
    ap.add_argument("--limite", type=int, help="parar tras N descargas (para probar)")
    ap.add_argument("--reintentar", action="store_true", help="solo los de fallos.csv")
    args = ap.parse_args()

    m = pd.read_csv(args.manifiesto)
    if args.especialidad:
        m = m[m["Especialidad"] == args.especialidad]
    if args.anio:
        m = m[m["Anio"] == args.anio]
    if args.desde:
        m = m[m["Anio"] >= args.desde]
    if args.hasta:
        m = m[m["Anio"] <= args.hasta]
    # De lo nuevo a lo viejo: 2017+ es texto nativo y no necesita OCR, asi que
    # bajarlo primero da corpus utilizable cuanto antes.
    if args.orden != "manifiesto":
        m = m.sort_values("Anio", ascending=args.orden == "antiguo", kind="stable")

    # Un fichero de fallos por año. Con --persona se encadenan varios y un
    # unico fallos.csv se reescribia al terminar cada uno, borrando los del
    # anterior; y si el año siguiente no fallaba, el bloque ni se ejecutaba y
    # los viejos sobrevivian: a veces se perdian y a veces no.
    fallos_f = args.destino / f"fallos{f'-{args.anio}' if args.anio else ''}.csv"
    ficheros = []

    if args.reintentar:
        # Se recogen los de todos los años: lo normal es reclamarlos de una vez
        # al acabar el reparto, sin acordarse de cual fallo en cual.
        ficheros = sorted(args.destino.glob("fallos*.csv"))
        if not ficheros:
            print(f"no hay ningun fallos*.csv en {args.destino}")
            return 0
        pendientes = set()
        for f in ficheros:
            try:
                pendientes |= set(pd.read_csv(f)["uuid"])
            except Exception as e:
                print(f"  no se pudo leer {f.name}: {type(e).__name__}")
        print(f"reintentando {len(pendientes):,} fallos de {len(ficheros)} "
              f"fichero(s): {', '.join(f.name for f in ficheros)}")
        m = m[m["uuid"].isin(pendientes)]

    total = len(m)
    if not total:
        print("el filtro no deja ningun PDF")
        return 1
    print(f"manifiesto: {total:,} PDFs | destino: {args.destino}")
    print(f"años {int(m['Anio'].min())}-{int(m['Anio'].max())} | orden: {args.orden}")
    print(f"cuota: {args.cada} PDFs por ventana de {args.ventana:.0f}s "
          f"-> {args.cada/args.ventana*3600:.0f}/hora\n")

    pendientes = [(f.uuid, f.url, args.destino / f.ruta) for f in m.itertuples()]
    pendientes = [(u, url, r) for u, url, r in pendientes if not ya_esta(r)]
    print(f"ya descargados: {total - len(pendientes):,} | quedan: {len(pendientes):,}\n")
    if not pendientes:
        print("nada que hacer")
        return 0
    if args.limite:
        pendientes = pendientes[: args.limite]

    sesion, en_ventana = sesion_nueva(), 0
    abre_ventana = time.monotonic()
    bajados = cortes = fallidos = seguidos = 0
    bytes_totales = 0
    inicio = time.monotonic()
    fallos = []

    for i, (uuid, url, ruta) in enumerate(pendientes, start=1):
        # El servidor da CADA descargas por ventana de VENTANA segundos y va por
        # IP: renovar la cookie no lo esquiva. Lo unico que funciona es agotar la
        # cuota rapido y dormir lo que le quede a la ventana.
        if en_ventana >= args.cada:
            resto = args.ventana - (time.monotonic() - abre_ventana)
            if resto > 0:
                time.sleep(resto)
            abre_ventana, en_ventana = time.monotonic(), 0
            sesion = sesion_nueva()

        cuerpo = None
        for intento in range(4):
            try:
                r = sesion.get(url, timeout=90)
                if r.content.startswith(b"%PDF") and len(r.content) >= MINIMO:
                    cuerpo = r.content
                    break
                # Cuota agotada antes de lo previsto: esperar la ventana entera.
                cortes += 1
                time.sleep(args.ventana)
                abre_ventana, en_ventana = time.monotonic(), 0
                sesion = sesion_nueva()
            except Exception:
                time.sleep(5 * (intento + 1))
                sesion = sesion_nueva()

        if cuerpo is None:
            fallidos += 1
            seguidos += 1
            fallos.append({"uuid": uuid, "url": url, "ruta": str(ruta)})
            print(f"  FALLO {uuid}", flush=True)
            if seguidos >= CORTE_FALLOS:
                print(f"\n{CORTE_FALLOS} fallos seguidos: se cayo la conexion o el "
                      f"portal no responde.\n"
                      f"  Paro aqui en vez de pasarme la noche sumando fallos.\n"
                      f"  Relanza el mismo comando cuando vuelva: lo descargado se "
                      f"conserva y solo pedira lo que falte.", flush=True)
                break
            continue
        seguidos = 0

        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(".parcial")
        temporal.write_bytes(cuerpo)
        temporal.replace(ruta)  # atomico: nunca queda un PDF a medias
        bajados += 1
        en_ventana += 1
        bytes_totales += len(cuerpo)
        time.sleep(args.pausa)

        if i % 100 == 0 or i == len(pendientes):
            transcurrido = time.monotonic() - inicio
            ritmo = i / transcurrido
            queda = (len(pendientes) - i) / ritmo if ritmo else 0
            print(f"  {i:,}/{len(pendientes):,}  {bajados:,} ok  {fallidos} fallos  "
                  f"{cortes} cortes  {bytes_totales/1e9:.1f} GB  "
                  f"{ritmo*3600:.0f}/h  quedan ~{queda/3600:.1f} h", flush=True)

    # Los listados viejos se retiran despues de correr, no antes: si el filtro
    # no dejaba nada o el proceso murio, se conservan intactos.
    if args.reintentar:
        for f in ficheros:
            f.unlink(missing_ok=True)
        fallos_f = args.destino / "fallos-reintento.csv"

    if fallos:
        args.destino.mkdir(parents=True, exist_ok=True)
        with fallos_f.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["uuid", "url", "ruta"])
            w.writeheader()
            w.writerows(fallos)
        print(f"\n{len(fallos)} fallos -> {fallos_f}  (python3 descargar.py --reintentar ...)")

    print(f"\nbajados {bajados:,} | fallos {fallidos} | cortes {cortes} | "
          f"{bytes_totales/1e9:.1f} GB en {(time.monotonic()-inicio)/3600:.1f} h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
