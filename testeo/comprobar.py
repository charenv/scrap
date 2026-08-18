"""Comprueba que la maquina esta lista antes de lanzar la primera descarga.

Repasa las rutas, las herramientas externas y el espacio libre, y dice
exactamente que falta y como arreglarlo. Vale la pena correrlo antes de dejar
un lote de 20 horas: casi todos los fallos de arranque son una variable de
entorno mal puesta o poppler fuera del PATH.

Uso:
    python3 comprobar.py
"""

import os
import shutil
import sys
from pathlib import Path

WIN = sys.platform.startswith("win")
OK, MAL, AVISO = "  [ok]   ", "  [FALTA]", "  [aviso]"

# Espacio para el año mas grande de cualquier reparto, con margen.
ESPACIO_MINIMO_GB = 15

# Como es el corpus bueno. Hay cuatro Excel con nombres parecidos y dos de ellos
# se quedaron en 208 641 filas, sin las 537 de Civil que se recuperaron despues.
# Trabajar con el equivocado no da ningun error: simplemente faltarian 537
# resoluciones al juntarlo todo, y nadie se enteraria.
FILAS_ESPERADAS = 209_180
CIVIL_ESPERADO = 14_735


def como_definir(var, ejemplo):
    if WIN:
        return f'setx {var} "{ejemplo}"   (y abre una terminal nueva)'
    return f'export {var}={ejemplo}   (añadelo a ~/.bashrc)'


def revisar_ruta(var, ejemplo, debe_existir):
    """Devuelve True si esta bien. debe_existir: el fichero tiene que estar ya."""
    valor = os.environ.get(var)
    if not valor:
        print(f"{MAL} {var} no esta definida")
        print(f"         {como_definir(var, ejemplo)}")
        return False

    ruta = Path(valor)
    if not debe_existir:
        # CORPUS_BASE se crea sola; lo que importa es que su padre exista.
        if ruta.exists():
            print(f"{OK} {var} = {ruta}")
        elif ruta.parent.exists():
            print(f"{OK} {var} = {ruta}  (se creara al primer lote)")
        else:
            print(f"{MAL} {var} apunta a {ruta}, y ni siquiera existe {ruta.parent}")
            print(f"         revisa la letra de unidad o crea la carpeta")
            return False
        return True

    if not ruta.exists():
        print(f"{MAL} {var} apunta a un fichero que no existe:")
        print(f"         {ruta}")
        print(f"         bajalo de la carpeta compartida de Drive y pon la ruta real")
        if WIN:
            print(f"         truco: Shift + clic derecho en el fichero -> "
                  f"'Copiar como ruta de acceso'")
        return False

    mb = ruta.stat().st_size / 1e6
    print(f"{OK} {var} = {ruta.name}  ({mb:.0f} MB)")
    return True


def revisar_orden(nombre, para_que, obligatorio):
    if shutil.which(nombre):
        print(f"{OK} {nombre}  ({para_que})")
        return True
    etiqueta = MAL if obligatorio else AVISO
    print(f"{etiqueta} {nombre} no esta en el PATH  ({para_que})")
    if nombre == "pdftotext":
        print("         Windows: winget install oschwartz10612.Poppler")
        print("         Linux:   sudo dnf install poppler-utils")
    elif nombre in ("ocrmypdf", "tesseract"):
        print("         solo hace falta para los años anteriores a 2017")
        print("         Windows: winget install UB-Mannheim.TesseractOCR && pip install ocrmypdf")
        print("         Linux:   sudo dnf install ocrmypdf tesseract-langpack-spa")
    return not obligatorio


def revisar_paquete(nombre):
    try:
        __import__(nombre)
        print(f"{OK} {nombre}")
        return True
    except ImportError:
        print(f"{MAL} falta el paquete {nombre}")
        print(f"         pip install {nombre}")
        return False


