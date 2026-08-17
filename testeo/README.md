# Corpus de la Corte Suprema — descarga, markdown y OCR

Segunda parte del proyecto. La primera (`../cosecha.py`) sacó los metadatos y el
enlace al PDF de cada resolución; esto se encarga de **bajar los PDFs, pasarlos a
markdown y dejarlos listos para el RAG**.

209 180 resoluciones, 1982-2026, 12 especialidades.

---

## Preparar la máquina

Todo son scripts de Python, así que funcionan igual en Windows y en Linux. Lo
único que cambia es cómo se instalan las herramientas externas.

### Linux (Fedora)

```bash
pip install --user pandas openpyxl requests
sudo dnf install poppler-utils                    # pdftotext
sudo dnf install ocrmypdf tesseract-langpack-spa tesseract-langpack-osd   # fase 2
```

### Windows

```powershell
pip install pandas openpyxl requests

winget install oschwartz10612.Poppler             # pdftotext
winget install UB-Mannheim.TesseractOCR           # fase 2
pip install ocrmypdf                              # fase 2
```

Poppler y Tesseract **tienen que quedar en el PATH**. Compruébalo con:

```powershell
pdftotext -v
tesseract --version
```

Si no responden, añade sus carpetas `bin` al PATH desde *Variables de entorno*
y abre una terminal nueva.

### Las rutas

Cada uno usa las suyas. En Linux, al final del `~/.bashrc`:

```bash
export CORPUS_BASE=/ruta/a/tu/disco/corpus-corte-suprema
export CORPUS_MANIFIESTO=/ruta/a/manifiesto-pdfs.csv
export CORPUS_EXCEL=/ruta/a/todo-corte-suprema-completo-limpio.xlsx
```

En Windows, en PowerShell y **una sola vez** (quedan guardadas):

```powershell
setx CORPUS_BASE "D:\corpus-corte-suprema"
setx CORPUS_MANIFIESTO "D:\datos\manifiesto-pdfs.csv"
setx CORPUS_EXCEL "D:\datos\todo-corte-suprema-completo-limpio.xlsx"
```

Cierra y vuelve a abrir la terminal para que las coja.

El manifiesto y el Excel están en la carpeta compartida de Drive. Son los mismos
para los tres: **no los regeneres**, o las rutas de los ficheros dejarían de
coincidir entre máquinas.

---

## Reparto

El portal limita a ~660 PDFs/hora **por IP**, así que cada uno descarga desde su
casa y los tres avanzamos en paralelo. Los años se repartieron buscando el
equilibrio más ajustado posible: 3 % de diferencia entre el que más y el que
menos.

| | Años | Documentos | Descarga |
|---|---|---|---|
| **charen** | 2025, 2024, 2020, 2014, 2011, 2009, 2007 | 66 342 | ~101 h |
| **joe** | 2023, 2021, 2019, 2015, 2013, 2010 | 68 347 | ~104 h |
| **larijo** | 2022, 2018, 2017, 2016, 2012, 2008, 2006, 2005, 2004, 2003, 2002, 2001, 2000, 1999, 1998, 1982 | 67 258 | ~102 h |

2026 ya está hecho y sirve de prueba para el RAG.

Larijo tiene muchos más años porque los anteriores a 2007 son diminutos —2004
tiene 1 documento, 1982 tiene 1— pero suman lo mismo que los demás.

---

## El ciclo, un año por vuelta

```bash
python3 lote.py --persona joe      # encadena todos tus años, del más nuevo al más viejo
```

Descarga y convierte año por año sin parar. Cuando uno termina avisa por
terminal y con notificación de escritorio.

**Cuando avise:**

1. Sube a Drive `pdfs/*/AÑO/` y `markdown/*/AÑO/`
2. `python3 lote.py --subido AÑO` — borra los PDFs y **conserva el markdown**

El markdown son ~200 MB por año y es lo que alimenta el RAG. No lo borres: lo
vas a usar constantemente.

### No espera a que subas

Al terminar un año **arranca el siguiente sin pausa**. El aviso es para que
subas cuando puedas, no para que el script se detenga.

Eso significa que necesitas sitio para varios años a la vez. Si te quedas
corto, el script se para solo con `ESPACIO INSUFICIENTE` antes de empezar el
año siguiente: subes, liberas con `--subido`, y relanzas `--persona`.

Relanzar es seguro: **los años ya marcados como subidos se saltan**, no se
vuelven a descargar aunque sus PDFs ya no estén.

