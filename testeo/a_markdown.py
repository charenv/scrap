"""Convierte los PDFs descargados a markdown con frontmatter, listo para RAG.

Tres cosas, en este orden:

  1. Quita el ruido. El 14% del texto extraido son sellos de firma digital
     (SINOE) que el PDF pinta en un lateral y pdftotext vuelca en mitad de las
     frases, partiendolas. Llevan nombres de jueces y fechas de notificacion que
     no son contenido y ensucian los embeddings.

  2. Parte por secciones. Las resoluciones siguen una estructura legal fija
     (VISTOS / MATERIA DEL RECURSO / CONSIDERANDO / DECISION) que varia segun el
     tipo de recurso, no segun la especialidad: se detecta la union de todas las
     cabeceras conocidas y se normaliza a titulos de markdown, para que los
     chunks del RAG caigan en unidades con sentido en vez de cada N caracteres.

  3. Pone los metadatos delante. Todo lo de la cosecha va en frontmatter YAML,
     asi cada chunk puede arrastrar especialidad, sala, año y tipo de recurso
     como filtros estructurados.

Los documentos escaneados no dan texto: se marcan con 'necesita_ocr: true' en un
listado aparte, para pasarles OCR sin volver a mirar los 209 mil.

Uso:
    python3 a_markdown.py --pdfs /ruta/pdfs --salida /ruta/markdown
    python3 a_markdown.py --pdfs /ruta/pdfs --salida /ruta/md --anio 2026
"""

import os

# Rutas configurables por variable de entorno, para que cada maquina del equipo
# use las suyas sin tocar el codigo:
#   export CORPUS_BASE=/ruta/al/usb/corpus-corte-suprema
#   export CORPUS_MANIFIESTO=/ruta/al/manifiesto-pdfs.csv
import argparse
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

MANIFIESTO = Path(os.environ.get(
    "CORPUS_MANIFIESTO",
    "/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/manifiesto-pdfs.csv"))
CORPUS = Path(os.environ.get(
    "CORPUS_EXCEL",
    "/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/todo-corte-suprema-completo-limpio.xlsx"))

# Un bloque que contenga cualquiera de estas marcas es sello de notificacion,
# no contenido. Se descarta el bloque entero, no la linea: el sello ocupa varias.
_SELLO = re.compile(
    r"SINOE|Sistema de Notificaciones|FIRMA DIGITAL|Servicio Digital"
    r"|Vocal Supremo\s*:|Secretari[oa]\s*:|D\.?Judicial\s*:"
    r"|Razón\s*:\s*RESOLUCI[OÓ]N|SEDE PALACIO DE JUSTICIA",
    re.IGNORECASE)

# Membrete que se repite en cada pagina y no aporta nada: ya esta en los metadatos.
_MEMBRETE = re.compile(
    r"^\s*(CORTE SUPREMA DE JUSTICIA( DE LA REPÚBLICA)?"
    r"|PODER JUDICIAL DEL PERÚ|-?\s*\d+\s*-?)\s*$", re.IGNORECASE)

# Cabeceras legales -> titulo canonico. La union de las que usan los distintos
# tipos de recurso; ninguna resolucion las trae todas.
SECCIONES = [
    (r"VIST[OA]S?\b.*", "Vistos"),
    (r"(I+\.?\s*)?ASUNTO\b", "Asunto"),
    (r"(I+\.?\s*)?MATERIA DEL RECURSO\b", "Materia del recurso"),
    (r"(I+\.?\s*)?MATERIA DE LA CONSULTA\b", "Materia de la consulta"),
    (r"(I+\.?\s*)?ANTECEDENTES\b", "Antecedentes"),
    (r"CAUSAL(ES)? (DEL|DE LOS) RECURSO?S?\b", "Causales del recurso"),
    (r"(I+\.?\s*)?FUNDAMENTOS?( DEL RECURSO| DE LA SALA)?\b", "Fundamentos"),
    (r"CONSIDERANDO\b", "Considerando"),
    (r"(I+\.?\s*)?AN[ÁA]LISIS\b", "Análisis"),
    (r"(I+\.?\s*)?DECISI[ÓO]N\b", "Decisión"),
    (r"AUTO CALIFICATORIO DEL RECURSO\b", "Auto calificatorio"),
    # 'SS.' es el marcador estandar de la firma de los jueces supremos: sale en
    # 20 de cada 22 resoluciones, mucho mas fiable que adivinar que apellidos son.
    (r"S\.?\s*S\.?", "Firmas"),
]
_SECCIONES = [(re.compile(rf"^\s*{p}\s*[:\.\-]?\s*$", re.IGNORECASE), n)
              for p, n in SECCIONES]

