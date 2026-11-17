// SAANS - unified ESP32 firmware: BME280 + MQ-135 + (optional) PMS5003 + (optional) fan relay
// Serial CSV @115200:
//   timestamp,temperature,humidity,pressure,mq_raw,voltage,pm1,pm25,pm10,fan,
//   cf_pm1,cf_pm25,cf_pm10,atm_pm1,atm_pm25,atm_pm10,n03,n05,n10,n25,pms_temp_x10,pms_hum_x10,pms_status
// pm* are -1 when no PMS5003 is attached. voltage = voltage at the ADC pin (after divider).
// The last 13 columns are the PMS5003T data words as sent. n03..n25 = particles above that size per 0.1 L.
// pms_status = version (high byte), error code (low byte).
//
// Wiring
//   BME280 : SDA->GPIO21, SCL->GPIO22, VCC->3.3V, GND
//   MQ-135 : AOUT->divider (18k from AOUT, 33k to GND)->GPIO34, VCC->5V, GND
//   PMS5003: VCC->5V, GND, PMS TX->GPIO16 (ESP32 RX2), PMS RX->GPIO17 (ESP32 TX2)
//   Relay  : IN->GPIO26 (active HIGH), VCC->5V, GND   (fan on the relay's NO contact)

#include <Wire.h>
#include <Adafruit_BME280.h>

#define USE_PMS5003 1   // set to 0 until the PMS5003 is connected
#define USE_RELAY   0   // set to 1 once the relay + fan are wired

const int MQ135_PIN = 34;
const int RELAY_PIN = 26;
const unsigned long LOG_INTERVAL_MS = 5000;

// Fan control: ON when PM2.5 or MQ-135 index is high, OFF with hysteresis.
const float PM25_ON  = 35.0, PM25_OFF  = 25.0;   // ug/m3
const int   MQ_ON    = 1500, MQ_OFF    = 1200;   // raw ADC

Adafruit_BME280 bme;
unsigned long lastLog = 0;
bool fanOn = false;
int pm1 = -1, pm25 = -1, pm10 = -1;
int pmsWords[13];   // every data word of the latest frame; -1 when no recent valid frame
unsigned long lastPmsFrame = 0;
const unsigned long PMS_STALE_MS = 10000;   // report blanks instead of repeating old values

#if USE_PMS5003
// PMS5003 frame: 0x42 0x4D, len(2), 13 data words, checksum(2) = 32 bytes. Atmospheric values.
bool readPMS() {
  while (Serial2.available() >= 32) {
    if (Serial2.peek() != 0x42) { Serial2.read(); continue; }
    uint8_t buf[32];
    Serial2.readBytes(buf, 32);
    if (buf[1] != 0x4D) continue;
    uint16_t sum = 0;
    for (int i = 0; i < 30; i++) sum += buf[i];
    if (sum != ((buf[30] << 8) | buf[31])) continue;
    pm1  = (buf[10] << 8) | buf[11];
    pm25 = (buf[12] << 8) | buf[13];
    pm10 = (buf[14] << 8) | buf[15];
    for (int i = 0; i < 13; i++) pmsWords[i] = (buf[4 + 2 * i] << 8) | buf[5 + 2 * i];
    lastPmsFrame = millis();
    return true;
  }
  return false;
}
#endif

int readMQ135() {
  long sum = 0;
  for (int i = 0; i < 16; i++) { sum += analogRead(MQ135_PIN); delay(2); }
  return sum / 16;
}

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  for (int i = 0; i < 13; i++) pmsWords[i] = -1;
  delay(2000);

  if (!bme.begin(0x76) && !bme.begin(0x77)) {
    Serial.println("ERROR: BME280 not found");
    while (1) delay(1000);
  }

  analogSetPinAttenuation(MQ135_PIN, ADC_11db);
  pinMode(MQ135_PIN, INPUT);

#if USE_PMS5003
  Serial2.begin(9600, SERIAL_8N1, 16, 17);
#endif
#if USE_RELAY
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);
#endif

  Serial.println("timestamp,temperature,humidity,pressure,mq_raw,voltage,pm1,pm25,pm10,fan,cf_pm1,cf_pm25,cf_pm10,atm_pm1,atm_pm25,atm_pm10,n03,n05,n10,n25,pms_temp_x10,pms_hum_x10,pms_status");
  lastLog = millis();
}

void updateFan(int mq) {
#if USE_RELAY
  bool high = (mq > MQ_ON) || (pm25 > PM25_ON);
  bool low  = (mq < MQ_OFF) && (pm25 < PM25_OFF);   // pm25 = -1 counts as low
  if (!fanOn && high) fanOn = true;
  else if (fanOn && low) fanOn = false;
  digitalWrite(RELAY_PIN, fanOn ? HIGH : LOW);
#endif
}

void loop() {
#if USE_PMS5003
  readPMS();   // keep draining the UART so the latest frame is current
#endif

  unsigned long now = millis();
  if (now - lastLog >= LOG_INTERVAL_MS) {
    lastLog = now;
#if USE_PMS5003
    if (lastPmsFrame == 0 || now - lastPmsFrame > PMS_STALE_MS) {
      pm1 = pm25 = pm10 = -1;
      for (int i = 0; i < 13; i++) pmsWords[i] = -1;
    }
#endif
    int mq = readMQ135();
    updateFan(mq);

    Serial.print(now);                       Serial.print(",");
    Serial.print(bme.readTemperature(), 2);  Serial.print(",");
    Serial.print(bme.readHumidity(), 2);     Serial.print(",");
    Serial.print(bme.readPressure() / 100.0, 2); Serial.print(",");
    Serial.print(mq);                        Serial.print(",");
    Serial.print((mq / 4095.0) * 3.3, 3);    Serial.print(",");
    Serial.print(pm1);                       Serial.print(",");
    Serial.print(pm25);                      Serial.print(",");
    Serial.print(pm10);                      Serial.print(",");
    Serial.print(fanOn ? 1 : 0);
    for (int i = 0; i < 13; i++) { Serial.print(","); Serial.print(pmsWords[i]); }
    Serial.println();
  }
}
