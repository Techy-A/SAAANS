#include <Wire.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;
const int MQ135_PIN = 34;

unsigned long lastLog = 0;
const unsigned long LOG_INTERVAL = 300000; // 5 minutes in milliseconds

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  
  // Wait for serial connection
  delay(2000);
  
  if (!bme.begin(0x76) && !bme.begin(0x77)) {
    Serial.println("ERROR: BME280 not found");
    while (1);
  }
  
  pinMode(MQ135_PIN, INPUT);
  
  // Print CSV header
  Serial.println("timestamp,temperature,humidity,pressure,mq_raw,voltage");
  
  lastLog = millis();
}

void loop() {
  unsigned long now = millis();
  
  // Log every 5 minutes
  if (now - lastLog >= LOG_INTERVAL) {
    logData();
    lastLog = now;
  }
  
  delay(1000);
}

void logData() {
  // Read sensors
  float temp = bme.readTemperature();
  float humidity = bme.readHumidity();
  float pressure = bme.readPressure() / 100.0;
  
  int mq_raw = analogRead(MQ135_PIN);
  float voltage = (mq_raw / 4095.0) * 3.3;
  
  // Get timestamp (milliseconds since boot)
  unsigned long timestamp = millis();
  
  // Print CSV row
  Serial.print(timestamp);
  Serial.print(",");
  Serial.print(temp, 2);
  Serial.print(",");
  Serial.print(humidity, 2);
  Serial.print(",");
  Serial.print(pressure, 2);
  Serial.print(",");
  Serial.print(mq_raw);
  Serial.print(",");
  Serial.println(voltage, 3);
}
