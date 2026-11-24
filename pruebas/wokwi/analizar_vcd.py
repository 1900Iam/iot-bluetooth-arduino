#!/usr/bin/env python3
"""Analiza la captura del analizador lógico de Wokwi (archivo .vcd).

Uso:
    python3 analizar_vcd.py captura_bt.vcd [carpeta_de_salida]

Canales esperados (atributo channelNames del analizador en diagram.json):
    D0 RX_D10       comandos que llegan a Arduino (lo que enviaría el HC-05)
    D1 TX_D11       respuestas de Arduino hacia el HC-05
    D2 LED_VERDE_D2
    D3 LED_ROJO_D3
    D4 USB_TX_D1    registro CSV que Arduino publica por USB

Genera en la carpeta de salida:
    resultados_por_comando.csv   una fila por línea de comando recibida
    estadisticas_latencia.csv    media, desviación, mínimo y máximo por comando
    registro_bt.txt              respuestas decodificadas de D11
    registro_usb.txt             registro CSV decodificado de D1
    fig_entradas_salidas.png     señales de entrada y salida en el tiempo
    fig_latencias.png            tiempos de respuesta por comando

Solo necesita Python 3 y matplotlib (pip3 install matplotlib).
"""
import bisect
import csv
import os
import statistics
import sys

BAUDIOS = 9600
BIT = 1.0 / BAUDIOS
UNIDADES = {'s': 1, 'ms': 1e-3, 'us': 1e-6, 'ns': 1e-9, 'ps': 1e-12, 'fs': 1e-15}
ESPERADO = {'V': (1, 0), 'R': (0, 1), 'A': (1, 1), '0': (0, 0)}
ORDEN = ['V', 'R', 'A', '0', 'X']


# ---------------------------------------------------------------- lectura VCD
def leer_vcd(ruta):
    """Devuelve {nombre_canal: (tiempos_s, valores)} a partir de un VCD."""
    escala = 1e-9
    codigos = {}
    cambios = {}
    t = 0.0
    with open(ruta, encoding='utf-8', errors='replace') as f:
        texto = f.read()
    encabezado, _, cuerpo = texto.partition('$enddefinitions')
    tokens = encabezado.split()
    for i, tok in enumerate(tokens):
        if tok == '$timescale':
            valor = ''.join(tokens[i + 1:tokens.index('$end', i)])
            num = ''.join(ch for ch in valor if ch.isdigit()) or '1'
            uni = ''.join(ch for ch in valor if ch.isalpha())
            escala = int(num) * UNIDADES.get(uni, 1e-9)
        if tok == '$var':
            # $var wire 1 <codigo> <nombre> $end
            codigos[tokens[i + 3]] = tokens[i + 4]
    for nombre in codigos.values():
        cambios[nombre] = ([], [])
    for linea in cuerpo.split('\n'):
        linea = linea.strip()
        if not linea or linea.startswith('$'):
            continue
        if linea[0] == '#':
            t = int(linea[1:]) * escala
            continue
        if linea[0] in '01xzXZ':
            valor, codigo = linea[0], linea[1:]
        elif linea[0] in 'bB':
            partes = linea[1:].split()
            valor, codigo = partes[0][-1], partes[1]
        else:
            continue
        if codigo in codigos:
            ts, vs = cambios[codigos[codigo]]
            v = 1 if valor == '1' else 0
            if ts and ts[-1] == t:
                vs[-1] = v
            else:
                ts.append(t)
                vs.append(v)
    return cambios


def canal(cambios, *claves):
    """Busca un canal por nombre exacto (o terminado en .nombre); si no, por coincidencia parcial."""
    def corto(n):
        return n.lower().rsplit('.', 1)[-1]
    for c in claves:
        for nombre, datos in cambios.items():
            if corto(nombre) == c.lower():
                return datos
    for nombre, datos in cambios.items():
        if claves[0].lower() in nombre.lower():
            return datos
    raise SystemExit(f'No se encontró el canal {claves} en la captura. Canales: {list(cambios)}')


