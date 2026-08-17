"""Procesa el corpus por lotes de un año, en el USB, y avisa cuando toca subir.

Cada lote hace el ciclo entero: descarga los PDFs de ese año, los convierte a
markdown y comprueba que no falte ninguno contra el manifiesto. Al terminar
avisa -en la terminal y con notificacion de escritorio- de que ese año esta
listo para Drive.

Cuando lo hayas subido, 'lote.py --subido 2026' borra los PDFs de ese año y
conserva el markdown, que pesa cien veces menos y es lo que alimenta el RAG.

El estado vive en estado.json, asi que se puede parar y retomar en cualquier
punto sin perder la cuenta de que lotes estan hechos y cuales ya subiste.

Uso:
    python3 lote.py --estado                 # que hay hecho y que falta
    python3 lote.py --anio 2026              # procesa un año entero
    python3 lote.py --persona joe            # todos los años asignados a joe
    python3 lote.py --anio 2026 --solo-md    # reconvierte sin volver a bajar
    python3 lote.py --subido 2026            # marca subido y libera los PDFs
"""

import os

# Rutas configurables por variable de entorno, para que cada maquina del equipo
# use las suyas sin tocar el codigo:
#   export CORPUS_BASE=/ruta/al/usb/corpus-corte-suprema
#   export CORPUS_MANIFIESTO=/ruta/al/manifiesto-pdfs.csv
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
BASE = Path(os.environ.get("CORPUS_BASE",
                          "/run/media/charen/charen/corpus-corte-suprema"))
MANIFIESTO = Path(os.environ.get(
    "CORPUS_MANIFIESTO",
    "/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/manifiesto-pdfs.csv"))

# KB por PDF segun la epoca, medidos sobre 109 documentos. Los de 2017 en
# adelante son texto nativo y pesan una septima parte de los escaneados.
PESO_KB = {"moderno": 307, "antiguo": 2194}
RITMO = 660  # PDFs/hora: medido sobre las 7 233 descargas de 2026

# Reparto de años entre las tres maquinas. El limite del portal es por IP, asi
# que cada persona descarga desde la suya y los tres avanzan en paralelo.
# Los años se asignaron buscando el reparto mas equilibrado posible: 66 342 /
# 68 347 / 67 258 documentos, un 3% de diferencia entre el que mas y el que
# menos. 2026 queda fuera porque ya esta hecho.
#
# Dentro de cada persona se procesa del mas nuevo al mas viejo: asi todo el
# mundo termina primero su parte de 2017-2025, que no necesita OCR, y el corpus
# es utilizable mucho antes de acabarlo todo.
REPARTO = {
    "charen": [2025, 2024, 2020, 2014, 2011, 2009, 2007],
    "joe":    [2023, 2021, 2019, 2015, 2013, 2010],
    "larijo": [2022, 2018, 2017, 2016, 2012, 2008, 2006, 2005,
               2004, 2003, 2002, 2001, 2000, 1999, 1998, 1982],
}


def epoca(anio):
    return "moderno" if anio >= 2017 else "antiguo"


def estado_path():
    return BASE / "estado.json"


def leer_estado():
    p = estado_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def guardar_estado(estado):
    BASE.mkdir(parents=True, exist_ok=True)
    estado_path().write_text(json.dumps(estado, indent=2, ensure_ascii=False),
                             encoding="utf-8")


def ya_subidos():
    """Años marcados como subidos: sus PDFs se borraron a proposito.

    Sin esto, encadenar el reparto despues de liberar espacio volveria a
    descargarlos enteros, porque el descargador solo ve que los ficheros no
    estan. Son horas de cuota del portal tiradas.
    """
    return {int(a) for a, v in leer_estado().items() if v.get("subido")}


def avisar(titulo, cuerpo):
    """Notificacion de escritorio. El equipo trabaja en Linux y en Windows, y un
    lote tarda horas: el aviso es lo que evita tener que vigilar la terminal."""
    print("\a", end="", flush=True)          # campana: funciona en todas partes
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[reflection.assembly]::LoadWithPartialName('System.Windows.Forms')"
                 ">$null; $n=New-Object System.Windows.Forms.NotifyIcon; "
                 "$n.Icon=[System.Drawing.SystemIcons]::Information; $n.Visible=$true; "
                 f"$n.ShowBalloonTip(10000,'{titulo}','{cuerpo}','Info')"],
                check=False, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            f'display notification "{cuerpo}" with title "{titulo}"'],
                           check=False, timeout=10)
        else:
            subprocess.run(["notify-send", "-u", "normal", titulo, cuerpo],
                           check=False, timeout=10)
    except Exception:
        pass                                   # sin aviso grafico, queda el banner


