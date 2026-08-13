"""Cliente HTTP para el buscador de jurisprudencia del Poder Judicial.

El sitio es JSF 2 + RichFaces 4.2.2, no una API. Eso impone tres reglas:

1. Cada respuesta trae un javax.faces.ViewState que hay que reenviar en el
   siguiente POST; si se pierde, el servidor responde ViewExpiredException.
2. La sesion vive en la cookie JSESSIONID, cuyo sufijo (.jvmr-scjurispN) es la
   ruta al nodo del balanceador. Si cambia de nodo, el estado de busqueda se
   pierde y la pagina de resultados vuelve vacia.
3. El servidor redirige a http:// aunque solo escucha en https (le falta el
   X-Forwarded-Proto). Hay que reescribir los redirects a mano.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://jurisprudencia.pj.gob.pe"
INICIO = BASE + "/jurisprudenciaweb/faces/page/inicio.xhtml"

# Valores del <select buEspecialidad> para Corte Suprema (buCorte=1).
ESPECIALIDADES = {
    "Civil": 1,
    "Comercial": 2,
    "Contencioso Administrativo": 3,
    "Familia Civil": 4,
    "Familia Tutelar": 5,
    "Familia Penal": 6,
    "Contencioso Adm. Laboral": 7,
    "Contencioso Adm. Previsional": 8,
    "Laboral": 9,
    "Penal": 10,
    "Revision de Procedimiento Coactivo": 11,
    "Constitucional": 12,
}

# Reparto de trabajo acordado en el README.
REPARTO = {
    "larijo": ["Familia Tutelar", "Civil", "Contencioso Adm. Previsional", "Comercial"],
    "charen": [
        "Revision de Procedimiento Coactivo",
        "Laboral",
        "Contencioso Adm. Laboral",
        "Contencioso Administrativo",
    ],
    "joe": ["Penal", "Familia Civil", "Familia Penal", "Constitucional"],
}

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_VIEWSTATE = re.compile(r'name="javax\.faces\.ViewState"[^>]*value="([^"]*)"')
_ACTION = re.compile(r'<form id="formBuscador"[^>]*action="([^"]*)"')


def _campos_formulario(html_texto):
    """Todos los campos de formBuscador con su valor actual, como los mandaria el navegador."""
    sopa = BeautifulSoup(html_texto, "html.parser")
    form = sopa.find("form", id="formBuscador")
    campos = {}
    if not form:
        return campos

    for etiqueta in form.find_all(["input", "select", "textarea"]):
        nombre = etiqueta.get("name")
        if not nombre or etiqueta.get("disabled") is not None:
            continue
        if etiqueta.name == "select":
            elegida = etiqueta.find("option", selected=True) or etiqueta.find("option")
            campos[nombre] = elegida.get("value", "") if elegida else ""
        elif etiqueta.name == "textarea":
            campos[nombre] = etiqueta.get_text()
        else:
            tipo = (etiqueta.get("type") or "text").lower()
            # Los botones solo viajan si son el que se pulso; los añade quien invoca.
            if tipo in ("submit", "image", "button", "reset"):
                continue
            if tipo in ("checkbox", "radio") and etiqueta.get("checked") is None:
                continue
            campos[nombre] = etiqueta.get("value", "")
    return campos


def _boton_ir(html_texto):
    """Id del boton IR del paginador, que cambia de nombre entre versiones."""
    m = re.search(r'name="(formBuscador:j_idt\d+)"[^>]*value="IR"', html_texto)
    return m.group(1) if m else "formBuscador:j_idt447"


class SesionExpirada(RuntimeError):
    """El servidor perdio el estado de busqueda (ViewState o nodo caido)."""


class _SesionHttps(requests.Session):
    """Sesion que fuerza https en los redirects rotos del servidor."""

    def send(self, request, **kwargs):
        if request.url.startswith("http://"):
            request.url = "https://" + request.url[7:]
        return super().send(request, **kwargs)


class ClienteJuris:
    def __init__(self, especialidad=None, pausa=1.5, reintentos=4, timeout=120):
        self.pausa = pausa
        self.reintentos = reintentos
        self.timeout = timeout
        self.sesion = None
        self.viewstate = None
        self.action = None
        # Se fija al construir para que un reintento de la pagina 1 sepa que
        # busqueda rehacer, y buscar() la reconfirma.
        self.especialidad_actual = especialidad
        self._ultimo_html = ""

    # ---------- infraestructura ----------

    def _nueva_sesion(self):
        self.sesion = _SesionHttps()
        self.sesion.headers.update({"User-Agent": UA, "Accept-Language": "es-PE,es;q=0.9"})
        r = self.sesion.get(INICIO, timeout=self.timeout)
        r.raise_for_status()
        self._absorber(r.text)

    def _absorber(self, html_texto):
        """Guarda el ViewState y el action de la respuesta para el proximo POST."""
        vs = _VIEWSTATE.search(html_texto)
        ac = _ACTION.search(html_texto)
        if not vs or not ac:
            raise SesionExpirada("la respuesta no trae ViewState/action")
        self.viewstate = vs.group(1)
        self.action = ac.group(1)

    def _post(self, datos):
        """POST del formulario siguiendo el redirect a mano (POST-Redirect-GET)."""
        time.sleep(self.pausa)
        datos = dict(datos, **{"javax.faces.ViewState": self.viewstate})
        r = self.sesion.post(
            BASE + self.action,
            data=datos,
            timeout=self.timeout,
            allow_redirects=False,
            headers={"Referer": INICIO, "Origin": BASE},
        )
        if r.status_code in (301, 302, 303, 307):
            destino = r.headers["Location"].replace("http://", "https://")
            time.sleep(self.pausa)
            r = self.sesion.get(destino, timeout=self.timeout, headers={"Referer": INICIO})
        # Un 500 aqui no es un fallo transitorio de red: en JSF significa casi
        # siempre que la vista murio. Reintentar el mismo POST no funciona
        # nunca; hay que rehacer la sesion. Se clasifica antes de
        # raise_for_status() para no confundirlo con un error de red.
        if r.status_code >= 500:
            raise SesionExpirada(f"HTTP {r.status_code}: la vista JSF murio")
        r.raise_for_status()
        if "ViewExpired" in r.text:
            raise SesionExpirada("ViewExpiredException del servidor")
        self._absorber(r.text)
        self._ultimo_html = r.text
        return r.text

    # ---------- operaciones del buscador ----------

    def buscar(self, especialidad):
        """Lanza la busqueda de Corte Suprema para una especialidad. Devuelve pagina 1."""
        codigo = ESPECIALIDADES[especialidad]
        self._nueva_sesion()
        self.especialidad_actual = especialidad
        return self._post(
            {
                "formBuscador": "formBuscador",
                "formBuscador:txtBusqueda": "",
                "formBuscador:buCorte": "1",
                "formBuscador:buDistrito": "0",
                "formBuscador:buEspecialidad": str(codigo),
                "formBuscador:buSala": "",
                "formBuscador:buPretensionDelitoSupValue": "",
                "formBuscador:buPretensionDelitoSupInput": "",
                "formBuscador:buPretensionValue": "",
                "formBuscador:buPretensionInput": "",
                "formBuscador:buPalabraClaveValue": "",
                "formBuscador:buPalabraClaveInput": "",
                "formBuscador:buNroExpediente": "",
                "formBuscador:buAnio": "",
                "formBuscador:j_idt69": "formBuscador:j_idt69",
                "forward": "buscar",
                "formBuscador:j_idt71": "21",
                "formBuscador:j_idt72": "DESC",
                "formBuscador:j_idt73": "Principal",
                "formBuscador:j_idt74": "1",
            }
        )

    def ir_a_pagina(self, numero):
        """Salta a una pagina reenviando el formulario completo con el spinner.

        JSF no acepta un POST parcial: si solo se manda el spinner, el bean
        reconstruye la busqueda con los filtros vacios y devuelve 0 filas. Hay
        que reenviar todos los campos con sus valores actuales.
        """
        campos = _campos_formulario(self._ultimo_html)
        boton = _boton_ir(self._ultimo_html)
        campos.update(
            {
                "formBuscador": "formBuscador",
                "formBuscador:spinner": str(numero),
                "formBuscador:spinner2": str(numero),
                boton: "IR",
            }
        )
        campos.pop("javax.faces.ViewState", None)
        return self._post(campos)

    def exportar_excel(self, timeout=600):
        """Pide el .xls con la totalidad del resultado de la busqueda actual.

        Es el boton "Exportar" de la pagina de resultados. Devuelve los bytes
        del fichero. Una sola peticion trae todas las filas, pero el .xls no
        incluye el uuid ni el link de descarga del PDF: eso solo vive en el
        HTML del listado.
        """
        m = re.search(
            r"mojarra\.jsfcljs\(document\.getElementById\('formBuscador'\),"
            r"\{'(formBuscador:j_idt\d+)':'[^']*'\},'_blank'\)",
            self._ultimo_html,
        )
        if not m:
            raise RuntimeError("no se encontro el boton de exportar")

        campos = _campos_formulario(self._ultimo_html)
        campos["formBuscador"] = "formBuscador"
        campos[m.group(1)] = m.group(1)
        campos.pop("javax.faces.ViewState", None)
        campos["javax.faces.ViewState"] = self.viewstate

        time.sleep(self.pausa)
        r = self.sesion.post(
            BASE + self.action, data=campos, timeout=timeout, allow_redirects=False
        )
        if r.status_code >= 500:
            raise SesionExpirada(f"HTTP {r.status_code} al exportar")
        r.raise_for_status()
        if "excel" not in (r.headers.get("Content-Type") or ""):
            raise RuntimeError(f"el servidor no devolvio un Excel: {r.headers.get('Content-Type')}")
        return r.content

    def recuperar(self, pagina):
        """Reabre la busqueda y vuelve a posicionarse en una pagina concreta.

        Se llama cuando el servidor pierde el estado a mitad de la corrida.
        Devuelve el HTML de esa pagina.
        """
        self.buscar(self.especialidad_actual)
        if pagina > 1:
            return self.ir_a_pagina(pagina)
        return self._ultimo_html

    # ---------- politica de resiliencia ----------

    def pagina_con_reintentos(self, numero, primera=False):
        """Pide una pagina tolerando caidas de red y expiraciones de sesion.

        Distingue los dos modos de fallo del sitio, que necesitan respuestas
        opuestas:

          - RequestException: timeout o 500 puntual. La sesion JSF sigue viva,
            asi que se repite la misma llamada con backoff exponencial.
          - SesionExpirada: el servidor perdio el estado (tipicamente porque el
            balanceador mando la peticion a otro nodo). Repetir el POST no
            sirve nunca: hay que rehacer la busqueda y renavegar.

        Si se agotan los intentos lanza la excepcion; decide quien llama si
        aborta o si anota la pagina como fallida y sigue.
        """
        espera = self.pausa
        ultimo_error = None

        for intento in range(1, self.reintentos + 1):
            try:
                if primera:
                    return self.buscar(self.especialidad_actual)
                return self.ir_a_pagina(numero)

            except SesionExpirada as e:
                ultimo_error = e
                print(f"    [pag {numero}] sesion perdida, rehaciendo busqueda "
                      f"(intento {intento}/{self.reintentos})")
                try:
                    return self.recuperar(numero)
                except (SesionExpirada, requests.RequestException) as e2:
                    ultimo_error = e2

            except requests.RequestException as e:
                ultimo_error = e
                print(f"    [pag {numero}] error de red: {type(e).__name__} "
                      f"(intento {intento}/{self.reintentos})")

            time.sleep(espera)
            espera = min(espera * 2, 60)

        raise ultimo_error
