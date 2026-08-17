"""Extraccion de datos desde el HTML de resultado.xhtml.

El sitio del PJ renderiza cada resolucion como un panel de RichFaces. En vez de
raspar los <div> de presentacion (fragiles ante cualquier retoque de CSS),
leemos el payload JSON que el propio JSF incrusta en el onclick del boton
"Ver ficha": ahi vienen todos los campos ya estructurados.
"""

import html
import json
import re
import unicodedata

from bs4 import BeautifulSoup

BASE = "https://jurisprudencia.pj.gob.pe"

# Panel de una resolucion y enlace al PDF dentro de el.
_ID_PANEL = re.compile(r"^formBuscador:repeat:\d+:j_idt\d+$")
_HREF_DESCARGA = re.compile(r"ServletDescarga\?uuid=([0-9a-fA-F-]{36})")

# El <a> de "Ver ficha" de cada fila: formBuscador:repeat:<n>:j_idt491
_ANCHOR_FICHA = re.compile(
    r'id="formBuscador:repeat:(\d+):j_idt\d+"[^>]*onclick="([^"]*)"'
)
_TOTAL = re.compile(
    r"De un total de\s*([\d.,]+)\s*resoluciones,\s*se obtuvieron\s*([\d.,]+)\s*resultados"
)
# El "de N" del paginador: <input spinner> ... de <span>N</span>
_NUM_PAGINAS = re.compile(r"spinner[^>]*>.*?de\s*.*?>\s*([\d.,]+)\s*<", re.S)

# Campos del payload JSON -> nombre de columna final
CAMPOS = {
    "recurso": "Casacion/Apelacion",
    "nroexp": "Nro Expediente",
    "pretensiones": "Pretension/Delito",
    "tipoResolucion": "Tipo de Resolucion",
    "fechaResolucion": "Fecha de Resolucion",
    "sala": "Sala Suprema",
    "normaDI": "Norma de Derecho Interno (Articulo)",
    "sumilla": "Sumilla",
    "palabras": "Palabras Clave",
}

COLUMNAS = list(CAMPOS.values()) + ["Link Resolucion", "uuid", "Especialidad", "Pagina"]


def _entero(txt):
    """'674.530' o '674,530' -> 674530."""
    return int(re.sub(r"[.,]", "", txt))


_ESCAPE_RESIDUAL = re.compile(r"\\u([0-9a-fA-F]{4})|\\(/)")


def _desescapar(valor):
    r"""Deshace la capa de escapes que sobrevive a json.loads.

    El payload viene con doble barra (\\u002D, \\/), asi que json.loads consume
    una sola y deja el literal - sin resolver. Esta pasada lo termina.
    """
    if not isinstance(valor, str):
        return valor
    return _ESCAPE_RESIDUAL.sub(
        lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(2), valor
    )


def _payload_a_dict(onclick):
    r"""Desanida el escapado del onclick y devuelve el dict de parameters.

    onclick trae &quot; (HTML) envolviendo \" (string JS), y dentro los guiones
    van como \\u002D. Se deshace HTML, luego una capa de barras -> JSON valido.
    """
    js = html.unescape(onclick).replace('\\"', '"')
    inicio = js.find('"parameters":')
    if inicio == -1:
        return None
    inicio = js.find("{", inicio + len('"parameters":'))
    if inicio == -1:
        return None

    # Recorremos contando llaves para cerrar el objeto exacto, respetando
    # comillas y escapes (las sumillas traen llaves y comillas literales).
    profundidad, en_cadena, escapado = 0, False, False
    for i in range(inicio, len(js)):
        c = js[i]
        if escapado:
            escapado = False
            continue
        if c == "\\":
            escapado = True
        elif c == '"':
            en_cadena = not en_cadena
        elif not en_cadena:
            if c == "{":
                profundidad += 1
            elif c == "}":
                profundidad -= 1
                if profundidad == 0:
                    try:
                        crudo = json.loads(js[inicio : i + 1])
                    except json.JSONDecodeError:
                        return None
                    return {k: _desescapar(v) for k, v in crudo.items()}
    return None


def totales(html_texto):
    """(total_corpus, total_resultados) o (None, None) si no aparece."""
    m = _TOTAL.search(re.sub(r"<[^>]+>", "", html_texto))
    if not m:
        return None, None
    return _entero(m.group(1)), _entero(m.group(2))


def num_paginas(html_texto, por_pagina=10):
    """Cantidad de paginas del resultado, derivada del total."""
    _, resultados = totales(html_texto)
    if resultados is None:
        return None
    return (resultados + por_pagina - 1) // por_pagina