class Senal:
    def __init__(self, datos):
        self.t, self.v = datos

    def nivel(self, t, inicial=0):
        i = bisect.bisect_right(self.t, t) - 1
        return self.v[i] if i >= 0 else inicial

    def flancos(self, desde, hasta):
        i = bisect.bisect_right(self.t, desde)
        res = []
        while i < len(self.t) and self.t[i] < hasta:
            if i > 0 and self.v[i] != self.v[i - 1]:
                res.append((self.t[i], self.v[i]))
            i += 1
        return res


def decodificar_uart(s):
    """Decodifica 8N1 a 9600 baudios: lista de (inicio_s, fin_s, byte)."""
    res = []
    fin_anterior = -1.0
    for k in range(1, len(s.t)):
        if s.v[k] != 0 or s.v[k - 1] != 1 or s.t[k] < fin_anterior:
            continue
        inicio = s.t[k]
        # la línea debe haber estado en reposo (alta) al menos medio bit antes
        if s.t[k] - s.t[k - 1] < 0.5 * BIT and fin_anterior < 0:
            continue
        if s.nivel(inicio + 0.5 * BIT, 1) != 0:
            continue  # falso bit de inicio
        b = 0
        for bit in range(8):
            b |= s.nivel(inicio + (1.5 + bit) * BIT, 1) << bit
        if s.nivel(inicio + 9.5 * BIT, 1) != 1:
            continue  # error de trama: falta el bit de parada
        fin = inicio + 10 * BIT
        res.append((inicio, fin, b))
        fin_anterior = inicio + 9.5 * BIT
    return res


def lineas_de_texto(bytes_):
    """Agrupa bytes en líneas terminadas en \\n: lista de dicts."""
    lineas, cur = [], None
    for ini, fin, b in bytes_:
        if cur is None:
            cur = {'inicio': ini, 'texto': ''}
        if b == 10:
            cur['fin'] = fin
            lineas.append(cur)
            cur = None
        elif b != 13:
            cur['texto'] += chr(b) if 32 <= b < 127 else f'<0x{b:02X}>'
    return lineas


def lineas_de_comandos(bytes_):
    """Agrupa los bytes recibidos en líneas: bytes seguidos con menos de 12 bits entre sí."""
    lineas = []
    for ini, fin, b in bytes_:
        if lineas and ini - lineas[-1]['fin'] < 2 * BIT:
            lineas[-1]['fin'] = fin
            lineas[-1]['bytes'] += chr(b)
        else:
            lineas.append({'inicio': ini, 'fin': fin, 'bytes': chr(b)})
    return lineas


def visible(s):
    return s.replace('\r', '\\r').replace('\n', '\\n')


