#Corte Suprema

Extrae las resoluciones de la Corte Suprema de
[jurisprudencia.pj.gob.pe](https://jurisprudencia.pj.gob.pe/jurisprudenciaweb/faces/page/inicio.xhtml)
a Excel: la metadata de cada casación/apelación **más el link de descarga de su PDF**.

No descarga los PDF, solo el link. Ese link es la razón de ser del proyecto: no aparece
en el Excel oficial del sitio ni se puede deducir del número de expediente, así que hay
que recorrer las páginas de resultados de diez en diez.

## Resultado

Corpus cosechado entre el **13 y el 15 de agosto de 2026**:

| Especialidad | Resoluciones |
|---|---:|
| Penal | 70 912 |
| Laboral | 31 886 |
| Contencioso Adm. Laboral | 24 724 |
| Contencioso Administrativo | 24 114 |
| Contencioso Adm. Previsional | 14 528 |
| Civil | 14 198 |
| Revisión de Procedimiento Coactivo | 13 549 |
| Constitucional | 11 937 |
| Familia Civil | 1 834 |
| Comercial | 517 |
| Familia Penal | 270 |
| Familia Tutelar | 172 |
| **Total** | **208 641** |

Las 208 641 con link, ninguna sin él. El sitio anunciaba 209 344 al empezar; la diferencia
es que el corpus cambia a diario, no trabajo perdido.

## Instalación

```bash
git clone https://github.com/charenv/scrap.git
cd scrap
pip install requests beautifulsoup4 openpyxl
```

Esas tres bastan para el flujo normal. `requirements.txt` incluye además `pandas`/`xlrd`,
que solo usa `exportar.py`, y `playwright`, que solo usa la exploración inicial.

## Uso

El flujo son tres pasos: cosechar, comprobar, juntar.

```bash
python3 cosecha.py --persona charen     # 1. cosechar lo que a uno le toca
python3 auditar.py                      # 2. comprobar que no quedaron huecos
python3 cosecha.py --solo-juntar        # 3. armar los Excel
```

La cosecha tarda horas y **es reanudable**: si se corta por lo que sea, se relanza el mismo
comando y sigue por donde iba. Guarda un checkpoint cada 25 páginas.

Otras formas de lanzarla:

```bash
python3 cosecha.py                          # las 12 especialidades
python3 cosecha.py --especialidad Penal     # solo una
```

Para que sobreviva al cierre de la terminal:

```bash
nohup python3 cosecha.py --persona charen > cosecha.log 2>&1 &
tail -f cosecha.log
```

### Comprobar antes de dar nada por hecho

`auditar.py` no se fía del log ni de lo que el propio scraper creyó que había hecho:
reconstruye qué páginas hay en disco y las compara con el rango que debería haber.

```bash
python3 auditar.py            # informe
python3 auditar.py --borrar   # quita los tramos con huecos para que se rehagan
```

Solo está terminado cuando dice `Sin huecos. Esto esta completo.` y sale con código 0.

### Importar datos cosechados por fuera

Si una parte del corpus se obtuvo con otro código y solo existe como Excel, `importar.py`
lo verifica y lo convierte a tramos normales. Recupera el `uuid` del propio link, revisa
que no falten páginas ni haya duplicados, y solo escribe si sale limpio.

```bash
python3 importar.py archivo.xlsx --persona larijo             # revisa
python3 importar.py archivo.xlsx --persona larijo --escribir  # mete
```

No pisa una especialidad que ya tenga tramos cosechados, salvo con `--forzar`.

## Los archivos

| Archivo | Qué hace |
|---|---|
| `cosecha.py` | **El que se corre.** Recorre las páginas y arma los Excel. |
| `auditar.py` | Comprueba que no falten páginas y dice qué tramos rehacer. |
| `importar.py` | Mete en el corpus un Excel cosechado por fuera, verificándolo antes. |
| `exportar.py` | Verificación cruzada: baja el Excel oficial del sitio para contrastar totales. |
| `juris_client.py` | Habla con el sitio: sesión JSF, búsqueda, paginación, reintentos. Define las 12 especialidades y el reparto. |
| `juris_parse.py` | Saca de cada HTML las 10 resoluciones con su metadata y su link. |
| `juris_excel.py` | Escribe el Excel. Las columnas viven aquí. |
| `exploracion/` | La exploración inicial con Playwright y las notas de planificación. Referencia, no se usa. |

## Qué deja

```
salida/
├── laboral/
│   ├── tramos/p00001-00025.json    ← checkpoint por tramo de 25 páginas
│   └── laboral.xlsx
├── charen-consolidado.xlsx          ← la parte de una persona
└── todo-corte-suprema.xlsx          ← el corpus entero (solo si se corren las 12)
```

Los `tramos/*.json` son el dato bruto y lo único que no se puede regenerar: los Excel se
reconstruyen de ellos en segundos con `--solo-juntar`.

Columnas: Casación/Apelación · Nro Expediente · Pretensión/Delito · Tipo de Resolución ·
Fecha de Resolución · Sala Suprema · Norma de Derecho Interno · Sumilla · Palabras Clave ·
**Link Resolución** · Especialidad · Página

## Reparto

Como no se puede paralelizar desde una misma IP, el trabajo se repartió por personas y
cada una corrió lo suyo en su propia máquina. Las tres a la vez tardaron ~6 h en lugar
de ~17.

| Persona | Especialidades | Resoluciones |
|---|---|---:|
| charen | Revisión de Proc. Coactivo, Laboral, Contencioso Adm. Laboral, Contencioso Administrativo | 94 273 |
| joe | Penal, Familia Civil, Familia Penal, Constitucional | 84 953 |
| larijo | Familia Tutelar, Civil, Contencioso Adm. Previsional, Comercial | 29 415 |

Se cambia en `REPARTO`, dentro de `juris_client.py`.

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
   Por eso `--workers` está en 1 y no conviene subirlo.

Y un detalle de los datos: **hay sumillas con comillas sin escapar** que rompen el propio
formato del sitio. `juris_parse.py` lee el JSON que el sitio incrusta en cada resultado y,
cuando viene malformado, cae al HTML visible.

## Por qué hay tanta comprobación

El sitio falla de formas que parecen éxitos, y las tres veces el síntoma fue el mismo:
algo vacío o ausente tratado como algo terminado.

- Una especialidad cuyo total no se pudo leer encolaba **cero** páginas y se saltaba en
  silencio, igual que una ya terminada.
- Una página devuelta vacía con un HTTP 200 válido cerraba el tramo con un hueco dentro,
  y la reanudación ya no volvía a pedirla porque constaba por hecha.
- Un tramo con huecos que no estaba en disco no aparecía en la lista de cosas por rehacer,
  y el informe daba el corpus por completo.

Los tres perdían datos sin una sola línea de aviso. De ahí que `auditar.py` verifique el
resultado en vez del proceso, y que nada se dé por terminado sin que lo confirme.

## Por qué el link obliga a paginar

El `uuid` de `ServletDescarga?uuid=…` es aleatorio y **solo aparece dentro del HTML del
listado**: ni el número de expediente lo contiene ni el Excel oficial del sitio lo trae.
Por eso no hay atajo. Si el link viniera en el export oficial, este proyecto no existiría.
