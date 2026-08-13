"""Saca el corpus entero usando el boton "Exportar" del propio buscador.

Una peticion por especialidad en vez de una por cada 10 resoluciones: las
209 344 resoluciones salen en minutos en lugar de ~17 horas.

Lo que el export NO trae es el uuid ni el link del PDF, porque eso solo existe
en el HTML del listado. Por eso, si ya hay datos paginados en salida/ (los que
va dejando scrap.py), este script los cruza y rellena el link donde puede.

El Excel resultante usa las mismas columnas que scrap.py.

Uso:
    python3 exportar.py                    # las 12 especialidades
    python3 exportar.py --persona charen   # solo las de una persona
    python3 exportar.py --especialidad Penal
"""

import argparse
import io
import json
import sys
import time

import pandas as pd

import juris_parse as jp
from juris_client import ESPECIALIDADES, REPARTO, ClienteJuris, SesionExpirada
from juris_excel import SALIDA, escribir_excel, slug

# El .xls trae la cabecera en la segunda fila y con la codificacion rota
# ("Pretensiï¿½n/Delito"), asi que se renombra por posicion, no por nombre.
COLUMNAS_EXPORT = [
    "_vacia",
    "Recurso",
    "Pretension/Delito",
    "Tipo de Resolucion",
    "Fecha de Resolucion",
    "Sala Suprema",
    "Norma de Derecho Interno (Articulo)",
    "Sumilla",
    "Palabras Clave",
    "Terminos Tesauro",
]


def links_ya_scrapeados(especialidad):
    """Mapa 'Casación-003439-2023' -> link, con lo que haya cosechado cosecha.py."""
    directorio = SALIDA / slug(especialidad) / "tramos"
    if not directorio.exists():
        return {}

    mapa = {}
    for fichero in sorted(directorio.glob("p*.json")):
        try:
            datos = json.loads(fichero.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for fila in datos["filas"]:
            clave = f"{fila['Casacion/Apelacion']}-{fila['Nro Expediente']}"
            # Un mismo nro de expediente puede repetirse con resoluciones
            # distintas; nos quedamos con el primero y no inventamos parejas.
            mapa.setdefault(clave, fila["Link Resolucion"])
    return mapa


def normalizar(xls_bytes, especialidad):
    """Convierte el .xls del PJ a nuestras columnas."""
    df = pd.read_excel(io.BytesIO(xls_bytes), header=1)
    df.columns = COLUMNAS_EXPORT[: len(df.columns)]
    df = df.drop(columns=[c for c in ("_vacia", "Terminos Tesauro") if c in df.columns])

    # "Casación-003439-2023" -> tipo de recurso + nro de expediente.
    partes = df["Recurso"].astype(str).str.rsplit("-", n=2)
    df["Casacion/Apelacion"] = partes.str[0]
    df["Nro Expediente"] = partes.str[1:].str.join("-")

    mapa = links_ya_scrapeados(especialidad)
    df["Link Resolucion"] = df["Recurso"].map(mapa).fillna("")
    df["Especialidad"] = especialidad
    df["Pagina"] = ""

    con_link = int((df["Link Resolucion"] != "").sum())
    df = df.drop(columns=["Recurso"])
    return df.fillna("").to_dict("records"), con_link


def exportar(especialidad, pausa=2.0, reintentos=3):
    """Descarga y normaliza una especialidad. Devuelve sus filas."""
    for intento in range(1, reintentos + 1):
        try:
            cliente = ClienteJuris(especialidad=especialidad, pausa=pausa)
            html = cliente.buscar(especialidad)
            _, esperadas = jp.totales(html)

            crudo = cliente.exportar_excel()
            filas, con_link = normalizar(crudo, especialidad)

            carpeta = SALIDA / slug(especialidad)
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / f"{slug(especialidad)}-export.xls").write_bytes(crudo)
            escribir_excel(filas, carpeta / f"{slug(especialidad)}.xlsx")

            aviso = "" if len(filas) == esperadas else f"  !! el buscador decia {esperadas}"
            print(f"{especialidad:36} {len(filas):>7} filas  {con_link:>7} con link{aviso}")
            return filas

        except (SesionExpirada, Exception) as e:
            if intento == reintentos:
                print(f"{especialidad:36} FALLO: {type(e).__name__}: {e}")
                return []
            print(f"{especialidad:36} reintento {intento}/{reintentos} ({type(e).__name__})")
            time.sleep(pausa * 2**intento)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--persona", choices=sorted(REPARTO))
    ap.add_argument("--especialidad", choices=sorted(ESPECIALIDADES))
    ap.add_argument("--pausa", type=float, default=2.0)
    args = ap.parse_args()

    if args.especialidad:
        objetivo = [args.especialidad]
    elif args.persona:
        objetivo = REPARTO[args.persona]
    else:
        objetivo = sorted(ESPECIALIDADES, key=lambda e: ESPECIALIDADES[e])

    print(f"{'ESPECIALIDAD':36} {'FILAS':>7} {'CON LINK':>12}")
    todas = []
    for especialidad in objetivo:
        todas.extend(exportar(especialidad, pausa=args.pausa))

    if len(objetivo) > 1:
        for persona, suyas in REPARTO.items():
            filas = [f for f in todas if f["Especialidad"] in suyas]
            if filas:
                escribir_excel(filas, SALIDA / f"{persona}-consolidado.xlsx")
                print(f"{persona + ' (consolidado)':36} {len(filas):>7} filas")

        escribir_excel(todas, SALIDA / "todo-corte-suprema.xlsx")
        print(f"\nTOTAL: {len(todas)} resoluciones -> salida/todo-corte-suprema.xlsx")


if __name__ == "__main__":
    sys.exit(main())
