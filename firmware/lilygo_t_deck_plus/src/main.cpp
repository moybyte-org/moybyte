#include <Arduino.h>
#include <SPI.h>

#include "kidcode_board_profile.h"

#if __has_include("kidcode_project_bundle.h")
#include "kidcode_project_bundle.h"
#else
#define KIDCODE_PROJECT_ID "none"
#define KIDCODE_PROJECT_TITLE "No bundled project"
#define KIDCODE_PROJECT_BUNDLE_SIZE 0
#endif

static uint32_t frame_count = 0;
static const uint16_t SCREEN_WIDTH = 320;
static const uint16_t SCREEN_HEIGHT = 240;
static const uint32_t TFT_SPI_HZ = 40000000;

static void tftSelect() {
    digitalWrite(KIDCODE_BOARD_TFT_CS, LOW);
}

static void tftDeselect() {
    digitalWrite(KIDCODE_BOARD_TFT_CS, HIGH);
}

static void tftCommand(uint8_t command) {
    digitalWrite(KIDCODE_BOARD_TFT_DC, LOW);
    tftSelect();
    SPI.transfer(command);
    tftDeselect();
}

static void tftData(const uint8_t *data, size_t length) {
    if (length == 0) {
        return;
    }
    digitalWrite(KIDCODE_BOARD_TFT_DC, HIGH);
    tftSelect();
    SPI.writeBytes(data, length);
    tftDeselect();
}

static void tftCommandData(uint8_t command, const uint8_t *data, size_t length) {
    tftCommand(command);
    tftData(data, length);
}

static void tftInitCommand(uint8_t command, const uint8_t *data, size_t length, uint16_t delay_ms = 0) {
    tftCommandData(command, data, length);
    if (delay_ms > 0) {
        delay(delay_ms);
    }
}

static void tftAddressWindow(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
    uint8_t caset[] = {
        static_cast<uint8_t>(x0 >> 8),
        static_cast<uint8_t>(x0 & 0xFF),
        static_cast<uint8_t>(x1 >> 8),
        static_cast<uint8_t>(x1 & 0xFF),
    };
    uint8_t raset[] = {
        static_cast<uint8_t>(y0 >> 8),
        static_cast<uint8_t>(y0 & 0xFF),
        static_cast<uint8_t>(y1 >> 8),
        static_cast<uint8_t>(y1 & 0xFF),
    };
    tftCommandData(0x2A, caset, sizeof(caset));
    tftCommandData(0x2B, raset, sizeof(raset));
    tftCommand(0x2C);
}

static void tftFillScreen(uint16_t color) {
    uint8_t pixels[128];
    for (size_t i = 0; i < sizeof(pixels); i += 2) {
        pixels[i] = static_cast<uint8_t>(color >> 8);
        pixels[i + 1] = static_cast<uint8_t>(color & 0xFF);
    }
    tftAddressWindow(0, 0, SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1);
    digitalWrite(KIDCODE_BOARD_TFT_DC, HIGH);
    tftSelect();
    uint32_t remaining = SCREEN_WIDTH * SCREEN_HEIGHT;
    while (remaining > 0) {
        uint32_t chunk_pixels = remaining > 64 ? 64 : remaining;
        SPI.writeBytes(pixels, chunk_pixels * 2);
        remaining -= chunk_pixels;
    }
    tftDeselect();
}

static void initDisplay() {
    pinMode(KIDCODE_BOARD_TFT_CS, OUTPUT);
    pinMode(KIDCODE_BOARD_TFT_DC, OUTPUT);
    pinMode(KIDCODE_BOARD_TFT_BACKLIGHT, OUTPUT);
    digitalWrite(KIDCODE_BOARD_TFT_CS, HIGH);
    digitalWrite(KIDCODE_BOARD_TFT_BACKLIGHT, HIGH);

    SPI.begin(
        KIDCODE_BOARD_SPI_SCK,
        KIDCODE_BOARD_SPI_MISO,
        KIDCODE_BOARD_SPI_MOSI,
        KIDCODE_BOARD_TFT_CS
    );
    SPI.beginTransaction(SPISettings(TFT_SPI_HZ, MSBFIRST, SPI_MODE0));

    tftCommand(0x01);
    delay(150);
    tftCommand(0x11);
    delay(120);
    const uint8_t colmod[] = {0x05};
    const uint8_t madctl[] = {0x55};
    const uint8_t porch[] = {0x0C, 0x0C, 0x00, 0x33, 0x33};
    const uint8_t gate[] = {0x75};
    const uint8_t vcom[] = {0x1A};
    const uint8_t lcm[] = {0x2C};
    const uint8_t vdv_vrh[] = {0x01};
    const uint8_t vrh[] = {0x13};
    const uint8_t vdv[] = {0x20};
    const uint8_t frctrl[] = {0x0F};
    const uint8_t power[] = {0xA4, 0xA1};
    const uint8_t gatectrl[] = {0xA1};
    const uint8_t gamma_pos[] = {0xD0, 0x0D, 0x14, 0x0D, 0x0D, 0x09, 0x38, 0x44, 0x4E, 0x3A, 0x17, 0x18, 0x2F, 0x30};
    const uint8_t gamma_neg[] = {0xD0, 0x09, 0x0F, 0x08, 0x07, 0x14, 0x37, 0x44, 0x4D, 0x38, 0x15, 0x16, 0x2C, 0x3E};

    tftCommandData(0x3A, colmod, sizeof(colmod));
    tftCommandData(0x36, madctl, sizeof(madctl));
    tftCommandData(0xB2, porch, sizeof(porch));
    tftCommandData(0xB7, gate, sizeof(gate));
    tftCommandData(0xBB, vcom, sizeof(vcom));
    tftCommandData(0xC0, lcm, sizeof(lcm));
    tftCommandData(0xC2, vdv_vrh, sizeof(vdv_vrh));
    tftCommandData(0xC3, vrh, sizeof(vrh));
    tftCommandData(0xC4, vdv, sizeof(vdv));
    tftCommandData(0xC6, frctrl, sizeof(frctrl));
    tftCommandData(0xD0, power, sizeof(power));
    tftCommandData(0xD6, gatectrl, sizeof(gatectrl));
    tftCommandData(0xE0, gamma_pos, sizeof(gamma_pos));
    tftCommandData(0xE1, gamma_neg, sizeof(gamma_neg));
    tftCommand(0x21);
    tftCommand(0x29);
    delay(20);
    tftFillScreen(0x001F);
}

void setup() {
    pinMode(KIDCODE_BOARD_POWERON, OUTPUT);
    digitalWrite(KIDCODE_BOARD_POWERON, HIGH);
    initDisplay();

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
    Serial.println("Display: ST7789 color heartbeat");
    Serial.println("Runtime: serial-only scaffold");
    Serial.println("Next: display, keyboard, and .kc8 bundle loading");
}

void loop() {
    const uint16_t colors[] = {0x001F, 0x07E0, 0xF800, 0xFFE0};
    tftFillScreen(colors[frame_count % 4]);
    Serial.print("KidCode heartbeat ");
    Serial.println(frame_count);
    frame_count += 1;
    delay(1000);
}
