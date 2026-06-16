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
static const uint16_t KIDCODE_CANVAS_SIZE = 128;
static const uint16_t KIDCODE_CANVAS_SCALE = 1;
static const uint16_t KIDCODE_CANVAS_X = (SCREEN_WIDTH - (KIDCODE_CANVAS_SIZE * KIDCODE_CANVAS_SCALE)) / 2;
static const uint16_t KIDCODE_CANVAS_Y = (SCREEN_HEIGHT - (KIDCODE_CANVAS_SIZE * KIDCODE_CANVAS_SCALE)) / 2;
static const uint32_t TFT_SPI_HZ = 40000000;
static int16_t player_x = 60;
static int16_t player_y = 60;
static int8_t player_dx = 2;
static const int16_t coin_x = 24;
static const int16_t coin_y = 24;

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

static void tftFillRect(uint16_t x, uint16_t y, uint16_t width, uint16_t height, uint16_t color) {
    if (width == 0 || height == 0) {
        return;
    }
    uint8_t pixels[128];
    for (size_t i = 0; i < sizeof(pixels); i += 2) {
        pixels[i] = static_cast<uint8_t>(color >> 8);
        pixels[i + 1] = static_cast<uint8_t>(color & 0xFF);
    }
    tftAddressWindow(x, y, x + width - 1, y + height - 1);
    digitalWrite(KIDCODE_BOARD_TFT_DC, HIGH);
    tftSelect();
    uint32_t remaining = static_cast<uint32_t>(width) * height;
    while (remaining > 0) {
        uint32_t chunk_pixels = remaining > 64 ? 64 : remaining;
        SPI.writeBytes(pixels, chunk_pixels * 2);
        remaining -= chunk_pixels;
    }
    tftDeselect();
}

static uint16_t canvasX(int16_t x) {
    return KIDCODE_CANVAS_X + (x * KIDCODE_CANVAS_SCALE);
}

static uint16_t canvasY(int16_t y) {
    return KIDCODE_CANVAS_Y + (y * KIDCODE_CANVAS_SCALE);
}

static void drawCanvasRect(int16_t x, int16_t y, int16_t width, int16_t height, uint16_t color) {
    if (width <= 0 || height <= 0) {
        return;
    }
    tftFillRect(
        canvasX(x),
        canvasY(y),
        width * KIDCODE_CANVAS_SCALE,
        height * KIDCODE_CANVAS_SCALE,
        color
    );
}

static void drawCanvasBorder() {
    const uint16_t border = 0xFFFF;
    drawCanvasRect(0, 0, KIDCODE_CANVAS_SIZE, 1, border);
    drawCanvasRect(0, KIDCODE_CANVAS_SIZE - 1, KIDCODE_CANVAS_SIZE, 1, border);
    drawCanvasRect(0, 0, 1, KIDCODE_CANVAS_SIZE, border);
    drawCanvasRect(KIDCODE_CANVAS_SIZE - 1, 0, 1, KIDCODE_CANVAS_SIZE, border);
}

static void updateNativeTinyRunner() {
    player_x += player_dx;
    if (player_x <= 2 || player_x >= 118) {
        player_dx = -player_dx;
        player_x += player_dx;
    }
}

static void renderNativeTinyRunner() {
    tftFillScreen(0x0000);
    drawCanvasRect(0, 0, KIDCODE_CANVAS_SIZE, KIDCODE_CANVAS_SIZE, 0x0000);
    drawCanvasBorder();
    drawCanvasRect(player_x, player_y, 8, 8, 0x07E0);
    drawCanvasRect(coin_x, coin_y, 8, 8, 0xFFE0);
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
    renderNativeTinyRunner();
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
    Serial.println("Display: KidCode native tiny_runner canvas");
    Serial.println("Runtime: native tiny_runner scaffold");
    Serial.println("Next: keyboard input and general .kc8 runtime loading");
}

void loop() {
    updateNativeTinyRunner();
    renderNativeTinyRunner();
    Serial.print("KidCode heartbeat ");
    Serial.println(frame_count);
    Serial.print("Native tiny_runner player_x ");
    Serial.println(player_x);
    frame_count += 1;
    delay(100);
}