def contar(carpeta, patron):
    return sum(1 for _ in carpeta.rglob(patron)) if carpeta.exists() else 0


def tamano(carpeta):
    if not carpeta.exists():
        return 0
    return sum(f.stat().st_size for f in carpeta.rglob("*") if f.is_file())


def mostrar_estado(m):
    estado = leer_estado()
    pdfs, mds = BASE / "pdfs", BASE / "markdown"
    libre = shutil.disk_usage(BASE if BASE.exists() else BASE.parent).free

    print(f"base: {BASE}")
    print(f"libre en el USB: {libre/1e9:.1f} GB\n")
    print(f"{'año':>6} {'docs':>8} {'pdfs':>8} {'md':>8} {'GB':>7}  estado")

    total_pend = 0
    for anio in sorted(m["Anio"].unique(), reverse=True):
        docs = int((m["Anio"] == anio).sum())
        npdf = sum(contar(pdfs / e / str(anio), "*.pdf") for e in
                   (pdfs.iterdir() if pdfs.exists() else []))
        nmd = sum(contar(mds / e / str(anio), "*.md") for e in
                  (mds.iterdir() if mds.exists() else []))
        gb = sum(tamano(pdfs / e / str(anio)) for e in
                 (pdfs.iterdir() if pdfs.exists() else [])) / 1e9
        info = estado.get(str(anio), {})
        if info.get("subido"):
            marca = f"subido {info['subido'][:10]}"
        elif npdf >= docs * 0.99 and nmd >= npdf * 0.99:
            marca = "LISTO PARA SUBIR"
        elif npdf >= docs * 0.99:
            marca = f"falta OCR ({npdf - nmd:,})"
        elif npdf:
            marca = f"a medias ({npdf/docs*100:.0f}%)"
        else:
            marca = "pendiente"
            total_pend += docs
        print(f"{anio:>6} {docs:>8,} {npdf:>8,} {nmd:>8,} {gb:>7.1f}  {marca}")

    print(f"\npendientes: {total_pend:,} documentos "
          f"(~{total_pend/RITMO:.0f} h de descarga)")


