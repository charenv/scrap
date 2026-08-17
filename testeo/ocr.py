"""Pasa OCR a los PDFs escaneados para que el resto del pipeline no cambie.

Un tercio del corpus -sobre todo lo anterior a 2017- son fotos de la resolucion
metidas en un PDF, sin capa de texto: pdftotext devuelve cero caracteres y
a_markdown.py los aparta en necesitan-ocr.csv.

ocrmypdf les añade una capa de texto invisible encima de la imagen. El PDF sigue
viendose igual y pesa parecido, pero ya responde a pdftotext, asi que despues de
este paso 'a_markdown.py' los procesa como si fueran nativos. El OCR es un rodeo
antes de la puerta, no una segunda puerta.

Reanudable: antes de trabajar comprueba si el PDF ya da texto, asi que relanzarlo
solo toca lo que falta. La escritura es atomica (fichero temporal + rename), de
modo que un corte a media pagina nunca deja un PDF roto.

Requiere:
    sudo dnf install ocrmypdf tesseract-langpack-spa

Uso:
    python3 ocr.py --pdfs /ruta/pdfs                     # todo lo que falte
    python3 ocr.py --pdfs /ruta/pdfs --anio 2013
    python3 ocr.py --pdfs /ruta/pdfs --jobs 8 --limite 50
"""

import argparse
import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path

MINIMO_TEXTO = 200  # mismos umbrales que a_markdown.py, para no discrepar
# ocrmypdf: 0 ok. 6 = la pagina ya tenia texto (con --skip-text no deberia salir,
# pero se admite igual). El resto son fallos de verdad.
CODIGOS_OK = {0, 6}


def faltan_herramientas():
    """ocrmypdf sin tesseract se ejecuta pero no reconoce nada: hacen falta las dos."""
    return [b for b in ("ocrmypdf", "tesseract") if shutil.which(b) is None]


def idiomas():
    try:
        r = subprocess.run(["tesseract", "--list-langs"],
                           capture_output=True, text=True, timeout=30)
        return set(r.stdout.split()[1:])
    except Exception:
        return set()


def tiene_texto(pdf):
    """True si el PDF ya devuelve texto util: entonces no hay nada que hacer."""
    try:
        r = subprocess.run(["pdftotext", str(pdf), "-"],
                           capture_output=True, text=True, timeout=120)
        return len(r.stdout.strip()) >= MINIMO_TEXTO
    except Exception:
        return False