Si prefieres controlarlo año por año, usa `--anio` en vez de `--persona`.

### Cuánto sitio hace falta

Los años de 2017 en adelante ocupan ~2,5 GB cada uno. Los anteriores son
escaneos y ocupan unas siete veces más. Contando todo el reparto de cada uno:

| | Fase 1 | Fase 2 | Total |
|---|---|---|---|
| charen | ~13 GB | ~53 GB | ~66 GB |
| joe | ~13 GB | ~57 GB | ~70 GB |
| larijo | ~14 GB | ~50 GB | ~64 GB |

Si vas subiendo y liberando conforme termina cada año, con 15 GB libres te
sobra.

### Un año suelto

```bash
python3 lote.py --anio 2019
```

### Ver cómo va

```bash
python3 lote.py --estado      # tabla de todos los años
python3 avance.py 2019        # progreso del lote en curso
```

### Si se corta

El mismo comando. Todo es reanudable: detecta los PDFs ya bajados y válidos y
solo pide lo que falta. No se pierde nada.

---

## OCR — solo para 2016 hacia atrás

De 2017 en adelante los PDFs traen texto y `pdftotext` los lee directamente. Los
anteriores son escaneos: `a_markdown.py` los aparta en `necesitan-ocr.csv` en vez
de fallar, y hay que pasarles OCR antes de que sirvan de nada.

```bash
python3 ocr.py --pdfs $CORPUS_BASE/pdfs --anio 2013 --jobs 8     # Linux
python3 ocr.py --pdfs $env:CORPUS_BASE\pdfs --anio 2013 --jobs 8  # PowerShell
python3 lote.py --anio 2013 --solo-md      # recoge el texto nuevo
```

Pon en `--jobs` el número de núcleos que tengas.

`ocrmypdf` añade una capa de texto sobre la imagen, así que después el resto del
pipeline funciona igual. Además reduce el tamaño a la mitad.

Es el paso caro: ~10 s por documento. Si tienes acceso a una máquina con muchos
núcleos, este es el trabajo que merece la pena mover allí.

---

## Al juntarlo todo

No hay conflictos posibles: cada resolución tiene un `uuid` único y su ruta se
deriva de él, así que **basta con copiar las carpetas unas encima de otras**.
Ningún fichero de dos personas distintas puede llamarse igual.

```
corpus-corte-suprema/
├── pdfs/<especialidad>/<año>/<recurso>-<expediente>-<uuid8>.pdf
└── markdown/<especialidad>/<año>/<recurso>-<expediente>-<uuid8>.md
```

Para comprobar que no falta nada al final:

```bash
python3 lote.py --estado
```

---

## Los scripts

| | |
|---|---|
| `lote.py` | Orquesta el ciclo. Es el único que necesitas en el día a día. |
| `descargar.py` | Descarga respetando la cuota del portal. |
| `a_markdown.py` | Limpia, secciona y escribe el markdown con frontmatter. |
| `ocr.py` | Capa de texto sobre los escaneos. |
| `avance.py` | Progreso del lote en curso. |

Y los de charen, que no hacen falta en el día a día:

| | |
|---|---|
| `limpieza.py` | Regenera el Excel normalizado y el manifiesto. |
| `recuperar.py` | Reclama filas perdidas en páginas incompletas de la cosecha. |
| `verificar_enlaces.py`, `medir_pdfs.py`, `probar_limite.py` | Diagnóstico. |

---

## Cosas que conviene saber

**El portal responde `200 OK` con un cuerpo vacío** cuando se agota la cuota, en
vez de dar un error. Por eso los scripts validan la cabecera `%PDF` y no el
código de estado: mirando el status guardarías miles de ficheros de cero bytes
creyendo que todo fue bien.

**El límite es por IP, no por sesión.** Renovar la cookie no lo esquiva
(comprobado). Son 20 descargas por ventana de ~60 s, y ninguna configuración de
cliente lo mejora: lo único que multiplica el ritmo es repartir entre máquinas.

**`--subido` solo borra los PDFs que ya tienen su markdown.** Un escaneo sin
texto extraído es la única copia que existe hasta pasarle OCR, y no se toca.

**Unos pocos PDFs vienen corruptos desde el portal** (imagen truncada). Los
scripts los apartan en `fallos.csv` y `fallos-ocr.csv` en vez de reventar.

**Usa `uuid` como clave, nunca el número de expediente.** Hay 155 380
expedientes para 209 180 resoluciones, y 24 177 se repiten entre especialidades
siendo casos completamente distintos.
