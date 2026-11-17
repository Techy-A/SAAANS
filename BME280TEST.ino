#include <Wire.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  
  if (!bme.begin(0x76)) {
    Serial.println("BME280 not found! Check wiring.");
    while (1);
  }
  
  Serial.println("BME280 ready.");
}

void loop() {
  Serial.print("Temp: ");
  Serial.print(bme.readTemperature());
  Serial.println(" C");
  
  Serial.print("Humidity: ");
  Serial.print(bme.readHumidity());
  Serial.println(" %");
  
  Serial.print("Pressure: ");
  Serial.print(bme.readPressure() / 100.0);
  Serial.println(" hPa");
  
  Serial.println("---");
  delay(2000);
}