# -------------------------------------------------------------------- análisis
def analizar(ruta_vcd, salida):
    os.makedirs(salida, exist_ok=True)
    cambios = leer_vcd(ruta_vcd)
    rx = Senal(canal(cambios, 'RX_D10', 'D0'))
    tx = Senal(canal(cambios, 'TX_D11', 'D1'))
    verde = Senal(canal(cambios, 'LED_VERDE_D2', 'D2'))
    rojo = Senal(canal(cambios, 'LED_ROJO_D3', 'D3'))
    try:
        usb = Senal(canal(cambios, 'USB_TX_D1', 'D4'))
    except SystemExit:
        usb = None

    resp = lineas_de_texto(decodificar_uart(tx))
    cmds = [l for l in lineas_de_comandos(decodificar_uart(rx))
            if l['bytes'].replace('\r', '').replace('\n', '').strip()]
    t_final = max(verde.t[-1] if verde.t else 0, tx.t[-1] if tx.t else 0) + 1
    if not cmds:
        print('La captura no tiene comandos recibidos por D10: la simulación no llegó a enviar ninguno.')
        print('Revisa que el escenario haya encontrado "Sistema listo" (paso wait-serial).')
        return [], []

    filas, previo = [], (0, 0)
    for i, l in enumerate(cmds):
        siguiente = cmds[i + 1]['inicio'] if i + 1 < len(cmds) else t_final
        r = next((x for x in resp if l['fin'] < x['inicio'] < siguiente), None)
        cm = l['bytes'].replace('\r', '').replace('\n', '').strip().upper()
        valido = cm in ESPERADO
        esp = ESPERADO.get(cm, previo)
        despues = (verde.nivel(siguiente - 1e-6), rojo.nivel(siguiente - 1e-6))
        cambios_led = [t for t, _ in verde.flancos(l['fin'], siguiente)[:1] + rojo.flancos(l['fin'], siguiente)[:1]]
        t_led = min(cambios_led) if cambios_led else None
        ok_msg = r is not None and (('OK ' + cm) in r['texto'] if valido else 'ERROR' in r['texto'])
        filas.append({
            'n': i + 1,
            'enviado': visible(l['bytes']),
            'comando': cm,
            'valido': valido,
            't_envio_ms': round(l['inicio'] * 1000, 3),
            'lat_led_ms': round((t_led - l['fin']) * 1000, 3) if t_led else '',
            'lat_resp_ms': round((r['inicio'] - l['fin']) * 1000, 3) if r else '',
            'dur_resp_ms': round((r['fin'] - r['inicio']) * 1000, 2) if r else '',
            'verde': despues[0], 'rojo': despues[1],
            'esperado': f'{esp[0]}{esp[1]}',
            'cumple': bool(despues == esp and ok_msg),
            'respuesta': r['texto'] if r else '',
        })
        previo = despues

    with open(os.path.join(salida, 'resultados_por_comando.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)

    # Estadísticas: solo las líneas en forma estándar "<comando>\r\n"
    est = []
    for c in ORDEN:
        g = [x for x in filas if x['enviado'] == f'{c}\\r\\n']
        if not g:
            continue
        fila = {'comando': c, 'rep': len(g), 'cumplen': sum(x['cumple'] for x in g)}
        for clave, col in (('led', 'lat_led_ms'), ('resp', 'lat_resp_ms')):
            vals = [x[col] for x in g if x[col] != '']
            if vals:
                fila.update({f'{clave}_media': round(statistics.mean(vals), 3),
                             f'{clave}_sd': round(statistics.stdev(vals), 3) if len(vals) > 1 else 0,
                             f'{clave}_min': min(vals), f'{clave}_max': max(vals)})
        dur = [x['dur_resp_ms'] for x in g if x['dur_resp_ms'] != '']
        fila['dur_media'] = round(statistics.mean(dur), 2) if dur else ''
        est.append(fila)
    campos = ['comando', 'rep', 'cumplen', 'led_media', 'led_sd', 'led_min', 'led_max',
              'resp_media', 'resp_sd', 'resp_min', 'resp_max', 'dur_media']
    with open(os.path.join(salida, 'estadisticas_latencia.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(est)

    with open(os.path.join(salida, 'registro_bt.txt'), 'w', encoding='utf-8') as f:
        for r in resp:
            f.write(f"{r['inicio'] * 1000:10.3f} ms  {r['texto']}\n")
    if usb is not None:
        with open(os.path.join(salida, 'registro_usb.txt'), 'w', encoding='utf-8') as f:
            for r in lineas_de_texto(decodificar_uart(usb)):
                f.write(r['texto'] + '\n')

    graficas(filas, est, rx, tx, verde, rojo, cmds, resp, salida)

    total = len(filas)
    ok = sum(x['cumple'] for x in filas)
    print(f'Líneas de comando: {total} · cumplen: {ok} · válidas: {sum(x["valido"] for x in filas)}')
    for e in est:
        print(f"  {e['comando']}: {e['rep']} rep · cumplen {e['cumplen']} · respuesta media "
              f"{e.get('resp_media', '-')} ms (mín {e.get('resp_min', '-')}, máx {e.get('resp_max', '-')})")
    fallas = [x for x in filas if not x['cumple']]
    for x in fallas:
        print(f"  NO CUMPLE: línea {x['n']} '{x['enviado']}' -> {x['respuesta']}")
    return filas, est


# -------------------------------------------------------------------- gráficas
def graficas(filas, est, rx, tx, verde, rojo, cmds, resp, salida):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('matplotlib no está instalado: se omiten las gráficas (pip3 install matplotlib).')
        return
    INK, SEC, GRID, BLUE = '#0b0b0b', '#52514e', '#e4e3df', '#2a78d6'
    GRN, RED = '#1baf7a', '#e34948'

    # Entradas y salidas: los primeros 10 comandos (dos ciclos)
    sel = cmds[:10]
    if sel:
        t0 = sel[0]['inicio'] - 0.2
        t1 = (cmds[10]['inicio'] - 0.05) if len(cmds) > 10 else sel[-1]['fin'] + 0.4
        fig, ax = plt.subplots(figsize=(7.4, 3.6), dpi=220)

        def escalon(s, base, color):
            ts = [t0] + [t for t in s.t if t0 < t < t1] + [t1]
            vs = [s.nivel(t0)] + [v for t, v in zip(s.t, s.v) if t0 < t < t1]
            vs.append(vs[-1])
            ax.step(ts, [base + 0.7 * v for v in vs], where='post', color=color, lw=1.6)
        escalon(rojo, 1.2, RED)
        escalon(verde, 2.4, GRN)
        for l in sel:
            ax.add_patch(plt.Rectangle((l['inicio'], 3.65), max(l['fin'] - l['inicio'], 0.003), 0.6, color=BLUE, lw=0))
            ax.text(l['inicio'] + 0.02, 3.95, l['bytes'].strip().upper() or '·', fontsize=8,
                    color=INK, va='center', fontweight='bold')
        for r in resp:
            if t0 < r['inicio'] < t1:
                ax.add_patch(plt.Rectangle((r['inicio'], 0.05), r['fin'] - r['inicio'], 0.6, color=SEC, alpha=0.55, lw=0))
        etiquetas = ['TX D11 (respuestas)', 'LED rojo (D3)', 'LED verde (D2)', 'RX D10 (comandos)']
        ax.set_yticks([0.35, 1.55, 2.75, 3.95])
        ax.set_yticklabels(etiquetas, fontsize=7.5, color=INK)
        ax.set_xlim(t0, t1)
        ax.set_ylim(-0.2, 4.5)
        ax.set_xlabel('Tiempo de simulación en Wokwi (s)', fontsize=8, color=SEC)
        ax.tick_params(axis='x', labelsize=7.5, colors=SEC)
        ax.tick_params(axis='y', length=0)
        for lado in ['top', 'right', 'left']:
            ax.spines[lado].set_visible(False)
        ax.spines['bottom'].set_color(SEC)
        fig.suptitle('Cada comando recibido por D10 cambia los LED y genera una respuesta por D11',
                     fontsize=9, color=INK, x=0.02, ha='left')
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        fig.savefig(os.path.join(salida, 'fig_entradas_salidas.png'), facecolor='white')
        plt.close(fig)

    # Tiempos de respuesta por comando
    if est:
        fig, ax = plt.subplots(figsize=(6.4, 3.0), dpi=220)
        orden = [e['comando'] for e in est]
        todos = []
        for i, c in enumerate(orden):
            y = [x['lat_resp_ms'] for x in filas if x['enviado'] == f'{c}\\r\\n' and x['lat_resp_ms'] != '']
            todos += y
            n = len(y)
            xs = [i + (-0.12 + 0.24 * k / max(n - 1, 1)) for k in range(n)]
            ax.scatter(xs, y, s=22, color='#9a9893', alpha=0.9, edgecolor='white', linewidth=0.6, zorder=3)
            m = statistics.mean(y)
            ax.hlines(m, i - 0.25, i + 0.25, color=BLUE, lw=2, zorder=4)
            ax.text(i + 0.29, m, f'{m:.2f}'.replace('.', ','), fontsize=7, color=INK, va='center')
        ax.set_xticks(range(len(orden)))
        ax.set_xticklabels([f"'{c}'" for c in orden], fontsize=8)
        ax.set_ylabel('Tiempo hasta la respuesta (ms)', fontsize=8, color=SEC)
        if todos:
            ax.set_ylim(min(todos) - 0.7, max(todos) + 0.7)
        ax.tick_params(axis='y', labelsize=7.5, colors=SEC)
        ax.grid(axis='y', color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        for lado in ['top', 'right']:
            ax.spines[lado].set_visible(False)
        ax.set_title('Repeticiones por comando en Wokwi; la línea azul es la media',
                     fontsize=8.5, color=INK, loc='left')
        fig.tight_layout()
        fig.savefig(os.path.join(salida, 'fig_latencias.png'), facecolor='white')
        plt.close(fig)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    ruta = sys.argv[1]
    destino = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(ruta)), 'resultados')
    analizar(ruta, destino)
