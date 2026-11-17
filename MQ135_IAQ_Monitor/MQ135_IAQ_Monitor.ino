#include <Wire.h>
#include <Adafruit_BME280.h>

Adafruit_BME280 bme;

// MQ-135 Configuration
const int MQ135_PIN = 34;
const float RL = 10.0;              // Load resistance in kOhms
const float R0 = 76.63;             // Sensor resistance in clean air (calibrate this!)

// Gas detection thresholds (ppm estimates)
const float CO2_THRESHOLD = 1000;   // CO2 > 1000ppm = poor ventilation
const float CO_THRESHOLD = 9;       // CO > 9ppm = dangerous
const float NH3_THRESHOLD = 25;     // NH3 > 25ppm = strong odor

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  
  Serial.println("\n╔════════════════════════════════════════╗");
  Serial.println("║   MQ-135 Indoor Air Quality Monitor   ║");
  Serial.println("╚════════════════════════════════════════╝\n");
  
  // Initialize BME280
  if (!bme.begin(0x76) && !bme.begin(0x77)) {
    Serial.println("⚠ BME280 not found!");
  } else {
    Serial.println("✓ BME280 initialized");
  }
  
  // Initialize MQ-135
  pinMode(MQ135_PIN, INPUT);
  Serial.println("✓ MQ-135 initialized");
  
  Serial.println("\n⏳ Warming up MQ-135 sensor (30 seconds)...");
  for (int i = 30; i > 0; i--) {
    Serial.print(i);
    Serial.print("... ");
    if (i % 10 == 0) Serial.println();
    delay(1000);
  }
  Serial.println("\n✓ Sensor ready!\n");
  
  Serial.println("Gas                 Relevance to IAQ");
  Serial.println("─────────────────────────────────────────");
  Serial.println("CO₂                 Carbon dioxide — stuffiness, occupancy");
  Serial.println("NH₃                 Ammonia — cleaning products, urine");
  Serial.println("NOₓ                 Nitrogen oxides — combustion, traffic");
  Serial.println("Benzene             VOC — paints, adhesives, fuel");
  Serial.println("Smoke               Cigarette smoke, burning");
  Serial.println("Alcohol vapour      Cleaning spirits, sanitiser");
  Serial.println("CO                  Carbon monoxide — dangerous gas");
  Serial.println("General VOCs        Volatile organic compounds");
  Serial.println("─────────────────────────────────────────\n");
}

