"""
Scraper para el Buscador de Jurisprudencia del Poder Judicial (jurisprudencia.pj.gob.pe)

Estrategia:
- Es una app JSF/RichFaces: la URL nunca cambia, todo se actualiza por AJAX (ViewState).
- Usamos Playwright para simular la interacción real (clics en buscar/filtrar/paginar)
  y dejamos que el navegador maneje el ViewState.
- El sitio tiene un detalle raro: al navegar a resultado.xhtml, el primer intento se va
  por HTTP (puerto 80, que no responde -> "connection refused") y recién el reintento
  por HTTPS funciona. Por eso interceptamos esa navegación y forzamos HTTPS directo.

Flujo real del sitio:
    1. Se entra a inicio.xhtml (navegación normal).
    2. Se marca "Especializada", se elige "Penal" en el combobox, click en "Buscar".
       -> Esto redirige (navegación real) a resultado.xhtml.
    3. En resultado.xhtml los resultados vienen de 10 en 10. Pasar de página es AJAX puro
       (la URL no cambia). Ahí usamos click -> esperar a que el AJAX actualice el DOM -> parsear -> repetir.

Instalación:
    pip install playwright beautifulsoup4 pandas openpyxl
    playwright install chromium

"""

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL_INICIO = "https://jurisprudencia.pj.gob.pe/jurisprudenciaweb/faces/page/inicio.xhtml"
URL_RESULTADO = "https://jurisprudencia.pj.gob.pe/jurisprudenciaweb/faces/page/resultado.xhtml"

# Para probar que todo funciona sin recorrer las ~7092 páginas.
LIMITE_PAGINAS = 10


def parsear_resultados(html: str) -> list[dict]:
    """Extrae todos los bloques .rf-p (cada resolución) de un HTML ya renderizado."""
    soup = BeautifulSoup(html, "html.parser")
    registros = []

    for bloque in soup.select('div.rf-p[id*="formBuscador:repeat"]'):
        spans_header = bloque.select(".rf-p-hdr span")
        tipo_recurso = spans_header[0].get_text(strip=True) if len(spans_header) > 0 else None
        nro_expediente = spans_header[1].get_text(strip=True) if len(spans_header) > 1 else None

        campos = {}
        for fila in bloque.select(".row"):
            columnas = fila.select(".col-sm-4, .col-sm-8, .col-sm-12")
            for col in columnas:
                label_tag = col.select_one(".txtbold")
                if not label_tag:
                    continue
                valor_tag = label_tag.find_next_sibling("div")
                if valor_tag:
                    campos[label_tag.get_text(strip=True).rstrip(":")] = valor_tag.get_text(strip=True)

        link_tag = bloque.select_one("a[href*='ServletDescarga']")
        url_descarga = None
        uuid = None
        if link_tag and link_tag.get("href"):
            href = link_tag["href"]
            url_descarga = href if href.startswith("http") else f"https://jurisprudencia.pj.gob.pe{href}"
            if "uuid=" in href:
                uuid = href.split("uuid=")[-1]

        registros.append({
            "tipo_recurso": tipo_recurso,
            "nro_expediente": nro_expediente,
            "pretension_delito": campos.get("Pretensión/Delito"),
            "tipo_resolucion": campos.get("Tipo Resolución"),
            "fecha_resolucion": campos.get("Fecha Resolución"),
            "sala_suprema": campos.get("Sala Suprema"),
            "norma_derecho_interno": campos.get("Norma de Derecho Interno"),
            "sumilla": campos.get("Sumilla"),
            "palabras_clave": campos.get("Palabras Clave"),
            "uuid": uuid,
            "url_descarga": url_descarga,
        })

    return registros


