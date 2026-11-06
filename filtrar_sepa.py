#!/usr/bin/env python3
"""Filtra el paquete SEPA (zip) y deja solo las sucursales de una zona.

Uso:
    python filtrar_sepa.py sepa_martes.zip
    python filtrar_sepa.py sepa_martes.zip --zona "Mar del Plata" --zona "Batan" --salida salida_mdp
    python filtrar_sepa.py carpeta_con_zips/

Genera en la carpeta de salida: comercio.csv, sucursales.csv y productos.csv,
listos para cargar en el buscador de precios.
Solo usa la biblioteca estándar de Python (3.8 o superior).
"""
import argparse, csv, io, os, sys, unicodedata, zipfile
from collections import defaultdict

csv.field_size_limit(10_000_000)

# Columnas de productos que usa el buscador (con --liviano se descartan las demás)
COLS_LIVIANAS = {
    "id_comercio", "id_bandera", "id_sucursal", "productos_ean", "productos_descripcion",
    "productos_cantidad_presentacion", "productos_unidad_medida_presentacion",
    "productos_marca", "productos_precio_lista", "productos_precio_unitario_promo1",
}


def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def paquetes(zf):
    """Devuelve grupos de CSV {nombre_base: (zip, info)}; entra a zips anidados."""
    grupos = defaultdict(dict)
    for info in zf.infolist():
        nombre = info.filename
        if nombre.endswith("/"):
            continue
        bajo = nombre.lower()
        if bajo.endswith(".zip"):
            try:
                anidado = zipfile.ZipFile(io.BytesIO(zf.read(info)))
            except zipfile.BadZipFile:
                print("  (zip dañado, lo salteo):", nombre)
                continue
            yield from paquetes(anidado)
        elif bajo.endswith(".csv"):
            grupos[os.path.dirname(nombre)][os.path.basename(bajo)] = (zf, info)
    yield from grupos.values()


def leer(zf, info):
    """Itera filas de un CSV del zip. Devuelve (encabezado, delimitador, filas)."""
    texto = io.TextIOWrapper(zf.open(info), encoding="utf-8-sig", errors="replace", newline="")
    primera = texto.readline()
    delim = "|" if primera.count("|") > primera.count(",") else ","
    quoting = csv.QUOTE_NONE if delim == "|" else csv.QUOTE_MINIMAL
    texto.seek(0)
    lector = csv.reader(texto, delimiter=delim, quoting=quoting)
    cab = [c.strip().lower() for c in next(lector)]
    return cab, delim, lector


def indice(cab, nombre, archivo):
    if nombre not in cab:
        sys.exit(f"No encuentro la columna '{nombre}' en {archivo}. Columnas: {cab}")
    return cab.index(nombre)


class Salida:
    def __init__(self, ruta, delim):
        self.f = open(ruta, "w", encoding="utf-8", newline="")
        if delim == "|":  # el buscador no interpreta comillas en archivos con |
            self.w = csv.writer(self.f, delimiter=delim, quoting=csv.QUOTE_NONE, quotechar=None, lineterminator="\n")
        else:
            self.w = csv.writer(self.f, delimiter=delim, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        self.cab = None
        self.n = 0

    def escribir(self, cab, fila):
        if self.cab is None:
            self.cab = cab
            self.w.writerow(cab)
        elif cab != self.cab:
            return  # encabezado distinto: se omite para no mezclar columnas
        self.w.writerow(fila)
        self.n += 1


def procesar(grupo, zonas, out, liviano=False):
    if not all(k in grupo for k in ("sucursales.csv", "productos.csv")):
        return 0
    cab, delim, filas = leer(*grupo["sucursales.csv"])
    i_c, i_b, i_s = (indice(cab, k, "sucursales.csv") for k in ("id_comercio", "id_bandera", "id_sucursal"))
    i_l = cab.index("sucursales_localidad") if "sucursales_localidad" in cab else -1
    i_p = cab.index("sucursales_provincia") if "sucursales_provincia" in cab else -1
    if i_l < 0 and i_p < 0:
        sys.exit(f"sucursales.csv no tiene localidad ni provincia. Columnas: {cab}")
    claves, banderas = set(), set()
    if out.get("delim") is None:
        out["delim"] = delim
        for n in ("comercio", "sucursales", "productos"):
            out[n] = Salida(os.path.join(out["dir"], n + ".csv"), delim)
    for f in filas:
        if len(f) <= max(i_c, i_b, i_s):
            continue
        txt = norm((f[i_l] if i_l >= 0 and i_l < len(f) else "") + " " + (f[i_p] if 0 <= i_p < len(f) else ""))
        if any(z in txt for z in zonas):
            claves.add((f[i_c], f[i_b], f[i_s]))
            banderas.add((f[i_c], f[i_b]))
            out["sucursales"].escribir(cab, f)
    if not claves:
        return 0
    if "comercio.csv" in grupo:
        cab_c, _, filas_c = leer(*grupo["comercio.csv"])
        ic, ib = indice(cab_c, "id_comercio", "comercio.csv"), indice(cab_c, "id_bandera", "comercio.csv")
        for f in filas_c:
            if len(f) > max(ic, ib) and (f[ic], f[ib]) in banderas:
                out["comercio"].escribir(cab_c, f)
    cab_p, _, filas_p = leer(*grupo["productos.csv"])
    pc, pb, ps = (indice(cab_p, k, "productos.csv") for k in ("id_comercio", "id_bandera", "id_sucursal"))
    m = max(pc, pb, ps)
    sel = [i for i, c in enumerate(cab_p) if c in COLS_LIVIANAS] if liviano else None
    cab_w = [cab_p[i] for i in sel] if sel else cab_p
    for f in filas_p:
        if len(f) > m and (f[pc], f[pb], f[ps]) in claves:
            if sel:
                if len(f) < len(cab_p):
                    continue
                f = [f[i] for i in sel]
            out["productos"].escribir(cab_w, f)
    return len(claves)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("origen", help="zip del SEPA o carpeta con zips")
    ap.add_argument("--zona", action="append", help="localidad o provincia a conservar (se puede repetir)")
    ap.add_argument("--salida", default="salida_sepa", help="carpeta de salida")
    ap.add_argument("--liviano", action="store_true", help="guarda solo las columnas que usa el buscador (archivo mucho más chico)")
    a = ap.parse_args()
    zonas = [norm(z) for z in (a.zona or ["Mar del Plata"])]
    os.makedirs(a.salida, exist_ok=True)
    out = {"dir": a.salida, "delim": None}
    if os.path.isdir(a.origen):
        zips = [os.path.join(a.origen, f) for f in sorted(os.listdir(a.origen)) if f.lower().endswith(".zip")]
    else:
        zips = [a.origen]
    if not zips:
        sys.exit("No encontré archivos .zip en esa ruta.")
    sucursales = 0
    for z in zips:
        print("Leyendo", z)
        with zipfile.ZipFile(z) as zf:
            for grupo in paquetes(zf):
                sucursales += procesar(grupo, zonas, out, a.liviano)
    if out["delim"] is None or sucursales == 0:
        sys.exit("No encontré sucursales en esa zona. Probá con otra --zona o revisá el archivo.")
    for n in ("comercio", "sucursales", "productos"):
        out[n].f.close()
    print(f"Listo: {sucursales} sucursales, {out['productos'].n:,} precios en '{a.salida}'.")


if __name__ == "__main__":
    main()
