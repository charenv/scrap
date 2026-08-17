"""Cosecha las 12 especialidades de la Corte Suprema con link de PDF garantizado.

El link (ServletDescarga?uuid=...) solo existe dentro del HTML del listado: ni
el export del sitio ni el numero de expediente lo contienen. Por eso hay que
recorrer las paginas. Lo que hace viable las 20 939 paginas es repartirlas
entre varias sesiones simultaneas, cada una con su propio JSESSIONID.

Reparto del trabajo: cada especialidad se corta en tramos de N paginas y los
workers van tomando tramos de una cola. Cada tramo se guarda en su propio
fichero, asi que no hacen falta locks y reanudar es saltarse los tramos ya
completos.

No guarda el HTML: solo la metadata y el link.

Uso:
    python3 cosecha.py                      # las 12, 4 workers
    python3 cosecha.py --workers 6
    python3 cosecha.py --especialidad Penal
    python3 cosecha.py --persona charen
"""

import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

import juris_parse as jp
from juris_client import ESPECIALIDADES, REPARTO, ClienteJuris
from juris_excel import SALIDA, escribir_excel, slug

TRAMO = 25  # paginas por tramo: corto, para no perder trabajo si se corta
CORTE_FALLOS = 8  # fallos seguidos dentro de un tramo antes de abandonarlo
TAMANO_PAGINA = 10  # el buscador sirve 10 resoluciones por pagina, salvo la ultima
INTENTOS_MEDIR = 3  # veces que se pide la pagina 1 antes de dar la medicion por perdida
ESPERA_MEDIR = 5.0  # segundos entre intentos de medicion

_imprimir = threading.Lock()


def log(mensaje):
    with _imprimir:
        print(mensaje, flush=True)


def ruta_tramo(especialidad, inicio, fin):
    return SALIDA / slug(especialidad) / "tramos" / f"p{inicio:05d}-{fin:05d}.json"


def tramos_de(especialidad, total_paginas):
    """Corta una especialidad en tramos, saltando los ya completos."""
    pendientes = []
    for inicio in range(1, total_paginas + 1, TRAMO):
        fin = min(inicio + TRAMO - 1, total_paginas)
        destino = ruta_tramo(especialidad, inicio, fin)
        if destino.exists():
            try:
                datos = json.loads(destino.read_text(encoding="utf-8"))
                if datos.get("completo"):
                    continue
            except json.JSONDecodeError:
                pass  # fichero a medias de una corrida cortada: se rehace
        pendientes.append((inicio, fin))
    return pendientes


def medir_paginas(cliente, especialidad):
    """Cuantas paginas tiene una especialidad. None si no se pudo averiguar.

    El sitio devuelve a ratos un 200 con el HTML incompleto, sin el contador de
    resultados. Eso no lanza excepcion: num_paginas() devuelve None y, si se
    trata como 0, la especialidad encola cero tramos y se salta en silencio,
    indistinguible de una ya terminada. Por eso se reintenta, y por eso el None
    se propaga en vez de colapsarse a 0.
    """
    for intento in range(1, INTENTOS_MEDIR + 1):
        try:
            html = cliente.pagina_con_reintentos(1, primera=True)
        except Exception as e:
            motivo = f"{type(e).__name__}: {e}"
        else:
            paginas = jp.num_paginas(html)
            if paginas is not None:
                return paginas
            motivo = "el HTML no traia el total de resultados"

        if intento < INTENTOS_MEDIR:
            log(f"    {especialidad}: no se pudo medir ({motivo}), "
                f"reintento {intento}/{INTENTOS_MEDIR - 1}")
            time.sleep(ESPERA_MEDIR)

    return None


def aviso_sin_medir(especialidades):
    """Deja constancia de las que no se pudieron medir. Nadie deberia perderselo."""
    log("\n" + "!" * 68)
    log("!! NO SE PUDIERON MEDIR, NO ESTAN COSECHADAS:")
    for especialidad in especialidades:
        log(f"!!   {especialidad}")
    log("!! Relanza el mismo comando: es reanudable y las tomara.")
    log("!" * 68)