def main():
    print(f"comprobando la maquina ({'Windows' if WIN else sys.platform})\n")
    bien = True

    print("RUTAS")
    bien &= revisar_ruta("CORPUS_BASE",
                         r"D:\corte-suprema" if WIN else "/ruta/al/disco/corte-suprema",
                         debe_existir=False)
    bien &= revisar_ruta("CORPUS_MANIFIESTO",
                         r"C:\Users\tu\Downloads\manifiesto-pdfs.csv" if WIN
                         else "/ruta/manifiesto-pdfs.csv",
                         debe_existir=True)
    bien &= revisar_ruta("CORPUS_EXCEL",
                         r"C:\Users\tu\Downloads\todo-corte-suprema-completo-limpio.xlsx"
                         if WIN else "/ruta/todo-corte-suprema-completo-limpio.xlsx",
                         debe_existir=True)

    print("\nPAQUETES DE PYTHON")
    for p in ("pandas", "openpyxl", "requests"):
        bien &= revisar_paquete(p)

    print("\nHERRAMIENTAS")
    bien &= revisar_orden("pdftotext", "convertir PDF a texto", obligatorio=True)
    revisar_orden("ocrmypdf", "OCR, solo para años <= 2016", obligatorio=False)
    revisar_orden("tesseract", "OCR, solo para años <= 2016", obligatorio=False)

    print("\nESPACIO")
    base = Path(os.environ.get("CORPUS_BASE", "."))
    ref = base if base.exists() else base.parent if base.parent.exists() else Path(".")
    libre = shutil.disk_usage(ref).free / 1e9
    if libre >= ESPACIO_MINIMO_GB:
        print(f"{OK} {libre:.0f} GB libres en {ref}")
    else:
        print(f"{AVISO} solo {libre:.0f} GB libres en {ref}")
        print(f"         un año de 2017+ ocupa ~2,5 GB y uno anterior ~18 GB")
        print(f"         puedes empezar igual, pero tendras que ir subiendo y liberando")

    # El manifiesto y el Excel son la fuente de todo: si se leen y traen las
    # cifras que tocan, el resto encaja.
    if bien:
        print("\nCONTENIDO")
        try:
            import pandas as pd
            m = pd.read_csv(os.environ["CORPUS_MANIFIESTO"],
                            usecols=["Anio", "uuid", "Especialidad"])
            civil = int((m.Especialidad == "Civil").sum())
            if len(m) == FILAS_ESPERADAS and civil == CIVIL_ESPERADO:
                print(f"{OK} manifiesto: {len(m):,} resoluciones, "
                      f"años {m.Anio.min()}-{m.Anio.max()}")
            else:
                bien = False
                print(f"{MAL} manifiesto equivocado: {len(m):,} filas "
                      f"(deberian ser {FILAS_ESPERADAS:,}), Civil {civil:,} "
                      f"(deberian ser {CIVIL_ESPERADO:,})")
                print("         bajate el de la carpeta compartida de Drive: hay "
                      "versiones antiguas a las que")
                print("         les faltan 537 resoluciones de Civil y no dan "
                      "ningun error, solo faltan.")
            if m.uuid.nunique() < len(m) - 5:
                print(f"{AVISO} solo {m.uuid.nunique():,} uuid distintos")

            x = pd.read_excel(os.environ["CORPUS_EXCEL"], engine="openpyxl",
                              usecols=["Especialidad", "Archivo PDF"])
            civil_x = int((x.Especialidad == "Civil").sum())
            if len(x) == FILAS_ESPERADAS and civil_x == CIVIL_ESPERADO:
                print(f"{OK} excel: {len(x):,} filas, con las rutas de los PDFs")
            else:
                bien = False
                print(f"{MAL} excel equivocado: {len(x):,} filas, Civil {civil_x:,}")
                print("         tiene que ser todo-corte-suprema-COMPLETO-LIMPIO.xlsx")
        except KeyError:
            bien = False
            print(f"{MAL} al excel le falta la columna 'Archivo PDF': es una "
                  f"version sin las rutas")
            print("         tiene que ser todo-corte-suprema-completo-LIMPIO.xlsx")
        except Exception as e:
            bien = False
            print(f"{MAL} no se pudo leer: {type(e).__name__}: {e}")

    print()
    if bien:
        print("TODO LISTO. Lanza tu reparto con:")
        print("    python3 lote.py --persona TU_NOMBRE")
    else:
        print("FALTAN COSAS. Arregla lo marcado con [FALTA] y vuelve a correr esto.")
    return 0 if bien else 1


if __name__ == "__main__":
    sys.exit(main())
