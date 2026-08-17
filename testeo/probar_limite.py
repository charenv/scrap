"""Averigua que desbloquea el corte del servlet de descarga.

El servidor sirve exactamente 20 PDFs y luego responde 200 OK con text/plain
vacio. Saber si el contador va por sesion o por IP decide el plan entero:
renovar cookie cuesta cero, esperar cuesta dias.

Pruebas, en orden:
  A  cuantas descargas aguanta una sesion limpia (confirma el 20)
  B  sesion nueva justo despues del corte, sin esperar
  C  si B falla, cuanto hay que esperar: 15s, 30s, 60s, 120s
  D  misma sesion tras la espera que funcionase en C

Si B pasa, el contador es por sesion y la descarga la limita solo el ancho de
banda. Si B falla y C pasa, es por IP con ventana temporal y hay que pacear.
Si tambien falla C, el limite es mas duro y toca replantear.

Uso:
    python3 probar_limite.py
"""

import sys
import time
from pathlib import Path

import pandas as pd
import requests

CORPUS = Path("/home/charen/corpus-corte-suprema-2026-08-15/scrap/salida/"
              "todo-corte-suprema-completo-limpio.xlsx")
CABECERAS = {"User-Agent": "Mozilla/5.0"}


def sesion_nueva():
    s = requests.Session()
    s.headers.update(CABECERAS)
    return s


def baja(sesion, url, timeout=60):
    """True si vino un PDF de verdad."""
    try:
        r = sesion.get(url, timeout=timeout)
    except Exception:
        return False
    return r.content.startswith(b"%PDF")


def main():
    urls = (pd.read_excel(CORPUS, engine="openpyxl")["Link Resolucion"]
            .sample(200, random_state=99).tolist())
    siguiente = iter(urls)

    print("A. cuantas descargas aguanta una sesion limpia")
    s = sesion_nueva()
    aguanto = 0
    while aguanto < 40:
        if not baja(s, next(siguiente)):
            break
        aguanto += 1
        time.sleep(1)
    print(f"   -> corto tras {aguanto} descargas\n")

    print("B. sesion NUEVA inmediatamente, sin esperar")
    ok_b = baja(sesion_nueva(), next(siguiente))
    print(f"   -> {'FUNCIONA: el contador es por sesion' if ok_b else 'falla: no basta la cookie'}\n")

    if ok_b:
        print("C. cuantas mas aguanta esa sesion nueva")
        s2, n = sesion_nueva(), 0
        while n < 25:
            if not baja(s2, next(siguiente)):
                break
            n += 1
            time.sleep(1)
        print(f"   -> {n} descargas mas\n")
        print("VEREDICTO: renovar sesion cada ~20 descargas y seguir. "
              "Sin esperas, la descarga la limita el ancho de banda.")
        return 0

    print("C. cuanto hay que esperar (con sesion nueva cada intento)")
    for espera in (15, 30, 60, 120):
        print(f"   esperando {espera}s...", flush=True)
        time.sleep(espera)
        if baja(sesion_nueva(), next(siguiente)):
            print(f"   -> con {espera}s funciona\n")
            total = 209179 / 20 * espera / 3600
            print(f"VEREDICTO: limite por IP con ventana de ~{espera}s. "
                  f"209.179 PDFs = {total:.0f} h solo de pausas ({total/24:.1f} dias).")
            return 0
        print(f"   -> con {espera}s sigue cortado")

    print("\nVEREDICTO: no se desbloquea ni esperando 2 min. "
          "El limite es mas duro; habria que probar desde otra IP o pedir acceso al PJ.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