def cosechar_tramo(cliente, especialidad, inicio, fin):
    """Recorre un tramo de paginas. Devuelve (filas, fallidas, incompletas).

    Una pagina vacia ya cuenta como fallo. Pero tambien las hay a medias: el
    sitio devuelve 9 de 10 resoluciones con un 200 valido, y esas si se
    aceptaban. Asi se perdieron 537 resoluciones de Civil sin dejar rastro,
    porque la cosecha termina "bien" igual, solo que con menos filas. Se anotan
    para poder reclamarlas despues.
    """
    filas, fallidas, incompletas, seguidas = [], [], [], 0

    for numero in range(inicio, fin + 1):
        try:
            html = cliente.pagina_con_reintentos(numero)
        except Exception as e:
            nuevas = None
        else:
            nuevas = jp.parsear_resultados(html, especialidad, numero)
            mostradas = jp.contar_paneles(html)
            if mostradas != len(nuevas):
                log(f"    !! {especialidad} p{numero}: {mostradas} en el HTML, {len(nuevas)} parseadas")

        # Una pagina vacia cuenta como fallo. Ninguna pagina del rango puede
        # estar vacia de verdad: los tramos se cortan sobre el total real, asi
        # que hasta la ultima trae al menos una fila. Cuando el sitio pierde la
        # sesion devuelve un listado sin resultados con un 200 perfectamente
        # valido, y aceptarlo marcaba el tramo como completo con un hueco
        # dentro: paginas que nadie volvia a pedir porque constaban por hechas.
        if not nuevas:
            fallidas.append(numero)
            seguidas += 1
            if seguidas >= CORTE_FALLOS:
                log(f"    {especialidad} p{inicio}-{fin}: {seguidas} fallos seguidos, abandono el tramo")
                fallidas.extend(range(numero + 1, fin + 1))
                break
            continue

        seguidas = 0
        if len(nuevas) < TAMANO_PAGINA:
            incompletas.append(numero)
            log(f"    !! {especialidad} p{numero}: {len(nuevas)} filas de "
                f"{TAMANO_PAGINA} (normal solo si es la ultima del listado)")
        descuadradas = [f for f in nuevas if "uuid_ficha" in f]
        if descuadradas:
            log(f"    !! {especialidad} p{numero}: {len(descuadradas)} panel(es) con "
                f"la ficha apuntando a otro uuid que el PDF; leidos del HTML visible")
        filas.extend(nuevas)

    return filas, fallidas, incompletas


