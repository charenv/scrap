"""Estima cuanto ocupa el corpus de PDFs y que parte son escaneos.

Descarga una muestra estratificada por año -no el corpus- y de cada PDF anota
el tamaño, si lleva fuentes (texto nativo) y si lleva imagenes comprimidas
(escaneo). La distincion importa para el espacio y para saber si hara falta OCR:
un escaneo pesa un orden de magnitud mas y no se puede buscar por texto.

El servlet corta tras ~20 descargas seguidas y responde text/plain vacio en vez
de dar un error, asi que se detecta por Content-Type y se espera antes de
reintentar.

Uso:
    python3 medir_pdfs.py                  # 100 PDFs, pausa 4s
    python3 medir_pdfs.py --n 40 --pausa 6
"""

import argparse
import random
import statistics as st
import sys
import time
from pathlib import Path

import pandas as pd
import requests

CORPUS = Path("/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/"
              "todo-corte-suprema-completo-limpio.xlsx")

# Filtros de imagen: si aparecen, hay pagina rasterizada dentro.
_IMAGEN = (b"/DCTDecode", b"/CCITTFaxDecode", b"/JBIG2Decode", b"/JPXDecode")
ESPERA_CORTE = 90  # segundos a esperar cuando el servidor deja de dar PDFs


def clasificar(cuerpo):
    """('escaneado'|'nativo'|'mixto'|'?', paginas estimadas)."""
    fuente = b"/Font" in cuerpo
    imagen = any(f in cuerpo for f in _IMAGEN)
    if imagen and not fuente:
        tipo = "escaneado"
    elif fuente and not imagen:
        tipo = "nativo"
    elif fuente and imagen:
        tipo = "mixto"
    else:
        tipo = "?"
    paginas = cuerpo.count(b"/Type/Page") + cuerpo.count(b"/Type /Page")
    paginas -= cuerpo.count(b"/Type/Pages") + cuerpo.count(b"/Type /Pages")
    return tipo, max(paginas, 0)


def bajar(sesion, url, timeout=90):
    """(bytes, content_type). Devuelve (None, ct) si no vino un PDF."""
    r = sesion.get(url, timeout=timeout)
    ct = r.headers.get("Content-Type", "")
    cuerpo = r.content
    if not cuerpo.startswith(b"%PDF"):
        return None, ct
    return cuerpo, ct


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--pausa", type=float, default=4.0)
    ap.add_argument("--semilla", type=int, default=11)
    args = ap.parse_args()

    df = pd.read_excel(CORPUS, engine="openpyxl")
    df["Anio"] = df["Anio"].astype(int)
    total_corpus = df["uuid"].nunique()

    # Estratificado por año, proporcional al peso real de cada año en el corpus.
    peso = df["Anio"].value_counts(normalize=True)
    cuotas = (peso * args.n).round().astype(int).clip(lower=1)
    muestra = pd.concat([
        df[df["Anio"] == anio].sample(min(k, (df["Anio"] == anio).sum()),
                                      random_state=args.semilla)
        for anio, k in cuotas.items()
    ]).sample(frac=1, random_state=args.semilla)  # barajada: el corte no sesga un año

    print(f"corpus: {total_corpus:,} PDFs | muestra: {len(muestra)} | pausa {args.pausa}s")
    print(f"{'#':>4} {'año':>5} {'KB':>8} {'pags':>5}  {'tipo':<10} especialidad", flush=True)

    sesion = requests.Session()
    sesion.headers["User-Agent"] = "Mozilla/5.0"
    filas, cortes = [], 0

    for i, (_, f) in enumerate(muestra.iterrows(), start=1):
        for intento in range(3):
            try:
                cuerpo, ct = bajar(sesion, f["Link Resolucion"])
            except Exception as e:
                print(f"{i:4d} {f['Anio']:5d}  FALLO {type(e).__name__}", flush=True)
                cuerpo = None
                break
            if cuerpo is not None:
                break
            cortes += 1
            print(f"{i:4d} {f['Anio']:5d}  cortado ({ct[:20]}), espero {ESPERA_CORTE}s",
                  flush=True)
            time.sleep(ESPERA_CORTE)
            sesion = requests.Session()
            sesion.headers["User-Agent"] = "Mozilla/5.0"

        if cuerpo:
            tipo, paginas = clasificar(cuerpo)
            filas.append({"anio": f["Anio"], "bytes": len(cuerpo), "tipo": tipo,
                          "paginas": paginas, "especialidad": f["Especialidad"]})
            print(f"{i:4d} {f['Anio']:5d} {len(cuerpo)/1024:8.0f} {paginas:5d}  "
                  f"{tipo:<10} {f['Especialidad']}", flush=True)
        time.sleep(args.pausa)

    if not filas:
        print("\nno se pudo medir ningun PDF")
        return 1

    m = pd.DataFrame(filas)
    tam = sorted(m["bytes"])
    print(f"\n{'='*70}\nMUESTRA VALIDA: {len(m)} PDFs  ({cortes} cortes del servidor)")
    print(f"  media   {st.mean(tam)/1024:8.0f} KB")
    print(f"  mediana {st.median(tam)/1024:8.0f} KB")
    print(f"  p10     {tam[int(len(tam)*.1)]/1024:8.0f} KB")
    print(f"  p90     {tam[int(len(tam)*.9)]/1024:8.0f} KB")
    print(f"  max     {tam[-1]/1024:8.0f} KB")

    print("\nPOR TIPO")
    for tipo, g in m.groupby("tipo"):
        print(f"  {tipo:<10} n={len(g):3d} ({len(g)/len(m)*100:4.1f}%)  "
              f"media={g['bytes'].mean()/1024:7.0f} KB  "
              f"paginas={g['paginas'].mean():4.1f}  "
              f"KB/pag={g['bytes'].sum()/1024/max(g['paginas'].sum(),1):6.0f}")

    print("\nPOR AÑO")
    for anio, g in m.groupby("anio"):
        esc = (g["tipo"] == "escaneado").mean() * 100
        print(f"  {anio}  n={len(g):3d}  media={g['bytes'].mean()/1024:7.0f} KB  "
              f"escaneados={esc:5.1f}%")

    # Proyeccion ponderada por el peso real de cada año en el corpus.
    por_anio = m.groupby("anio")["bytes"].mean()
    conteo = df.groupby("Anio")["uuid"].nunique()
    comunes = por_anio.index.intersection(conteo.index)
    ponderado = (por_anio[comunes] * conteo[comunes]).sum()
    cubierto = conteo[comunes].sum()
    proyeccion = ponderado / cubierto * total_corpus

    print(f"\n{'='*70}\nPROYECCION A {total_corpus:,} PDFs")
    print(f"  por media simple        {st.mean(tam)*total_corpus/1e9:7.1f} GB")
    print(f"  por mediana             {st.median(tam)*total_corpus/1e9:7.1f} GB")
    print(f"  ponderada por año  -->  {proyeccion/1e9:7.1f} GB   (la mas fiable)")
    err = st.stdev(tam) / len(tam) ** 0.5 if len(tam) > 1 else 0
    print(f"  margen (+-1.96 EE)      {(st.mean(tam)-1.96*err)*total_corpus/1e9:.0f} "
          f"- {(st.mean(tam)+1.96*err)*total_corpus/1e9:.0f} GB")

    m.to_csv(Path(__file__).parent / "muestra-pdfs.csv", index=False)
    print(f"\nmuestra -> muestra-pdfs.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
