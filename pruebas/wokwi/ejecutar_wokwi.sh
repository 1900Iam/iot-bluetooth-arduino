#!/usr/bin/env bash
# Ejecuta las 55 pruebas en el simulador de Wokwi (wokwi-cli) y analiza la captura.
# Pensado para macOS. Requisitos (una sola vez):
#   brew install arduino-cli
#   arduino-cli core install arduino:avr
#   curl -L https://wokwi.com/ci/install.sh | sh
#   pip3 install matplotlib
#   export WOKWI_CLI_TOKEN=...   (token creado en https://wokwi.com/dashboard/ci)
# Uso:  pruebas/wokwi/ejecutar_wokwi.sh
#       SIN_VERIFICACION=1 pruebas/wokwi/ejecutar_wokwi.sh   (escenario sin expect-pin)
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$AQUI/../.." && pwd)"
SALIDA="$AQUI/resultados"
ESCENARIO="escenario_pruebas.yaml"
[ "${SIN_VERIFICACION:-0}" = "1" ] && ESCENARIO="escenario_sin_verificacion.yaml"

for programa in arduino-cli wokwi-cli python3; do
  command -v "$programa" >/dev/null || { echo "Falta $programa (ver los requisitos al inicio de este archivo)."; exit 1; }
done
[ -n "${WOKWI_CLI_TOKEN:-}" ] || { echo "Falta la variable WOKWI_CLI_TOKEN."; exit 1; }

echo "== 1. Compilando el sketch para Arduino UNO"
arduino-cli compile --fqbn arduino:avr:uno --output-dir "$RAIZ/sketch/build" "$RAIZ/sketch"

echo "== 2. Ejecutando $ESCENARIO en Wokwi"
mkdir -p "$SALIDA"
codigo=0
( cd "$RAIZ/sketch" && wokwi-cli . \
    --scenario "$ESCENARIO" \
    --timeout 60000 \
    --serial-log-file "$SALIDA/registro_serial_wokwi.txt" \
    --vcd-file "$SALIDA/captura_bt.vcd" ) || codigo=$?
echo "wokwi-cli terminó con código $codigo (0 = el escenario y todas sus verificaciones pasaron)"

echo "== 3. Analizando la captura del analizador lógico"
python3 "$AQUI/analizar_vcd.py" "$SALIDA/captura_bt.vcd" "$SALIDA"
cp "$SALIDA/fig_entradas_salidas.png" "$RAIZ/evidencias/graficas/wokwi_entradas_salidas.png" 2>/dev/null || true
cp "$SALIDA/fig_latencias.png" "$RAIZ/evidencias/graficas/wokwi_latencias.png" 2>/dev/null || true
echo "Listo. Resultados en $SALIDA"
exit $codigo