void loop() {
  // Read sensors
  float temp = bme.readTemperature();
  float humidity = bme.readHumidity();
  float pressure = bme.readPressure() / 100.0;
  
  int adc = analogRead(MQ135_PIN);
  float voltage = (adc / 4095.0) * 3.3;
  
  // Calculate sensor resistance
  float RS = ((3.3 * RL) / voltage) - RL;
  float ratio = RS / R0;
  
  // Estimate gas concentrations (simplified model)
  float co2_ppm = 116.6020682 * pow(ratio, -2.769034857);      // CO2
  float co_ppm = 605.18 * pow(ratio, -3.937);                   // CO
  float nh3_ppm = 102.2 * pow(ratio, -2.473);                   // NH3
  float benzene_ppm = 4.8 * pow(ratio, -2.68);                  // Benzene
  float alcohol_ppm = 60.3 * pow(ratio, -2.894);                // Alcohol
  
  // Display results
  Serial.println("╔════════════════════════════════════════════════════════╗");
  Serial.println("║              INDOOR AIQ QUALITY REPORT                 ║");
  Serial.println("╚════════════════════════════════════════════════════════╝");
  
  // Environmental conditions
  Serial.println("\n📊 Environmental Conditions:");
  Serial.print("   Temperature:  ");
  Serial.print(temp, 1);
  Serial.print(" °C (");
  Serial.print(temp * 9/5 + 32, 1);
  Serial.println(" °F)");
  
  Serial.print("   Humidity:     ");
  Serial.print(humidity, 1);
  Serial.println(" %");
  
  Serial.print("   Pressure:     ");
  Serial.print(pressure, 1);
  Serial.println(" hPa");
  
  // MQ-135 raw data
  Serial.println("\n🔬 MQ-135 Sensor Data:");
  Serial.print("   ADC Value:    ");
  Serial.println(adc);
  
  Serial.print("   Voltage:      ");
  Serial.print(voltage, 2);
  Serial.println(" V");
  
  Serial.print("   RS/R0 Ratio:  ");
  Serial.println(ratio, 2);
  
  // Gas concentrations
  Serial.println("\n🌫️  Detected Gas Concentrations (estimated):");
  Serial.println("   ┌─────────────────┬──────────┬────────────┐");
  Serial.println("   │ Gas             │ Level    │ Status     │");
  Serial.println("   ├─────────────────┼──────────┼────────────┤");
  
  // CO2
  Serial.print("   │ CO₂             │ ");
  Serial.print(co2_ppm, 0);
  Serial.print(" ppm");
  Serial.print(co2_ppm < 100 ? "  " : co2_ppm < 1000 ? " " : "");
  Serial.print(" │ ");
  if (co2_ppm < 800) Serial.println("Good       │");
  else if (co2_ppm < 1000) Serial.println("Moderate   │");
  else if (co2_ppm < 1500) Serial.println("Poor       │");
  else Serial.println("Very Poor  │");
  
  // CO
  Serial.print("   │ CO              │ ");
  Serial.print(co_ppm, 1);
  Serial.print(" ppm");
  Serial.print(co_ppm < 10 ? "  " : " ");
  Serial.print(" │ ");
  if (co_ppm < 9) Serial.println("Safe       │");
  else if (co_ppm < 35) Serial.println("⚠ Caution  │");
  else Serial.println("⚠ DANGER!  │");
  
  // NH3
  Serial.print("   │ NH₃ (Ammonia)   │ ");
  Serial.print(nh3_ppm, 1);
  Serial.print(" ppm");
  Serial.print(nh3_ppm < 10 ? "  " : nh3_ppm < 100 ? " " : "");
  Serial.print(" │ ");
  if (nh3_ppm < 25) Serial.println("Low        │");
  else if (nh3_ppm < 50) Serial.println("Moderate   │");
  else Serial.println("High       │");
  
  // Benzene
  Serial.print("   │ Benzene (VOC)   │ ");
  Serial.print(benzene_ppm, 2);
  Serial.print(" ppm");
  Serial.print(benzene_ppm < 10 ? " " : "");
  Serial.print(" │ ");
  if (benzene_ppm < 1) Serial.println("Good       │");
  else if (benzene_ppm < 5) Serial.println("Moderate   │");
  else Serial.println("High       │");
  
  // Alcohol
  Serial.print("   │ Alcohol vapour  │ ");
  Serial.print(alcohol_ppm, 1);
  Serial.print(" ppm");
  Serial.print(alcohol_ppm < 10 ? "  " : alcohol_ppm < 100 ? " " : "");
  Serial.print(" │ ");
  if (alcohol_ppm < 50) Serial.println("Low        │");
  else Serial.println("Detected   │");
  
  Serial.println("   └─────────────────┴──────────┴────────────┘");
  
  // Overall air quality assessment
  Serial.println("\n🏠 Overall Air Quality:");
  int score = 100;
  if (co2_ppm > 1000) score -= 20;
  if (co_ppm > 9) score -= 30;
  if (nh3_ppm > 25) score -= 15;
  if (benzene_ppm > 5) score -= 20;
  if (adc < 500) score -= 15;
  
  Serial.print("   IAQ Score: ");
  Serial.print(score);
  Serial.print("/100 - ");
  
  if (score >= 80) Serial.println("✓ Excellent");
  else if (score >= 60) Serial.println("○ Good");
  else if (score >= 40) Serial.println("△ Fair");
  else Serial.println("✗ Poor - Ventilate!");
  
  // Recommendations
  if (co2_ppm > 1000 || score < 60) {
    Serial.println("\n💡 Recommendations:");
    if (co2_ppm > 1000) Serial.println("   • Open windows for ventilation");
    if (co_ppm > 9) Serial.println("   • ⚠ CHECK FOR CO SOURCES IMMEDIATELY!");
    if (nh3_ppm > 25) Serial.println("   • Check for cleaning products or waste");
    if (benzene_ppm > 5) Serial.println("   • Reduce VOC sources (paints, solvents)");
  }
  
  Serial.println("\n" + String('═', 56));
  Serial.println("Next reading in 5 seconds...\n");
  
  delay(5000);
}