# Etiquetas en negrita del panel -> columna, para el modo de respaldo.
_ETIQUETAS = {
    "pretension/delito": "Pretension/Delito",
    "tipo resolucion": "Tipo de Resolucion",
    "fecha resolucion": "Fecha de Resolucion",
    "sala suprema": "Sala Suprema",
    "norma de derecho interno": "Norma de Derecho Interno (Articulo)",
    "sumilla": "Sumilla",
    "palabras clave": "Palabras Clave",
}


def _normalizar_etiqueta(texto):
    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return plano.strip().rstrip(":").strip().lower()


def _desde_html(panel):
    """Lee los campos del panel visible. Respaldo cuando el JSON viene roto."""
    fila = {}
    for etiqueta in panel.select("div.txtbold"):
        clave = _ETIQUETAS.get(_normalizar_etiqueta(etiqueta.get_text()))
        if not clave:
            continue
        valor = etiqueta.find_next_sibling("div")
        fila[clave] = valor.get_text(" ", strip=True) if valor else ""

    # Cabecera del panel: tipo de recurso y numero de expediente.
    cabecera = panel.select("div.rf-p-hdr span")
    if len(cabecera) >= 2:
        fila["Casacion/Apelacion"] = cabecera[0].get_text(strip=True)
        fila["Nro Expediente"] = cabecera[1].get_text(strip=True)
    return fila


def parsear_resultados(html_texto, especialidad="", pagina=0):
    """Devuelve una fila por resolucion de la pagina.

    Recorre los paneles (no los enlaces): asi el numero de filas siempre iguala
    al de resoluciones mostradas. Por cada panel intenta primero el payload
    JSON del boton "Ver ficha", y si viene malformado -que pasa: hay sumillas
    con comillas sin escapar en el origen- cae al HTML visible.
    """
    sopa = BeautifulSoup(html_texto, "html.parser")
    filas = []

    for panel in sopa.find_all("div", id=_ID_PANEL):
        enlace = panel.find("a", href=_HREF_DESCARGA)
        if not enlace:
            continue
        uuid = _HREF_DESCARGA.search(enlace["href"]).group(1)

        datos = None
        # BeautifulSoup ya deshace las entidades, asi que el atributo llega como
        # \"uuid\": se busca el nombre suelto, no entrecomillado.
        ficha = panel.find("a", onclick=re.compile(r"\buuid\b"))
        if ficha:
            datos = _payload_a_dict(ficha.get("onclick", ""))

        # El uuid sale del enlace de descarga y los campos del payload de "Ver
        # ficha": son dos sitios distintos del mismo panel. Si no coinciden, el
        # panel esta mezclando dos resoluciones y el payload no es de fiar, asi
        # que se cae al HTML visible, que si pertenece a este panel con certeza.
        descuadre = bool(datos) and datos.get("uuid") not in (None, uuid)

        if datos and "uuid" in datos and not descuadre:
            fila = {col: (datos.get(k) or "").strip() for k, col in CAMPOS.items()}
            fila["Origen"] = "json"
        else:
            fila = {col: "" for col in CAMPOS.values()}
            fila.update({k: v for k, v in _desde_html(panel).items()})
            fila["Origen"] = "html-descuadre" if descuadre else "html"

        fila["uuid"] = uuid
        if descuadre:
            fila["uuid_ficha"] = datos.get("uuid")
        fila["Link Resolucion"] = f"{BASE}/jurisprudenciaweb/ServletDescarga?uuid={uuid}"
        fila["Especialidad"] = especialidad
        fila["Pagina"] = pagina
        filas.append(fila)

    return filas


def contar_paneles(html_texto):
    """Cuantas resoluciones muestra la pagina, para cuadrar contra lo parseado."""
    return len(re.findall(r'id="formBuscador:repeat:\d+:j_idt\d+"\s+style="width:100%', html_texto))


def nombre_archivo(fila):
    """casacion-028632-2025 -> nombre base sin extension, en minusculas y ascii."""
    recurso = fila.get("Casacion/Apelacion", "") or "resolucion"
    recurso = (
        recurso.strip()
        .lower()
        .replace("ó", "o")
        .replace("í", "i")
        .replace("á", "a")
        .replace("é", "e")
        .replace("ú", "u")
        .replace("ñ", "n")
    )
    recurso = re.sub(r"[^a-z0-9]+", "-", recurso).strip("-")
    nro = re.sub(r"[^0-9-]", "", fila.get("Nro Expediente", ""))
    return f"{recurso}-{nro}" if nro else recurso