def main():
    todos_los_registros = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
        )
        page = context.new_page()

        # -----------------------------------------------------------------
        # El botón "Buscar" intenta navegar primero por HTTP (puerto 80,
        # que no responde -> connection refused). Solo BLOQUEAMOS esa
        # petición mala acá; el reintento por HTTPS lo hacemos aparte, en
        # el flujo principal (llamar a page.goto() desde dentro del propio
        # handler de route choca con la navegación que ya está en curso).
        # -----------------------------------------------------------------
        def bloquear_http_resultado(route, request):
            if request.method == "GET" and request.url.startswith("http://") and "resultado.xhtml" in request.url:
                print(f"🔀 Bloqueando intento por HTTP: {request.url}")
                route.abort()
            else:
                route.continue_()

        page.route("**/resultado.xhtml", bloquear_http_resultado)

        try:
            # --- FASE 1: inicio.xhtml -> resultado.xhtml ---
            print("Abriendo página inicial...")
            page.goto(URL_INICIO, wait_until="networkidle", timeout=30000)

            page.click("#formBuscador\\:especializada\\:header\\:inactive")
            page.select_option("#formBuscador\\:buEspecialidad", value="10") # Se cambia segun la categoria
            page.wait_for_timeout(800)

            page.click("input[name='formBuscador:j_idt69']")

            # Dejamos que se resuelva (o falle) el primer intento, sin abortar el script si truena.
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass

            # Si el intento por HTTP fue bloqueado (o simplemente falló), reintentamos
            # explícitamente por HTTPS, como paso separado y limpio.
            if "resultado.xhtml" not in page.url or "chromewebdata" in page.url:
                print("Reintentando navegación directa por HTTPS...")
                page.goto(URL_RESULTADO, wait_until="domcontentloaded", timeout=30000)

            page.wait_for_selector("div.rf-p", timeout=20000)

            print(f"URL después de Buscar: {page.url}")
        except Exception as e:
            page.screenshot(path="error_debug.png", full_page=True)
            with open("error_debug.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"\n Falló en la búsqueda. URL actual: {page.url}")
            print(f"Error: {e}")
            print("Revisa error_debug.png / error_debug.html para ver qué se veía.")
            browser.close()
            return

        # --- FASE 2: resultado.xhtml -> paginación AJAX de 10 en 10 ---
        pagina = 1
        while True:
            try:
                page.wait_for_selector("div.rf-p", timeout=15000)
            except Exception:
                print("\n No se encontraron resultados en esta página.")
                break

            page.wait_for_load_state("networkidle")

            html = page.content()
            registros = parsear_resultados(html)
            print(f"Página {pagina}: {len(registros)} registros")
            todos_los_registros.extend(registros)

            boton_siguiente = page.query_selector("#formBuscador\\:data1_ds_next")
            if not boton_siguiente:
                print("No hay botón 'siguiente' -> última página.")
                break

            clase = boton_siguiente.get_attribute("class") or ""
            if "rf-ds-btn-dis" in clase or "dis" in clase.split():
                print("Botón 'siguiente' deshabilitado -> última página.")
                break

            if LIMITE_PAGINAS and pagina >= LIMITE_PAGINAS:
                print(f"Límite de {LIMITE_PAGINAS} páginas alcanzado (modo prueba).")
                break

            primer_uuid_antes = registros[0]["uuid"] if registros else None

            # El panel de "cargando" (shade overlay) de RichFaces a veces tarda en
            # desaparecer y tapa los clics. Esperamos a que se oculte antes de clickear.
            try:
                page.wait_for_selector("#panelState_shade", state="hidden", timeout=5000)
            except Exception:
                pass  # no estaba visible, seguimos sin problema

            boton_siguiente.click(force=True)

            page.wait_for_function(
                """(uuidAntes) => {
                    const bloque = document.querySelector('div.rf-p a[href*="ServletDescarga"]');
                    if (!bloque) return false;
                    const href = bloque.getAttribute('href') || '';
                    return !href.includes(uuidAntes);
                }""",
                arg=primer_uuid_antes,
                timeout=15000,
            )
            pagina += 1

        browser.close()

    # --- Dedup por uuid y exportar ---
    df = pd.DataFrame(todos_los_registros)
    if df.empty:
        print("\n No se recolectó ningún registro. Revisa error_debug.png si existe.")
        return

    df = df.drop_duplicates(subset="uuid")
    df.to_excel("resoluciones.xlsx", index=False)
    print(f"\n✅ Listo: {len(df)} resoluciones guardadas en resoluciones.xlsx")


if __name__ == "__main__":
    main()