def procesar(anio, m, solo_md):
    docs = int((m["Anio"] == anio).sum())
    if not docs:
        print(f"el año {anio} no tiene documentos en el manifiesto")
        return 1

    pdfs, mds = BASE / "pdfs", BASE / "markdown"
    estimado = docs * PESO_KB[epoca(anio)] * 1024
    BASE.mkdir(parents=True, exist_ok=True)
    libre = shutil.disk_usage(BASE).free

    print(f"LOTE {anio}: {docs:,} documentos")
    if not solo_md:
        print(f"  tamaño estimado : {estimado/1e9:.1f} GB  ({epoca(anio)})")
        print(f"  libre en el USB : {libre/1e9:.1f} GB")
        print(f"  descarga        : ~{docs/RITMO:.1f} h")
    print()

    if not solo_md and libre < estimado * 1.3:
        print(f"ESPACIO INSUFICIENTE: hacen falta ~{estimado*1.3/1e9:.1f} GB "
              f"y hay {libre/1e9:.1f} GB.\nSube y libera algun lote anterior "
              f"con 'lote.py --subido AÑO'.")
        return 1

    if not solo_md:
        print("--- descargando")
        r = subprocess.run([sys.executable, str(AQUI / "descargar.py"),
                            "--destino", str(pdfs), "--anio", str(anio)])
        if r.returncode:
            print("la descarga fallo; el lote queda a medias y se puede retomar")
            return 1

    print("\n--- convirtiendo a markdown")
    r = subprocess.run([sys.executable, str(AQUI / "a_markdown.py"),
                        "--pdfs", str(pdfs), "--salida", str(mds),
                        "--anio", str(anio)])
    if r.returncode:
        return 1

    # Comprobacion final contra el manifiesto: que no falte ningun PDF.
    bajados = sum(contar(pdfs / e.name / str(anio), "*.pdf")
                  for e in pdfs.iterdir()) if pdfs.exists() else 0
    hechos = sum(contar(mds / e.name / str(anio), "*.md")
                 for e in mds.iterdir()) if mds.exists() else 0
    peso_pdf = sum(tamano(pdfs / e.name / str(anio))
                   for e in pdfs.iterdir()) if pdfs.exists() else 0
    peso_md = sum(tamano(mds / e.name / str(anio))
                  for e in mds.iterdir()) if mds.exists() else 0

    estado = leer_estado()
    estado[str(anio)] = {"documentos": docs, "pdfs": bajados, "markdown": hechos,
                         "bytes_pdf": peso_pdf, "bytes_md": peso_md,
                         "procesado": datetime.now().isoformat(timespec="seconds")}
    guardar_estado(estado)

    faltan = docs - bajados
    print("\n" + "=" * 66)
    if faltan > 0 and solo_md:
        # Conversion parcial a proposito, con la descarga aun en marcha: es
        # avance normal, no un fallo. Se informa como progreso.
        print(f"AVANCE {anio}: {bajados:,}/{docs:,} PDFs ({bajados/docs*100:.1f}%), "
              f"{hechos:,} en markdown")
        print(f"  la descarga sigue; vuelve a lanzar --solo-md cuando quieras")
        print("=" * 66)
        return 0
    if faltan > 0:
        print(f"LOTE {anio} INCOMPLETO: faltan {faltan:,} de {docs:,} PDFs")
        print(f"  relanza 'lote.py --anio {anio}' para reclamar lo que falta")
        print("=" * 66)
        return 1

    pendientes = bajados - hechos
    if pendientes:
        print(f"LOTE {anio} DESCARGADO, CON {pendientes:,} PENDIENTES DE OCR")
    else:
        print(f"LOTE {anio} LISTO PARA SUBIR")
    print("=" * 66)
    print(f"  PDFs     : {bajados:,}  ({peso_pdf/1e9:.2f} GB)")
    print(f"  markdown : {hechos:,}  ({peso_md/1e6:.0f} MB)")
    if pendientes:
        print(f"  escaneados sin texto: {pendientes:,} ({pendientes/bajados*100:.0f}%)"
              f" -> markdown/necesitan-ocr.csv")
        print(f"\n  pasales OCR ANTES de subir y liberar: sin markdown, el PDF es")
        print( "  la unica copia y '--subido' no lo borrara.")
    print(f"\n  sube a Drive estas dos carpetas:")
    for e in sorted(p.name for p in (BASE / "pdfs").iterdir()):
        if (BASE / "pdfs" / e / str(anio)).exists():
            print(f"    pdfs/{e}/{anio}/   y   markdown/{e}/{anio}/")
    print(f"\n  cuando este subido:  python3 lote.py --subido {anio}")
    print("  (borra los PDFs y conserva el markdown)")

    if pendientes:
        avisar(f"Lote {anio} descargado",
               f"{bajados:,} PDFs, {hechos:,} en markdown. "
               f"{pendientes:,} escaneados esperan OCR antes de subir.")
    else:
        avisar(f"Lote {anio} listo para subir",
               f"{bajados:,} PDFs ({peso_pdf/1e9:.1f} GB) y {hechos:,} markdown. "
               f"Sube a Drive y luego: lote.py --subido {anio}")
    return 0


def marcar_subido(anio):
    estado = leer_estado()
    info = estado.get(str(anio))
    if not info:
        print(f"el lote {anio} no esta procesado")
        return 1

    pdfs, mds = BASE / "pdfs", BASE / "markdown"
    # Solo se borra el PDF del que ya se extrajo el texto. Un escaneo sin su
    # markdown sigue siendo la unica copia: borrarlo perderia el documento hasta
    # volver a descargarlo, y son horas de cuota del portal.
    borrables, retenidos, peso = [], 0, 0
    if pdfs.exists():
        for especialidad in pdfs.iterdir():
            carpeta = especialidad / str(anio)
            if not carpeta.exists():
                continue
            for pdf in carpeta.rglob("*.pdf"):
                md = mds / pdf.relative_to(pdfs).with_suffix(".md")
                if md.exists():
                    borrables.append(pdf)
                    peso += pdf.stat().st_size
                else:
                    retenidos += 1

    if retenidos:
        print(f"AVISO: {retenidos:,} PDFs de {anio} no tienen markdown "
              f"(escaneados, pendientes de OCR).")
        print("       Esos NO se borran: son la unica copia hasta pasarles OCR.")
    if not borrables:
        print(f"no hay ningun PDF de {anio} que se pueda liberar todavia")
    else:
        print(f"se borraran {len(borrables):,} PDFs de {anio} ({peso/1e9:.2f} GB), "
              f"todos con su markdown ya extraido.")
        if input("confirmar (s/N): ").strip().lower() != "s":
            print("cancelado; no se ha borrado nada")
            return 1
        for pdf in borrables:
            pdf.unlink()
        for especialidad in pdfs.iterdir():           # carpetas que quedan vacias
            carpeta = especialidad / str(anio)
            if carpeta.exists() and not any(carpeta.rglob("*.pdf")):
                shutil.rmtree(carpeta)
        print(f"liberados {peso/1e9:.2f} GB")
    info["pendientes_ocr"] = retenidos

    info["subido"] = datetime.now().isoformat(timespec="seconds")
    guardar_estado(estado)
    print(f"lote {anio} marcado como subido")
    return 0


