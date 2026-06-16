#include <Arduino.h>

#include "kidcode_board_profile.h"

static uint32_t frame_count = 0;

void setup() {
    pinMode(KIDCODE_BOARD_POWERON, OUTPUT);
    digitalWrite(KIDCODE_BOARD_POWERON, HIGH);

    Serial.begin(115200);
    delay(1000);
    Serial.println();
    Serial.println("KidCode firmware smoke test");
    Serial.print("Board: ");
    Serial.println(KIDCODE_BOARD_TITLE);
    Serial.print("Board id: ");
    Serial.println(KIDCODE_BOARD_ID);
    Serial.println("Runtime: serial-only scaffold");
    Serial.println("Next: display, keyboard, and .kc8 bundle loading");
}

void loop() {
    Serial.print("KidCode heartbeat ");
    Serial.println(frame_count);
    frame_count += 1;
    delay(1000);
}
