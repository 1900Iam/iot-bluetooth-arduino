/*
  ============================================================================
  Control de dos LED por Bluetooth (HC-05) con Arduino UNO
  Entrega 2 - Internet de las Cosas, Politécnico Grancolombiano
  Grupo B03, Subgrupo 12
  Implementación: Fabián Estiben Romero Villamil
  ============================================================================

  ARQUITECTURA
    Celular o terminal --(Bluetooth)--> módulo HC-05 --(UART 9600 8N1)--> Arduino UNO
    El HC-05 funciona como un puente transparente: todo byte que recibe por
    Bluetooth lo entrega tal cual por su pin TXD, y todo byte que recibe por su
    pin RXD lo transmite por Bluetooth. Para Arduino, el enlace es un puerto serie.

  CONEXIONES
    HC-05 VCC  -> 5V          (el módulo trae regulador de 3,3 V)
    HC-05 GND  -> GND         (referencia común de tierra)
    HC-05 TXD  -> D10         (RX de SoftwareSerial; 3,3 V se lee como HIGH)
    HC-05 RXD  <- D11         (TX de SoftwareSerial) a través de un divisor
                               1 kΩ / 2 kΩ que baja los 5 V de Arduino a 3,3 V
    LED verde  -> D2 con resistencia de 220 Ω a GND
    LED rojo   -> D3 con resistencia de 220 Ω a GND

    Se usa SoftwareSerial en D10/D11 para dejar libre el puerto serie por
    hardware (D0/D1), que comparte el conversor USB del Arduino: así se puede
    cargar el programa y ver el registro en el monitor serial sin desconectar
    el módulo Bluetooth.

  COMANDOS (un carácter; se aceptan mayúsculas y minúsculas)
    V : enciende el LED verde y apaga el rojo
    R : enciende el LED rojo y apaga el verde
    A : enciende ambos LED
    0 : apaga ambos LED
    Cualquier otro carácter se rechaza con un mensaje de error y los LED
    conservan su estado. Los caracteres de fin de línea (\r, \n), los espacios
    y los tabuladores se ignoran, para que funcione con cualquier terminal.

  RECEPCIÓN POR LÍNEA (SoftwareSerial es semidúplex)
    SoftwareSerial desactiva las interrupciones mientras transmite cada byte,
    así que no puede recibir al mismo tiempo. Si se respondiera apenas llega
    "V", los caracteres "\r\n" que la terminal envía justo después llegarían
    durante la respuesta y se corromperían. Por eso los bytes del enlace
    Bluetooth se acumulan y la línea se procesa cuando han pasado
    SILENCIO_MS milisegundos sin recibir nada (o si el búfer se llena).

  CANALES
    - Bluetooth (SoftwareSerial, D10/D11): recibe comandos y responde.
    - USB (Serial, D0/D1): también acepta comandos, para pruebas sin módulo;
      replica con el prefijo "BT> " cada respuesta enviada por Bluetooth y
      publica un registro CSV de cada comando procesado:
        LOG,t_ms,origen,comando,resultado,verde,rojo
  ============================================================================
*/

#include <SoftwareSerial.h>

// ---------------------------- Configuración ---------------------------------
const uint8_t PIN_LED_VERDE = 2;
const uint8_t PIN_LED_ROJO  = 3;
const uint8_t PIN_BT_RX     = 10;   // Arduino recibe aquí lo que envía el HC-05 (TXD)
const uint8_t PIN_BT_TX     = 11;   // Arduino transmite aquí hacia el HC-05 (RXD)
const long    BAUDIOS       = 9600; // velocidad por defecto del HC-05 en modo datos
const unsigned long SILENCIO_MS = 10; // pausa que indica fin de línea (≈10 bytes a 9600 baudios)
const uint8_t TAM_BUFER     = 32;   // máximo de caracteres por línea

SoftwareSerial bt(PIN_BT_RX, PIN_BT_TX);  // puerto serie del enlace Bluetooth

bool verdeEncendido = false;   // estado actual de cada LED
bool rojoEncendido  = false;
unsigned long comandosValidos   = 0;  // contadores para el registro
unsigned long comandosInvalidos = 0;

char bufer[TAM_BUFER];          // línea recibida por Bluetooth, pendiente de procesar
uint8_t largo = 0;
unsigned long tUltimoByte = 0;  // instante en que llegó el último byte

// ---------------------------------------------------------------------------
// Escribe el estado solicitado en los dos LED y lo guarda en las variables.
// ---------------------------------------------------------------------------
void aplicarEstado(bool verde, bool rojo) {
  verdeEncendido = verde;
  rojoEncendido  = rojo;
  digitalWrite(PIN_LED_VERDE, verde ? HIGH : LOW);
  digitalWrite(PIN_LED_ROJO,  rojo  ? HIGH : LOW);
}

