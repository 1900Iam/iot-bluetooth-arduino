#!/usr/bin/env bash
# Compila sketch.ino con el núcleo oficial de Arduino AVR (incluida SoftwareSerial)
# y lo ejecuta en simavr (ATmega328P a 16 MHz) con el guion de comandos guion.txt.
# Requisitos (Ubuntu/Debian): sudo apt install gcc-avr avr-libc simavr libsimavr-dev libelf-dev git python3-pandas
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
B=$(mktemp -d)
git clone -q --depth 1 https://github.com/arduino/ArduinoCore-avr.git "$B/core"
C="$B/core"; cd "$B"
F="-mmcu=atmega328p -DF_CPU=16000000L -DARDUINO=10819 -DARDUINO_AVR_UNO -DARDUINO_ARCH_AVR -Os -ffunction-sections -fdata-sections -I$C/cores/arduino -I$C/variants/standard -I$C/libraries/SoftwareSerial/src"
for f in "$C"/cores/arduino/*.c; do avr-gcc $F -c "$f" -o "$(basename "$f").o"; done
for f in "$C"/cores/arduino/*.cpp "$C"/libraries/SoftwareSerial/src/SoftwareSerial.cpp; do avr-g++ $F -std=gnu++11 -fno-exceptions -fno-threadsafe-statics -c "$f" -o "$(basename "$f").o" 2>/dev/null; done
avr-gcc $F -x assembler-with-cpp -c "$C/cores/arduino/wiring_pulse.S" -o wiring_pulse.S.o
(echo '#include <Arduino.h>'; cat "$DIR/../../sketch/sketch.ino") > sketch.cpp
avr-g++ $F -std=gnu++11 -fno-exceptions -fno-threadsafe-statics -Wall -c sketch.cpp -o sketch.obj
avr-gcc -mmcu=atmega328p -Os -Wl,--gc-sections sketch.obj *.o -o bt.elf
avr-size -C --mcu=atmega328p bt.elf
gcc -O2 -I/usr/include/simavr -I/usr/include/simavr/avr "$DIR/banco_bt.c" -o banco -lsimavr -lelf
cd "$DIR" && "$B/banco" "$B/bt.elf" guion.txt && python3 analizar.py
mv serial_usb.txt registro_serial_usb.txt
