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


def cosechar_tramo(cliente, especialidad, inicio, fin):
    """Recorre un tramo de paginas. Devuelve (filas, paginas_fallidas)."""
    filas, fallidas, seguidas = [], [], 0

    for numero in range(inicio, fin + 1):
        try:
            html = cliente.pagina_con_reintentos(numero)
        except Exception as e:
            fallidas.append(numero)
            seguidas += 1
            if seguidas >= CORTE_FALLOS:
                log(f"    {especialidad} p{inicio}-{fin}: {seguidas} fallos seguidos, abandono el tramo")
                fallidas.extend(range(numero + 1, fin + 1))
                break
            continue
        seguidas = 0

        nuevas = jp.parsear_resultados(html, especialidad, numero)
        mostradas = jp.contar_paneles(html)
        if mostradas != len(nuevas):
            log(f"    !! {especialidad} p{numero}: {mostradas} en el HTML, {len(nuevas)} parseadas")
        filas.extend(nuevas)

    return filas, fallidas


def worker(nombre, cola, pausa):
    while True:
        try:
            especialidad, inicio, fin = cola.get_nowait()
        except queue.Empty:
            return

        try:
            cliente = ClienteJuris(especialidad=especialidad, pausa=pausa)
            cliente.pagina_con_reintentos(1, primera=True)
            filas, fallidas = cosechar_tramo(cliente, especialidad, inicio, fin)

            destino = ruta_tramo(especialidad, inicio, fin)
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(
                json.dumps(
                    {"completo": not fallidas, "fallidas": fallidas, "filas": filas},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            marca = "" if not fallidas else f"  ({len(fallidas)} paginas fallidas)"
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

    # Cuantas paginas tiene cada una (una peticion por especialidad).
    log("midiendo...")
    cola = queue.Queue()
    total_paginas = 0
    for especialidad in objetivo:
        cliente = ClienteJuris(especialidad=especialidad, pausa=args.pausa)
        try:
            # Con reintentos: el sitio devuelve 500 a ratos y no vale la pena
            # tirar la corrida entera solo por contar paginas.
            html = cliente.pagina_con_reintentos(1, primera=True)
        except Exception as e:
            log(f"  {especialidad:36} no se pudo medir ({type(e).__name__}), se salta")
            continue
        paginas = jp.num_paginas(html) or 0
        pendientes = tramos_de(especialidad, paginas)
        total_paginas += sum(f - i + 1 for i, f in pendientes)
        for inicio, fin in pendientes:
            cola.put((especialidad, inicio, fin))
        log(f"  {especialidad:36} {paginas:>5} paginas  {len(pendientes):>3} tramos pendientes")

    if cola.empty():
        log("\nno queda nada pendiente")
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
        escribir_excel(todas, SALIDA / "todo-corte-suprema.xlsx")
        log(f"\nTOTAL: {len(todas)} resoluciones -> salida/todo-corte-suprema.xlsx")


if __name__ == "__main__":
    sys.exit(main())