def main():
    global BASE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anio", type=int)
    ap.add_argument("--persona", choices=sorted(REPARTO),
                    help="encadena los años asignados a esa persona")
    ap.add_argument("--desde", type=int, help="año minimo, para encadenar varios")
    ap.add_argument("--hasta", type=int, help="año maximo, para encadenar varios")
    ap.add_argument("--subido", type=int, metavar="AÑO")
    ap.add_argument("--estado", action="store_true")
    ap.add_argument("--solo-md", action="store_true",
                    help="no descarga, solo reconvierte lo que ya esta")
    ap.add_argument("--base", type=Path, default=BASE)
    args = ap.parse_args()
    BASE = args.base

    if not MANIFIESTO.exists():
        print(f"no encuentro el manifiesto: {MANIFIESTO}")
        return 1
    m = pd.read_csv(MANIFIESTO, usecols=["Anio", "Especialidad"])

    if args.subido:
        return marcar_subido(args.subido)

    if args.persona:
        hechos = ya_subidos()
        anios = [a for a in REPARTO[args.persona]
                 if a in set(m["Anio"]) and a not in hechos]
        if saltados := sorted(set(REPARTO[args.persona]) & hechos, reverse=True):
            print(f"ya subidos, se saltan: {saltados}\n")
        if not anios:
            print(f"{args.persona} no tiene años pendientes")
            return 0
        docs = int(m["Anio"].isin(anios).sum())
        print(f"REPARTO DE {args.persona.upper()}: {len(anios)} años, {docs:,} documentos")
        print(f"  ~{docs/RITMO:.0f} h de descarga  ({docs/RITMO/24:.1f} dias)\n")
        for i, anio in enumerate(anios, start=1):
            print(f"\n{'#' * 66}\n# AÑO {anio}  ({i}/{len(anios)})\n{'#' * 66}")
            if procesar(anio, m, args.solo_md):
                print(f"\nel año {anio} quedo a medias; relanza para continuar")
                return 1
            if i < len(anios):
                print(f"\n>>> sigue con {anios[i]} sin esperar. Sube {anio} cuando "
                      f"puedas y libera con: lote.py --subido {anio}")
        avisar(f"Reparto de {args.persona} terminado",
               f"{len(anios)} años, {docs:,} documentos listos para subir.")
        return 0

    if args.desde or args.hasta:
        # Del mas nuevo al mas viejo: 2017 en adelante es texto nativo y no
        # necesita OCR, asi que lo utilizable entra antes.
        hechos = ya_subidos()
        anios = sorted((a for a in m["Anio"].unique()
                        if (not args.desde or a >= args.desde)
                        and (not args.hasta or a <= args.hasta)
                        and a not in hechos), reverse=True)
        if not anios:
            print("ese rango de años no tiene documentos")
            return 1
        docs = int(m["Anio"].isin(anios).sum())
        print(f"ENCADENANDO {len(anios)} años: {anios[-1]}-{anios[0]}")
        print(f"  {docs:,} documentos, ~{docs/RITMO:.0f} h en total\n")
        for i, anio in enumerate(anios, start=1):
            print(f"\n{'#' * 66}\n# AÑO {anio}  ({i}/{len(anios)})\n{'#' * 66}")
            if procesar(anio, m, args.solo_md):
                print(f"\nel año {anio} quedo a medias; relanza para continuar")
                return 1
        avisar("Todos los lotes terminados",
               f"{len(anios)} años, {docs:,} documentos listos para subir.")
        return 0

    if args.anio:
        return procesar(args.anio, m, args.solo_md)
    mostrar_estado(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
