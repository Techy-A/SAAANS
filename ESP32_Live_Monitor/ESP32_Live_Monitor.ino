#include <Wire.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;
const int MQ135_PIN = 34;

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  
  delay(2000);
  
  if (!bme.begin(0x76) && !bme.begin(0x77)) {
    Serial.println("ERROR: BME280 not found");
    while (1);
  }
  
  pinMode(MQ135_PIN, INPUT);
  Serial.println("READY");
}

void loop() {
  // Read sensors
  float temp = bme.readTemperature();
  float humidity = bme.readHumidity();
  float pressure = bme.readPressure() / 100.0;
  int mq_raw = analogRead(MQ135_PIN);
  float voltage = (mq_raw / 4095.0) * 3.3;
  
  // Print in simple parseable format
  Serial.print("Temperature: ");
  Serial.print(temp);
  Serial.println(" C");
  
  Serial.print("Humidity: ");
  Serial.print(humidity);
  Serial.println(" %");
  
  Serial.print("Pressure: ");
  Serial.print(pressure);
  Serial.println(" hPa");
  
  Serial.print("Raw ADC: ");
  Serial.println(mq_raw);
  
  Serial.print("Voltage: ");
  Serial.print(voltage);
  Serial.println(" V");
  
  Serial.println("---");
  
  delay(5000);  // Update every 5 seconds
}