def ocr(pdf, idioma, jobs, extra):
    """Añade la capa de texto sobre el propio PDF. (ok, motivo)."""
    temporal = pdf.with_suffix(".ocr.tmp")
    orden = ["ocrmypdf", "--skip-text", "--language", idioma,
             "--jobs", str(jobs), "--optimize", "1", "--quiet", *extra,
             str(pdf), str(temporal)]
    try:
        r = subprocess.run(orden, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        temporal.unlink(missing_ok=True)
        return False, "timeout"
    except Exception as e:
        temporal.unlink(missing_ok=True)
        return False, type(e).__name__

    if r.returncode not in CODIGOS_OK or not temporal.exists():
        temporal.unlink(missing_ok=True)
        motivo = (r.stderr or "").strip().splitlines()
        return False, (motivo[-1][:80] if motivo else f"codigo {r.returncode}")

    # El codigo de salida no basta: ocrmypdf puede terminar en 0 y devolver un
    # PDF sin una linea de texto (p.ej. si tesseract no esta). Sustituir el
    # original por eso seria cambiarlo por nada, en silencio. Se comprueba el
    # resultado, que es lo unico que importa.
    if not tiene_texto(temporal):
        temporal.unlink(missing_ok=True)
        return False, "el OCR no produjo texto"

    temporal.replace(pdf)  # atomico: o esta el viejo o el nuevo, nunca a medias
    return True, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdfs", type=Path, required=True)
    ap.add_argument("--anio", type=int)
    ap.add_argument("--especialidad", help="slug de la carpeta, p.ej. penal")
    ap.add_argument("--idioma", default="spa")
    ap.add_argument("--jobs", type=int, default=4, help="hilos por documento")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--sin-enderezar", action="store_true",
                    help="mas rapido, peor con escaneos torcidos")
    args = ap.parse_args()

    if faltan := faltan_herramientas():
        print(f"falta{'n' if len(faltan) > 1 else ''}: {', '.join(faltan)}")
        print("  sudo dnf install ocrmypdf tesseract-langpack-spa")
        return 1
    disponibles = idiomas()
    if disponibles and args.idioma not in disponibles:
        print(f"tesseract no tiene el idioma '{args.idioma}'. Tiene: "
              f"{', '.join(sorted(disponibles))}\n"
              f"  sudo dnf install tesseract-langpack-{args.idioma}")
        return 1

    # Enderezar cuesta CPU pero en escaneos de juzgado cambia mucho el resultado:
    # muchos vienen torcidos. Rotar paginas se añade solo si tesseract tiene el
    # modelo 'osd': sin el, tesseract no arranca siquiera y tumba todo el OCR,
    # no solo la rotacion. Es un fallo silencioso caro de diagnosticar.
    extra = []
    if not args.sin_enderezar:
        extra.append("--deskew")
        if "osd" in disponibles:
            extra.append("--rotate-pages")
        else:
            print("aviso: sin el modelo 'osd' no se corrige la orientacion.")
            print("       sudo dnf install tesseract-langpack-osd\n")

    raiz = args.pdfs
    if args.especialidad:
        raiz = raiz / args.especialidad
    patron = f"*/{args.anio}/*.pdf" if args.anio else "*.pdf"
    candidatos = sorted(raiz.rglob(patron) if args.anio else raiz.rglob("*.pdf"))
    if args.anio and not args.especialidad:
        candidatos = [p for p in sorted(raiz.rglob("*.pdf"))
                      if p.parent.name == str(args.anio)]
    if not candidatos:
        print(f"no hay PDFs en {raiz} con ese filtro")
        return 1

    print(f"revisando {len(candidatos):,} PDFs en {raiz}")
    pendientes = [p for p in candidatos if not tiene_texto(p)]
    print(f"  ya tienen texto : {len(candidatos) - len(pendientes):,}")
    print(f"  necesitan OCR   : {len(pendientes):,}")
    if not pendientes:
        print("nada que hacer")
        return 0
    if args.limite:
        pendientes = pendientes[: args.limite]

    print(f"  idioma {args.idioma} | {args.jobs} hilos"
          f" | {' '.join(x.lstrip('-') for x in extra) or 'sin retoques'}\n")

    hechos = fallidos = 0
    fallos = []
    inicio = time.monotonic()
    for i, pdf in enumerate(pendientes, start=1):
        ok, motivo = ocr(pdf, args.idioma, args.jobs, extra)
        if ok:
            hechos += 1
        else:
            fallidos += 1
            fallos.append({"ruta": str(pdf.relative_to(args.pdfs)), "motivo": motivo})
            print(f"  FALLO {pdf.name}: {motivo}", flush=True)

        if i % 20 == 0 or i == len(pendientes):
            transcurrido = time.monotonic() - inicio
            ritmo = i / transcurrido
            queda = (len(pendientes) - i) / ritmo if ritmo else 0
            print(f"  {i:,}/{len(pendientes):,}  {hechos:,} ok  {fallidos} fallos  "
                  f"{transcurrido/i:.1f}s/doc  quedan ~{queda/3600:.1f} h", flush=True)

    print(f"\nOCR hecho en {hechos:,} PDFs, {fallidos} fallos, "
          f"{(time.monotonic()-inicio)/3600:.1f} h")
    if fallos:
        destino = args.pdfs / "fallos-ocr.csv"
        with destino.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["ruta", "motivo"])
            w.writeheader()
            w.writerows(fallos)
        print(f"  detalle -> {destino}")
    if hechos:
        print("\nahora vuelve a convertir para recoger el texto nuevo:")
        print(f"  python3 a_markdown.py --pdfs {args.pdfs} --salida <markdown>"
              + (f" --anio {args.anio}" if args.anio else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
