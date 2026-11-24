// Banco de pruebas simavr para el sketch Bluetooth.
// Emula el lado del HC-05: transmite cada comando bit a bit (UART 9600 8N1)
// hacia el pin D10 (PB2) del ATmega328P, decodifica las respuestas que el
// Arduino envía por D11 (PB3) y registra las transiciones de los LED (D2, D3).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sim_avr.h"
#include "sim_elf.h"
#include "avr_ioport.h"
#include "avr_uart.h"

static avr_t *avr;
static FILE *fled, *ftx, *fusb;
#define F_CPU 16000000.0
#define BAUD 9600.0
static const double BIT = F_CPU / BAUD;   // ciclos por bit (1666,67)

static void led_cb(struct avr_irq_t *irq, uint32_t v, void *p) {
  fprintf(fled, "%llu,D%d,%u\n", (unsigned long long)avr->cycle, (int)(intptr_t)p, v);
}
static void tx_cb(struct avr_irq_t *irq, uint32_t v, void *p) {
  fprintf(ftx, "%llu,%u\n", (unsigned long long)avr->cycle, v);
}
static void usb_cb(struct avr_irq_t *irq, uint32_t v, void *p) { fputc((int)v, fusb); }

// Cola de niveles a imponer en PB2 (línea TXD del HC-05)
typedef struct { uint64_t c; int v; } nivel_t;
static nivel_t *ev; static size_t nev = 0, cap = 0;
static void push(uint64_t c, int v) {
  if (nev == cap) { cap = cap ? cap * 2 : 4096; ev = realloc(ev, cap * sizeof *ev); }
  ev[nev].c = c; ev[nev].v = v; nev++;
}
static void enviar_byte(uint64_t c0, uint8_t b) {
  push(c0, 0);                                               // bit de inicio
  for (int i = 0; i < 8; i++) push(c0 + (uint64_t)((i + 1) * BIT), (b >> i) & 1);
  push(c0 + (uint64_t)(9 * BIT), 1);                         // bit de parada
}

int main(int argc, char **argv) {
  const char *elf = argv[1], *guion = argv[2];
  elf_firmware_t fw; memset(&fw, 0, sizeof fw);
  if (elf_read_firmware(elf, &fw)) return 1;
  avr = avr_make_mcu_by_name("atmega328p");
  avr_init(avr); avr->frequency = (uint32_t)F_CPU; avr->log = 0;
  avr_load_firmware(avr, &fw);

  fled = fopen("leds.csv", "w"); fprintf(fled, "ciclo,pin,valor\n");
  ftx  = fopen("tx_bt.csv", "w"); fprintf(ftx, "ciclo,valor\n");
  fusb = fopen("serial_usb.txt", "w");
  FILE *fcmd = fopen("comandos_enviados.csv", "w"); fprintf(fcmd, "ciclo_inicio,ciclo_fin,byte\n");

  avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('D'), 2), led_cb, (void *)2);
  avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('D'), 3), led_cb, (void *)3);
  avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 3), tx_cb, NULL);
  uint32_t f = 0;
  avr_ioctl(avr, AVR_IOCTL_UART_GET_FLAGS('0'), &f); f &= ~AVR_UART_FLAG_STDIO;
  avr_ioctl(avr, AVR_IOCTL_UART_SET_FLAGS('0'), &f);
  avr_irq_register_notify(avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUTPUT), usb_cb, NULL);

  // Guion: líneas "t_ms texto" (texto con \r y \n escapados). Cada byte se
  // envía seguido del siguiente, como lo entregaría el HC-05.
  FILE *g = fopen(guion, "r"); char linea[256]; double fin_ms = 0;
  while (fgets(linea, sizeof linea, g)) {
    double t; char txt[200];
    if (sscanf(linea, "%lf %199[^\n]", &t, txt) != 2) continue;
    uint64_t c = (uint64_t)(t / 1000.0 * F_CPU);
    for (char *s = txt; *s; s++) {
      uint8_t b = (uint8_t)*s;
      if (*s == '\\' && s[1] == 'r') { b = '\r'; s++; }
      else if (*s == '\\' && s[1] == 'n') { b = '\n'; s++; }
      else if (*s == '\\' && s[1] == 's') { b = ' '; s++; }
      enviar_byte(c, b);
      fprintf(fcmd, "%llu,%llu,%u\n", (unsigned long long)c, (unsigned long long)(c + (uint64_t)(10 * BIT)), b);
      c += (uint64_t)(10 * BIT);
    }
    if (t > fin_ms) fin_ms = t;
  }
  fclose(g); fclose(fcmd);

  avr_irq_t *rx = avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), 2);
  int deseado = 1; size_t k = 0;
  avr_raise_irq(rx, 1);
  uint64_t fin = (uint64_t)((fin_ms + 600) / 1000.0 * F_CPU);
  while (avr->cycle < fin) {
    while (k < nev && avr->cycle >= ev[k].c) { deseado = ev[k].v; avr_raise_irq(rx, deseado); k++; }
    int st = avr_run(avr);
    // simavr 1.6 reaplica el pull-up al escribir PORTB: se reinyecta el nivel de la línea
    if (((avr->data[0x23] >> 2) & 1) != deseado) avr_raise_irq(rx, deseado);   // PINB = 0x23
    if (st == cpu_Done || st == cpu_Crashed) { fprintf(stderr, "estado %d\n", st); break; }
  }
  fclose(fled); fclose(ftx); fclose(fusb);
  printf("simulado %.3f s\n", avr->cycle / F_CPU);
  return 0;
}