# La parte resolutiva casi nunca ocupa una linea suelta: va pegada al texto
# ("POR ESTAS CONSIDERACIONES, DECLARARON INFUNDADO el recurso..."). Estas se
# buscan al principio de linea y se corta ahi, conservando el resto como cuerpo.
# 'DECLARARON' es el verbo mas frecuente del corpus, por delante de 'RESUELVE'.
INICIOS = [
    (r"POR (ESTOS|ESTAS) (FUNDAMENTOS|CONSIDERACIONES|RAZONES)", "Decisión"),
    (r"(SE RESUELVE|HA RESUELTO|RESUELVE|RESOLVIERON|DECLARARON|FALLO)\b", "Decisión"),
]
_INICIOS = [(re.compile(rf"^\s*({p})", re.IGNORECASE), n) for p, n in INICIOS]

MINIMO_TEXTO = 200  # por debajo de esto el PDF es un escaneo sin capa de texto


def extraer(pdf):
    """Texto crudo del PDF, o '' si es un escaneo."""
    r = subprocess.run(["pdftotext", str(pdf), "-"],
                       capture_output=True, text=True, timeout=120)
    return r.stdout if r.returncode == 0 else ""


def limpiar(texto):
    """Quita sellos de firma, membretes repetidos y espaciado sobrante."""
    bloques = re.split(r"\n\s*\n", texto)
    utiles = []
    for bloque in bloques:
        if _SELLO.search(bloque):
            continue
        lineas = [l for l in bloque.splitlines() if not _MEMBRETE.match(l)]
        bloque = "\n".join(lineas).strip()
        if bloque:
            utiles.append(bloque)
    limpio = "\n\n".join(utiles)
    limpio = limpio.replace("\f", "\n")
    return re.sub(r"\n{3,}", "\n\n", limpio).strip()


def seccionar(texto):
    """Convierte las cabeceras legales en titulos '## ' de markdown."""
    salida, encontradas = [], []
    for linea in texto.splitlines():
        for patron, nombre in _SECCIONES:
            if patron.match(linea):
                salida.append(f"\n## {nombre}\n")
                encontradas.append(nombre)
                break
        else:
            # Cabecera pegada al texto: se corta delante y el resto sigue siendo
            # cuerpo, para no perder la parte resolutiva de la misma linea.
            for patron, nombre in _INICIOS:
                if (m := patron.match(linea)) and nombre not in encontradas:
                    salida.append(f"\n## {nombre}\n")
                    salida.append(linea[m.start(1):])
                    encontradas.append(nombre)
                    break
            else:
                salida.append(linea)
    return "\n".join(salida), encontradas


