#include <Wire.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;

// MQ-135 sensor pin
const int MQ135_PIN = 34;

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  
  Serial.println("\n=== BME280 + MQ-135 Sensor Test ===\n");
  
  // Test BME280
  Serial.print("Testing BME280... ");
  if (!bme.begin(0x76) && !bme.begin(0x77)) {
    Serial.println("FAILED!");
    Serial.println("BME280 not found. Check wiring:");
    Serial.println("  - VCC to 3.3V");
    Serial.println("  - GND to GND");
    Serial.println("  - SDA to D21");
    Serial.println("  - SCL to D22");
  } else {
    Serial.println("OK!");
  }
  
  // Test MQ-135
  Serial.print("Testing MQ-135... ");
  pinMode(MQ135_PIN, INPUT);
  int testRead = analogRead(MQ135_PIN);
  if (testRead == 0 || testRead == 4095) {
    Serial.println("WARNING - Check wiring!");
    Serial.println("  Reading: " + String(testRead) + " (should be between 0-4095)");
  } else {
    Serial.println("OK!");
    Serial.println("  Initial reading: " + String(testRead));
  }
  
  Serial.println("\n--- Starting continuous monitoring ---\n");
}

void loop() {
  // Read BME280
  float temp = bme.readTemperature();
  float humidity = bme.readHumidity();
  float pressure = bme.readPressure() / 100.0;
  
  // Read MQ-135
  int mq135_raw = analogRead(MQ135_PIN);
  float mq135_voltage = (mq135_raw / 4095.0) * 3.3;
  
  // Display results
  Serial.println("=== Sensor Readings ===");
  
  Serial.println("\nBME280:");
  Serial.print("  Temperature: ");
  Serial.print(temp);
  Serial.print(" °C (");
  Serial.print(temp * 9/5 + 32);
  Serial.println(" °F)");
  
  Serial.print("  Humidity:    ");
  Serial.print(humidity);
  Serial.println(" %");
  
  Serial.print("  Pressure:    ");
  Serial.print(pressure);
  Serial.println(" hPa");
  
  Serial.println("\nMQ-135 Air Quality:");
  Serial.print("  Raw ADC:     ");
  Serial.println(mq135_raw);
  
  Serial.print("  Voltage:     ");
  Serial.print(mq135_voltage);
  Serial.println(" V");
  
  Serial.print("  Air Quality: ");
  if (mq135_raw < 1000) {
    Serial.println("GOOD (Clean air)");
  } else if (mq135_raw < 2000) {
    Serial.println("MODERATE");
  } else if (mq135_raw < 3000) {
    Serial.println("POOR");
  } else {
    Serial.println("VERY POOR (High pollution)");
  }
  
  Serial.println("\n" + String('-', 40) + "\n");
  
  delay(3000);
}