def worker(nombre, cola, pausa):
    while True:
        try:
            especialidad, inicio, fin = cola.get_nowait()
        except queue.Empty:
            return

        try:
            cliente = ClienteJuris(especialidad=especialidad, pausa=pausa)
            cliente.pagina_con_reintentos(1, primera=True)
            filas, fallidas, incompletas = cosechar_tramo(
                cliente, especialidad, inicio, fin)

            destino = ruta_tramo(especialidad, inicio, fin)
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(
                json.dumps(
                    {
                        "completo": not fallidas,
                        "fallidas": fallidas,
                        "incompletas": incompletas,
                        "filas": filas,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            marca = "" if not fallidas else f"  ({len(fallidas)} paginas fallidas)"
            if incompletas:
                marca += f"  ({len(incompletas)} paginas incompletas)"
            log(f"  [{nombre}] {especialidad} p{inicio}-{fin}: {len(filas)} filas{marca}")

        except Exception as e:
            log(f"  [{nombre}] {especialidad} p{inicio}-{fin} FALLO: {type(e).__name__}: {e}")
        finally:
            cola.task_done()


def juntar(especialidad):
    """Une los tramos de una especialidad en su Excel. Devuelve las filas."""
    carpeta = SALIDA / slug(especialidad)
    directorio = carpeta / "tramos"
    if not directorio.exists():
        return []

    filas, vistos = [], set()
    for fichero in sorted(directorio.glob("p*.json")):
        try:
            datos = json.loads(fichero.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for fila in datos["filas"]:
            # Un reintento puede repetir una pagina; el uuid es la identidad.
            if fila["uuid"] not in vistos:
                vistos.add(fila["uuid"])
                filas.append(fila)

    filas.sort(key=lambda f: f["Pagina"])
    if filas:
        escribir_excel(filas, carpeta / f"{slug(especialidad)}.xlsx")
    return filas


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--persona", choices=sorted(REPARTO))
    ap.add_argument("--especialidad", choices=sorted(ESPECIALIDADES))
    # Medido: con 2 sesiones simultaneas desde la misma IP se pierde la mitad de
    # las paginas, porque el buscador ata el estado de busqueda a la IP. Para ir
    # mas rapido hay que repartir por maquinas, no por hilos.
    ap.add_argument("--workers", type=int, default=1, help="sesiones simultaneas (1: no subir)")
    ap.add_argument("--pausa", type=float, default=1.2, help="segundos entre peticiones")
    ap.add_argument("--solo-juntar", action="store_true",
                    help="rehace los Excel con lo cosechado hasta ahora, sin pedir nada al sitio")
    args = ap.parse_args()

    if args.especialidad:
        objetivo = [args.especialidad]
    elif args.persona:
        objetivo = REPARTO[args.persona]
    else:
        # De la mas pequena a la mas grande: en una corrida de horas conviene
        # tener especialidades enteras terminadas cuanto antes.
        orden = ["Familia Tutelar", "Familia Penal", "Comercial", "Familia Civil",
                 "Constitucional", "Revision de Procedimiento Coactivo",
                 "Contencioso Adm. Previsional", "Civil", "Contencioso Administrativo",
                 "Contencioso Adm. Laboral", "Laboral", "Penal"]
        objetivo = [e for e in orden if e in ESPECIALIDADES]

    # todo-corte-suprema.xlsx solo tiene sentido cuando la corrida abarca las 12.
    # Con --persona o --especialidad, "todas" es un trozo del corpus y escribirlo
    # con ese nombre invita a entregar un cuarto del trabajo creyendolo entero.
    corpus_completo = not args.persona and not args.especialidad

    # Rehacer los Excel con lo que haya en disco, sin tocar el sitio. Sirve para
    # mirar el avance a mitad de una corrida larga sin interferir con ella.
    if args.solo_juntar:
        todas = []
        for especialidad in objetivo:
            filas = juntar(especialidad)
            todas.extend(filas)
            if filas:
                con_link = sum(1 for f in filas if f["Link Resolucion"])
                log(f"  {especialidad:36} {len(filas):>7} filas  {con_link:>7} con link")
        if len(objetivo) > 1 and todas:
            for persona, suyas in REPARTO.items():
                filas = [f for f in todas if f["Especialidad"] in suyas]
                if filas:
                    escribir_excel(filas, SALIDA / f"{persona}-consolidado.xlsx")
            if corpus_completo:
                escribir_excel(todas, SALIDA / "todo-corte-suprema.xlsx")
        log(f"\nTOTAL: {len(todas)} resoluciones")
        return

    # Cuantas paginas tiene cada una (una peticion por especialidad).
    log("midiendo...")
    cola = queue.Queue()
    total_paginas = 0
    sin_medir = []
    for especialidad in objetivo:
        cliente = ClienteJuris(especialidad=especialidad, pausa=args.pausa)
        paginas = medir_paginas(cliente, especialidad)
        if paginas is None:
            sin_medir.append(especialidad)
            log(f"  {especialidad:36} {'??':>5} paginas  NO SE PUDO MEDIR, queda pendiente")
            continue
        pendientes = tramos_de(especialidad, paginas)
        total_paginas += sum(f - i + 1 for i, f in pendientes)
        for inicio, fin in pendientes:
            cola.put((especialidad, inicio, fin))
        log(f"  {especialidad:36} {paginas:>5} paginas  {len(pendientes):>3} tramos pendientes")

    if sin_medir:
        aviso_sin_medir(sin_medir)

    if cola.empty():
        log("\nno queda nada pendiente de lo que se pudo medir"
            if sin_medir else "\nno queda nada pendiente")
    else:
        estimado = total_paginas * 3.0 / args.workers / 3600
        log(f"\n{total_paginas} paginas en {cola.qsize()} tramos, "
            f"{args.workers} workers, ~{estimado:.1f} h\n")

        t0 = time.time()
        hilos = [
            threading.Thread(target=worker, args=(f"w{i+1}", cola, args.pausa), daemon=True)
            for i in range(args.workers)
        ]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        log(f"\ncosecha terminada en {(time.time()-t0)/3600:.2f} h")

    # Excel por especialidad, por persona y global.
    log("")
    todas = []
    for especialidad in objetivo:
        filas = juntar(especialidad)
        todas.extend(filas)
        con_link = sum(1 for f in filas if f["Link Resolucion"])
        log(f"  {especialidad:36} {len(filas):>7} filas  {con_link:>7} con link")

    if len(objetivo) > 1:
        for persona, suyas in REPARTO.items():
            filas = [f for f in todas if f["Especialidad"] in suyas]
            if filas:
                escribir_excel(filas, SALIDA / f"{persona}-consolidado.xlsx")
        if corpus_completo:
            escribir_excel(todas, SALIDA / "todo-corte-suprema.xlsx")
            log(f"\nTOTAL: {len(todas)} resoluciones -> salida/todo-corte-suprema.xlsx")
        else:
            log(f"\nTOTAL de esta parte: {len(todas)} resoluciones")
            log("(es tu parte del reparto, no el corpus entero: manda salida/ a charen)")

    # Repetido al final a proposito: tras horas de corrida, el aviso de la fase
    # de medicion ya se perdio scroll arriba. El codigo de salida != 0 es para
    # que se note aunque nadie lea.
    if sin_medir:
        aviso_sin_medir(sin_medir)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