def _yaml(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return '""'
    s = str(valor).replace('"', "'").replace("\n", " ").strip()
    return f'"{s}"'


def frontmatter(fila, paginas, secciones, chars):
    campos = {
        "uuid": fila["uuid"],
        "expediente": fila["Nro Expediente"],
        "recurso": fila["Casacion/Apelacion"],
        "especialidad": fila["Especialidad"],
        "sala": fila["Sala Suprema"],
        "fecha": fila["Fecha de Resolucion"],
        "anio": fila["Anio"],
        "materia": fila["Pretension/Delito"],
        "norma": fila["Norma de Derecho Interno (Articulo)"],
        "palabras_clave": fila["Palabras Clave"],
        "url": fila["Link Resolucion"],
        "paginas": paginas,
        "caracteres": chars,
        "secciones": ", ".join(dict.fromkeys(secciones)) or "",
    }
    lineas = [f"{k}: {_yaml(v)}" for k, v in campos.items()]
    return "---\n" + "\n".join(lineas) + "\n---\n"


def convertir(fila, pdf):
    """(markdown, secciones) o (None, motivo) si no se pudo."""
    crudo = extraer(pdf)
    if not crudo.strip():
        return None, "sin texto"
    paginas = crudo.count("\f") + 1
    limpio = limpiar(crudo)
    if len(limpio) < MINIMO_TEXTO:
        return None, "escaneado"

    cuerpo, secciones = seccionar(limpio)
    cabecera = frontmatter(fila, paginas, secciones, len(limpio))

    titulo = f"# {fila['Casacion/Apelacion']} {fila['Nro Expediente']}"
    sumilla = fila["Sumilla"]
    bloque_sumilla = ""
    if isinstance(sumilla, str) and sumilla.strip():
        bloque_sumilla = f"\n> **Sumilla.** {sumilla.strip()}\n"

    return f"{cabecera}\n{titulo}\n{bloque_sumilla}\n{cuerpo.strip()}\n", secciones


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdfs", type=Path, required=True)
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--anio", type=int)
    ap.add_argument("--especialidad")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--rehacer", action="store_true",
                    help="reconvierte tambien los que ya tienen markdown")
    args = ap.parse_args()

    corpus = pd.read_excel(CORPUS, engine="openpyxl")
    if args.anio:
        corpus = corpus[corpus["Anio"] == args.anio]
    if args.especialidad:
        corpus = corpus[corpus["Especialidad"] == args.especialidad]

    # Solo lo que este descargado.
    corpus = corpus[[(args.pdfs / r).exists() for r in corpus["Archivo PDF"]]]

    # Solo los que no tienen markdown, o cuyo PDF es mas nuevo que el. Reconvertir
    # el año entero en cada pasada son ~40 minutos con 16 000 documentos, y con
    # --solo-md se lanza muchas veces mientras la descarga avanza.
    # La fecha importa por el OCR: ocrmypdf reemplaza el PDF, asi que su markdown
    # queda viejo y hay que rehacerlo. Compararlas lo detecta solo.
    if not args.rehacer:
        antes = len(corpus)
        pendientes = []
        for pdf_rel, md_rel in zip(corpus["Archivo PDF"], corpus["Archivo MD"]):
            md = args.salida / md_rel
            pendientes.append(
                not md.exists()
                or md.stat().st_mtime < (args.pdfs / pdf_rel).stat().st_mtime)
        corpus = corpus[pendientes]
        if antes - len(corpus):
            print(f"  {antes - len(corpus):,} ya convertidos, se saltan")
    if args.limite:
        corpus = corpus.head(args.limite)
    if corpus.empty:
        print(f"no hay PDFs descargados en {args.pdfs} con ese filtro")
        return 1

    print(f"convirtiendo {len(corpus):,} PDFs -> {args.salida}\n")
    hechos = fallidos = 0
    chars = 0
    sin_texto, secciones_vistas = [], {}

    for fila in corpus.to_dict("records"):
        pdf = args.pdfs / fila["Archivo PDF"]
        destino = args.salida / fila["Archivo MD"]
        md, info = convertir(fila, pdf)
        if md is None:
            fallidos += 1
            sin_texto.append({"uuid": fila["uuid"], "ruta": fila["Archivo PDF"],
                              "motivo": info, "url": fila["Link Resolucion"]})
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(md, encoding="utf-8")
        hechos += 1
        chars += len(md)
        for s in set(info):
            secciones_vistas[s] = secciones_vistas.get(s, 0) + 1

    print(f"convertidos : {hechos:,}  ({chars/1e6:.1f} M caracteres, "
          f"media {chars//max(hechos,1):,} por documento)")
    print(f"necesitan OCR: {fallidos:,}")

    if secciones_vistas:
        print("\nsecciones detectadas (% de documentos):")
        for s, n in sorted(secciones_vistas.items(), key=lambda x: -x[1]):
            print(f"  {s:<26} {n/hechos*100:5.1f}%")

    if sin_texto:
        # Un fichero por año: el corpus se procesa año a año y un unico
        # necesitan-ocr.csv se sobrescribia en cada vuelta, dejando solo el
        # ultimo. Ademas asi los tres pueden juntar sus carpetas sin pisarse.
        sufijo = f"-{args.anio}" if args.anio else ""
        pendiente = args.salida / f"necesitan-ocr{sufijo}.csv"
        pendiente.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(sin_texto).to_csv(pendiente, index=False)
        print(f"\n{len(sin_texto):,} pendientes de OCR -> {pendiente}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
