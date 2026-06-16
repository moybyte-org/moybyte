#include <Arduino.h>

#include "kidcode_board_profile.h"

#if __has_include("kidcode_project_bundle.h")
#include "kidcode_project_bundle.h"
#else
#define KIDCODE_PROJECT_ID "none"
#define KIDCODE_PROJECT_TITLE "No bundled project"
#define KIDCODE_PROJECT_BUNDLE_SIZE 0
#endif

static uint32_t frame_count = 0;

void setup() {
    pinMode(KIDCODE_BOARD_POWERON, OUTPUT);
    digitalWrite(KIDCODE_BOARD_POWERON, HIGH);
    pinMode(KIDCODE_BOARD_TFT_BACKLIGHT, OUTPUT);
    digitalWrite(KIDCODE_BOARD_TFT_BACKLIGHT, HIGH);

    Serial.begin(115200);
    delay(1000);
    Serial.println();
    Serial.println("KidCode firmware smoke test");
    Serial.print("Board: ");
    Serial.println(KIDCODE_BOARD_TITLE);
    Serial.print("Board id: ");
    Serial.println(KIDCODE_BOARD_ID);
    Serial.print("Bundled project: ");
    Serial.println(KIDCODE_PROJECT_ID);
    Serial.print("Bundle title: ");
    Serial.println(KIDCODE_PROJECT_TITLE);
    Serial.print("Bundle bytes: ");
    Serial.println(KIDCODE_PROJECT_BUNDLE_SIZE);
    Serial.println("Display backlight: blinking");
    Serial.println("Runtime: serial-only scaffold");
    Serial.println("Next: display, keyboard, and .kc8 bundle loading");
}

void loop() {
    digitalWrite(KIDCODE_BOARD_TFT_BACKLIGHT, (frame_count % 2) == 0 ? HIGH : LOW);
    Serial.print("KidCode heartbeat ");
    Serial.println(frame_count);
    frame_count += 1;
    delay(1000);
}
