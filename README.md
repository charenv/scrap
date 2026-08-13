# khipux — Corte Suprema

Extrae las resoluciones de la Corte Suprema de
[jurisprudencia.pj.gob.pe](https://jurisprudencia.pj.gob.pe/jurisprudenciaweb/faces/page/inicio.xhtml)
a Excel: metadata de cada casación/apelación **más el link de descarga de su PDF**.

**209 344 resoluciones** en 12 especialidades, 20 939 páginas de 10.
No se descargan los PDF, solo el link.

## Instalación

```bash
git clone <este-repo>
cd scrap
pip install -r requirements.txt
```

Con `requests`, `beautifulsoup4` y `openpyxl` basta para `cosecha.py`. `pandas`/`xlrd`
solo hacen falta para `exportar.py`, y `playwright` solo para `joe.py`.

## Uso

```bash
python3 cosecha.py                       # las 12 especialidades
python3 cosecha.py --persona charen      # solo las del reparto de una persona
python3 cosecha.py --especialidad Penal
```

Es reanudable: si se corta, relanza el mismo comando y sigue por donde iba.

```bash
tail -f cosecha.log      # ver avance
grep '!!' cosecha.log    # ver problemas; si no sale nada, todo limpio
```

## Los archivos

| Archivo | Qué es |
|---|---|
| `cosecha.py` | **El que se corre.** Recorre las páginas y arma los Excel. |
| `juris_client.py` | Habla con el sitio: sesión JSF, búsqueda, paginación, reintentos. Define las 12 especialidades y el reparto. |
| `juris_parse.py` | Saca de cada HTML las 10 resoluciones con su metadata y su link. |
| `juris_excel.py` | Escribe el Excel. Las columnas viven aquí. |
| `exportar.py` | Verificación cruzada: baja el Excel oficial del sitio (rápido, sin links) para contrastar totales. |
| `joe.py` | La exploración inicial con Playwright, de joe. Referencia. |

## Qué deja

```
salida/
├── laboral/
│   ├── tramos/p00001-00025.json    ← checkpoint por tramo de 25 páginas
│   └── laboral.xlsx
├── charen-consolidado.xlsx
└── todo-corte-suprema.xlsx
```

Columnas: Casación/Apelación · Nro Expediente · Pretensión/Delito · Tipo de Resolución ·
Fecha de Resolución · Sala Suprema · Norma de Derecho Interno · Sumilla · Palabras Clave ·
**Link Resolución** · Especialidad · Página

## Reparto

| Persona | Especialidades | Resoluciones |
|---|---|---|
| larijo | Familia Tutelar, Civil, Contencioso Adm. Previsional, Comercial | 30 079 |
| charen | Revisión de Proc. Coactivo, Laboral, Contencioso Adm. Laboral, Contencioso Administrativo | 94 311 |
| joe | Penal, Familia Civil, Familia Penal, Constitucional | 84 954 |

Se cambia en `REPARTO`, en `juris_client.py`.

Cada persona corre lo suyo **en su propia máquina** (ver punto 4 de abajo: desde una misma
IP no se puede paralelizar). Las tres a la vez tardan ~6 h en lugar de ~17.

## Lo que hay que saber del sitio

Cuatro cosas que costaron encontrarse y que explican por qué el código es como es:

1. **No hay API.** Es JSF 2 + RichFaces: el servidor reconstruye la búsqueda desde los
   campos de cada POST. Por eso hay que reenviar el formulario **entero** en cada
   petición; mandar solo el número de página devuelve 0 resultados.

2. **El servidor redirige a `http://` pero solo escucha en `https://`.** Le falta el
   `X-Forwarded-Proto`. Hay que reescribir los redirects a mano.

3. **Un HTTP 500 significa "tu sesión murió", no "hubo un problema de red".** Reintentar
   el mismo POST no funciona nunca: hay que rehacer la búsqueda y volver a navegar.

4. **No se puede paralelizar desde una misma IP.** Medido: 1 sesión → 6/6 páginas;
   2 sesiones → 6/12. El buscador ata el estado a la IP y las sesiones se pisan.
   Para ir más rápido hay que repartir por máquinas, cada persona la suya.

Y un detalle de los datos: **hay sumillas con comillas sin escapar** que rompen el propio
formato del sitio. `juris_parse.py` lee el JSON que el sitio incrusta en cada resultado y,
cuando viene malformado, cae al HTML visible. Además avisa si el número de filas no
coincide con las resoluciones mostradas, porque perder filas en silencio es el fallo
peligroso.

## Por qué el link obliga a paginar

El `uuid` de `ServletDescarga?uuid=…` es aleatorio y **solo aparece dentro del HTML del
listado**: ni el número de expediente lo contiene ni el Excel oficial del sitio lo trae.
Por eso no hay atajo: para tener el link hay que recorrer las páginas de 10 en 10.