// ---------------------------------------------------------------------------
// Escribe una respuesta con marca de tiempo y el estado de los LED, por ejemplo:
//   [t=1520 ms] OK V: LED verde encendido. verde=ON rojo=OFF
//   [t=2112 ms] ERROR: comando 'X' no valido. Use V, R, A o 0.
// texto == nullptr indica un comando no válido.
// ---------------------------------------------------------------------------
void responder(Print &canal, unsigned long t, const __FlashStringHelper *texto, char recibido) {
  canal.print(F("[t="));
  canal.print(t);
  canal.print(F(" ms] "));
  if (texto != nullptr) {
    canal.print(texto);
    canal.print(F(" verde="));
    canal.print(verdeEncendido ? F("ON") : F("OFF"));
    canal.print(F(" rojo="));
    canal.println(rojoEncendido ? F("ON") : F("OFF"));
  } else {
    canal.print(F("ERROR: comando '"));
    if (recibido >= 33 && recibido <= 126) canal.print(recibido);
    else { canal.print(F("0x")); canal.print((uint8_t)recibido, HEX); }
    canal.println(F("' no valido. Use V, R, A o 0."));
  }
}

// ---------------------------------------------------------------------------
// Registro CSV por el puerto USB: LOG,t_ms,origen,comando,resultado,verde,rojo
// ---------------------------------------------------------------------------
void registrar(unsigned long t, const char *origen, char comando, bool valido) {
  Serial.print(F("LOG,"));
  Serial.print(t);
  Serial.print(',');
  Serial.print(origen);
  Serial.print(',');
  if (comando >= 33 && comando <= 126) Serial.print(comando);   // imprimible
  else { Serial.print(F("0x")); Serial.print((uint8_t)comando, HEX); }
  Serial.print(',');
  Serial.print(valido ? F("OK") : F("INVALIDO"));
  Serial.print(',');
  Serial.print(verdeEncendido ? 1 : 0);
  Serial.print(',');
  Serial.println(rojoEncendido ? 1 : 0);
}

// ---------------------------------------------------------------------------
// Interpreta un carácter recibido por cualquiera de los dos canales.
// ---------------------------------------------------------------------------
void procesarComando(char recibido, Stream &canal, const char *origen) {
  // 1) Ignorar fin de línea y espacios que agregan las terminales
  if (recibido == '\r' || recibido == '\n' || recibido == ' ' || recibido == '\t') return;

  unsigned long t = millis();
  char comando = toupper(recibido);   // tolerar minúsculas
  const __FlashStringHelper *texto = nullptr;

  // 2) Ejecutar la acción correspondiente
  switch (comando) {
    case 'V': aplicarEstado(true,  false); texto = F("OK V: LED verde encendido.");  break;
    case 'R': aplicarEstado(false, true);  texto = F("OK R: LED rojo encendido.");   break;
    case 'A': aplicarEstado(true,  true);  texto = F("OK A: ambos LED encendidos."); break;
    case '0': aplicarEstado(false, false); texto = F("OK 0: ambos LED apagados.");   break;
    default:  break;  // 3) comando inválido: los LED conservan su estado
  }

  // 4) Responder por el canal de origen (primero, para no retrasar al celular)
  responder(canal, t, texto, recibido);

  // 5) Si el comando llegó por Bluetooth, replicar la respuesta en el monitor
  //    USB con el prefijo "BT> ", para seguir el enlace desde el computador.
  if (&canal != &Serial) {
    Serial.print(F("BT> "));
    responder(Serial, t, texto, recibido);
  }

  // 6) Registro CSV
  if (texto != nullptr) comandosValidos++; else comandosInvalidos++;
  registrar(t, origen, texto != nullptr ? comando : recibido, texto != nullptr);
}

void setup() {
  // Pines digitales de los LED como salidas, inicialmente apagados
  pinMode(PIN_LED_VERDE, OUTPUT);
  pinMode(PIN_LED_ROJO, OUTPUT);
  aplicarEstado(false, false);

  // Inicializar ambos puertos serie a 9600 baudios (8 bits, sin paridad, 1 bit de parada)
  Serial.begin(BAUDIOS);
  bt.begin(BAUDIOS);

  // Mensaje de bienvenida en ambos canales
  bt.println(F("Sistema listo. Comandos: V=verde, R=rojo, A=ambos, 0=apagar."));
  Serial.println(F("Sistema listo. Comandos: V=verde, R=rojo, A=ambos, 0=apagar."));
  Serial.println(F("LOG,t_ms,origen,comando,resultado,verde,rojo"));
}

void loop() {
  // 1) Enlace Bluetooth: acumular los bytes recibidos sin responder todavía
  while (bt.available() > 0) {
    char c = (char)bt.read();
    if (largo < TAM_BUFER) bufer[largo++] = c;
    tUltimoByte = millis();
  }
  // 2) Si la línea terminó (silencio) o el búfer está lleno, procesarla
  if (largo > 0 && (millis() - tUltimoByte >= SILENCIO_MS || largo >= TAM_BUFER)) {
    for (uint8_t i = 0; i < largo; i++) procesarComando(bufer[i], bt, "BT");
    largo = 0;
  }
  // 3) Puerto USB (por hardware, dúplex completo): procesar cada byte de inmediato
  while (Serial.available() > 0) {
    procesarComando((char)Serial.read(), Serial, "USB");
  }
}
