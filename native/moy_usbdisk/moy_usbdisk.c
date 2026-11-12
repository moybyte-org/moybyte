// moy_usbdisk: the card on a computer over USB mass storage (moy_usbdisk.h).

#include <string.h>

// A usermod is compiled a second time for the micropython.elf link without
// the IDF's ESP_PLATFORM, under which FreeRTOS.h gives the ESP-IDF trace
// defaults and xTaskCreatePinnedToCore that TinyUSB's FreeRTOS layer uses.
#ifndef ESP_PLATFORM
#define ESP_PLATFORM 1
#endif

#include "py/mpconfig.h"
#include "py/mperrno.h"

#define CFG_TUSB_CONFIG_FILE "moy_tusb_config.h"
#include "tusb.h"

#include "esp_heap_caps.h"
#include "moy_kmem.h"
#include "moy_tasks.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_private/usb_phy.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "moy_sd.h"
#include "moy_usbdisk.h"

#if CONFIG_IDF_TARGET_ESP32S3
#include "soc/rtc_cntl_reg.h"
#define SWITCHES_USJ 1
#else
#define SWITCHES_USJ 0
#endif

// The card on the panel's bus (the T-Deck): each transfer in a band gap.
#if defined(MOY_HW_SPI2_SD_CS)
#include "moy_flush.h"
#define gate_take() moy_flush_gate_take()
#define gate_give() moy_flush_gate_give()
#define gate_open() moy_flush_gate_open()
#else
#define gate_take()
#define gate_give()
#define gate_open()
#endif

#define SECTOR 512

// How long the drive waits for a computer before it gives the port back by
// itself, the store staying held until the console takes it back: never
// configured, configured and then gone (unplugged, suspended), or ejected
// and not taken back. On the T-Deck that is what returns the dev channel
// when nobody is there to turn the drive off.
#define WAIT_US (30 * 1000000LL)
#define EJECTED_US (10 * 1000000LL)

// The store's hold (native/moy_store/moy_card.c), and the `usb` host on the
// same port where the image has one (native/p4/moy_usb).
#if __has_include("moy_vol.h")
#include "moy_vol.h"
#else
int moy_store_card_held(void);
#endif
#pragma weak moy_store_card_held
#if __has_include("moy_usb.h")
#include "moy_usb.h"
#else
int moy_usb_host_up(void);
#endif
#pragma weak moy_usb_host_up

static volatile int s_on;           // the stack is up
static volatile int s_run;          // the task goes on
static volatile int s_ejected;
static uint32_t s_sectors;

// In PSRAM while the drive is on, so it holds no internal SRAM of its own
// beyond TinyUSB's statics: the sector a transfer smaller than one gathers
// (a read's, or the write being filled) and the string descriptor's words.
typedef struct {
    uint8_t sec[SECTOR];
    uint32_t sec_lba;               // the sector `sec` holds read, or NONE
    uint16_t str[24];
} work_t;
#define NONE 0xFFFFFFFFu
static work_t *W;
static usb_phy_handle_t s_phy;
static TaskHandle_t s_task;
static SemaphoreHandle_t s_done;
#if SWITCHES_USJ
static usb_phy_handle_t s_usj;
static int s_shutdown;
#endif

// -- the descriptors -------------------------------------------------------------

#if CFG_TUD_MAX_SPEED == OPT_MODE_HIGH_SPEED
#define EP_SIZE 512
#else
#define EP_SIZE 64
#endif

static const tusb_desc_device_t DEVICE = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = 0,
    .bDeviceSubClass = 0,
    .bDeviceProtocol = 0,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0x303A,             // Espressif's, esp_tinyusb's MSC-only product id
    .idProduct = 0x4008,
    .bcdDevice = 0x0100,
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1,
};

#define CONFIG_LEN (TUD_CONFIG_DESC_LEN + TUD_MSC_DESC_LEN)

static const uint8_t CONFIG[] = {
    TUD_CONFIG_DESCRIPTOR(1, 1, 0, CONFIG_LEN, 0x00, 100),
    TUD_MSC_DESCRIPTOR(0, 0, 0x01, 0x81, EP_SIZE),
};

const uint8_t *tud_descriptor_device_cb(void) {
    return (const uint8_t *)&DEVICE;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return CONFIG;
}

#if CFG_TUD_MAX_SPEED == OPT_MODE_HIGH_SPEED
static const tusb_desc_device_qualifier_t QUALIFIER = {
    .bLength = sizeof(tusb_desc_device_qualifier_t),
    .bDescriptorType = TUSB_DESC_DEVICE_QUALIFIER,
    .bcdUSB = 0x0200,
    .bDeviceClass = 0,
    .bDeviceSubClass = 0,
    .bDeviceProtocol = 0,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .bNumConfigurations = 1,
    .bReserved = 0,
};

const uint8_t *tud_descriptor_device_qualifier_cb(void) {
    return (const uint8_t *)&QUALIFIER;
}
#endif

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    uint16_t *s_str = W->str;
    char serial[13];
    const char *s;
    if (index == 0) {
        s_str[1] = 0x0409;
        s_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | 4);
        return s_str;
    }
    if (index == 1) {
        s = "Moybyte";
    } else if (index == 2) {
        s = "Moybyte card";
    } else if (index == 3) {
        uint8_t mac[6];
        esp_efuse_mac_get_default(mac);
        for (int i = 0; i < 6; i++) {
            serial[i * 2] = "0123456789ABCDEF"[mac[i] >> 4];
            serial[i * 2 + 1] = "0123456789ABCDEF"[mac[i] & 15];
        }
        serial[12] = 0;
        s = serial;
    } else {
        return NULL;
    }
    size_t n = strlen(s);
    if (n > 23) {
        n = 23;
    }
    for (size_t i = 0; i < n; i++) {
        s_str[1 + i] = (uint8_t)s[i];
    }
    s_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * n + 2));
    return s_str;
}

// -- the one LUN: the card ----------------------------------------------------------

void tud_msc_inquiry_cb(uint8_t lun, uint8_t vendor_id[8], uint8_t product_id[16],
                        uint8_t product_rev[4]) {
    (void)lun;
    memcpy(vendor_id, "Moybyte ", 8);
    memcpy(product_id, "Card            ", 16);
    memcpy(product_rev, "1.0 ", 4);
}

bool tud_msc_test_unit_ready_cb(uint8_t lun) {
    if (s_ejected) {
        tud_msc_set_sense(lun, SCSI_SENSE_NOT_READY, 0x3A, 0x00);    // medium not present
        return false;
    }
    return true;
}

void tud_msc_capacity_cb(uint8_t lun, uint32_t *block_count, uint16_t *block_size) {
    (void)lun;
    *block_count = s_sectors;
    *block_size = SECTOR;
}

bool tud_msc_start_stop_cb(uint8_t lun, uint8_t power_condition, bool start, bool load_eject) {
    (void)lun;
    (void)power_condition;
    if (load_eject) {
        s_ejected = !start;
    }
    return true;
}

// `n` bytes at `offset` into the transfer that began at sector `lba`. Whole
// sectors go to the card as they are; a part of one (the S3's 64-byte
// packets) is read from the sector kept in PSRAM, or gathered there until
// the sector is whole and then written.
static int32_t card_io(uint32_t lba, uint32_t offset, uint8_t *buf, uint32_t n, int write) {
    lba += offset / SECTOR;
    offset %= SECTOR;
    if (lba >= s_sectors || (offset == 0 && n % SECTOR == 0 && lba + n / SECTOR > s_sectors)) {
        return -1;
    }
    int err = 0;
    if (offset == 0 && n % SECTOR == 0) {
        W->sec_lba = NONE;
        gate_take();
        err = moy_sd_card_io(lba, buf, n / SECTOR, write);
        gate_give();
    } else {
        if (offset + n > SECTOR) {
            n = SECTOR - offset;
        }
        if (write) {
            W->sec_lba = NONE;
            memcpy(W->sec + offset, buf, n);
            if (offset + n == SECTOR) {
                gate_take();
                err = moy_sd_card_io(lba, W->sec, 1, 1);
                gate_give();
            }
        } else {
            if (W->sec_lba != lba) {
                gate_take();
                err = moy_sd_card_io(lba, W->sec, 1, 0);
                gate_give();
                W->sec_lba = err == 0 ? lba : NONE;
            }
            if (err == 0) {
                memcpy(buf, W->sec + offset, n);
            }
        }
    }
    return err == 0 ? (int32_t)n : -1;
}

int32_t tud_msc_read10_cb(uint8_t lun, uint32_t lba, uint32_t offset, void *buffer,
                          uint32_t bufsize) {
    (void)lun;
    return card_io(lba, offset, buffer, bufsize, 0);
}

int32_t tud_msc_write10_cb(uint8_t lun, uint32_t lba, uint32_t offset, uint8_t *buffer,
                           uint32_t bufsize) {
    (void)lun;
    return card_io(lba, offset, buffer, bufsize, 1);
}

int32_t tud_msc_scsi_cb(uint8_t lun, uint8_t const scsi_cmd[16], void *buffer,
                        uint16_t bufsize) {
    (void)buffer;
    (void)bufsize;
    switch (scsi_cmd[0]) {
        case 0x1E:                  // PREVENT ALLOW MEDIUM REMOVAL: nothing to lock
            return 0;
        default:
            tud_msc_set_sense(lun, SCSI_SENSE_ILLEGAL_REQUEST, 0x20, 0x00);
            return -1;
    }
}

// -- the port -------------------------------------------------------------------------

#if SWITCHES_USJ
// The internal PHY back on USB-Serial/JTAG: the OTG side let go and the USJ
// controller selected, as MicroPython's usb_usj_mode does.
static void usj_back(void) {
    if (s_phy != NULL) {
        usb_del_phy(s_phy);
        s_phy = NULL;
    }
    if (s_usj == NULL) {
        usb_phy_config_t c = {.controller = USB_PHY_CTRL_SERIAL_JTAG};
        usb_new_phy(&c, &s_usj);
    }
}

// A restart while the drive is on: the PHY back on USB-Serial/JTAG first, so
// the next boot's console and the ROM's loader are where the dev channel is.
static void on_shutdown(void) {
    if (s_on) {
        tud_disconnect();
        usj_back();
    }
}

// Before anything prints: a start whose PHY selection outlived the reset (the
// RTC domain keeps it across a software reset) is put back on USB-Serial/JTAG.
__attribute__((constructor)) static void usj_at_start(void) {
    if (REG_GET_BIT(RTC_CNTL_USB_CONF_REG, RTC_CNTL_SW_HW_USB_PHY_SEL)) {
        REG_CLR_BIT(RTC_CNTL_USB_CONF_REG, RTC_CNTL_SW_HW_USB_PHY_SEL | RTC_CNTL_SW_USB_PHY_SEL);
    }
}
#endif

static int phy_up(void) {
    usb_phy_config_t c = {
        .controller = USB_PHY_CTRL_OTG,
        .otg_mode = USB_OTG_MODE_DEVICE,
        #if CONFIG_IDF_TARGET_ESP32P4
        .target = USB_PHY_TARGET_UTMI,
        .otg_speed = USB_PHY_SPEED_HIGH,
        #else
        .target = USB_PHY_TARGET_INT,
        #endif
    };
    #if SWITCHES_USJ
    if (s_usj != NULL) {
        usb_del_phy(s_usj);
        s_usj = NULL;
    }
    #endif
    if (usb_new_phy(&c, &s_phy) != ESP_OK) {
        s_phy = NULL;
        #if SWITCHES_USJ
        usj_back();
        #endif
        return MP_EIO;
    }
    return 0;
}

static void phy_down(void) {
    #if SWITCHES_USJ
    usj_back();
    #else
    if (s_phy != NULL) {
        usb_del_phy(s_phy);
        s_phy = NULL;
    }
    #endif
}

// The task: TinyUSB's events until it is told to stop or a computer is not
// there (WAIT_US, EJECTED_US), then the port given back from here, so a
// drive nobody turns off still ends.
static void task(void *arg) {
    (void)arg;
    int64_t since = moy_now_us();
    int was = -1;
    while (s_run) {
        tud_task_ext(50, false);
        int st = moy_usbdisk_state();
        int64_t now = moy_now_us();
        if (st != was) {
            was = st;
            since = now;
        }
        if ((st == MOY_USBDISK_WAITING && now - since > WAIT_US)
            || (st == MOY_USBDISK_EJECTED && now - since > EJECTED_US)) {
            s_run = 0;
        }
    }
    tud_disconnect();
    tud_deinit(MOY_TUSB_RHPORT);
    vTaskDelay(pdMS_TO_TICKS(100));         // the computer sees it go before USJ comes
    phy_down();
    s_on = 0;
    xSemaphoreGive(s_done);
    vTaskDelete(NULL);
}

int moy_usbdisk_on(void) {
    return s_on;
}

int moy_usbdisk_start(void) {
    if (s_on) {
        return 0;
    }
    if (moy_store_card_held == NULL || !moy_store_card_held()) {
        return MP_EBUSY;
    }
    if (moy_usb_host_up != NULL && moy_usb_host_up()) {
        return MP_EBUSY;
    }
    s_sectors = moy_sd_card_sectors();
    if (s_sectors == 0) {
        return MP_ENODEV;
    }
    if (s_done == NULL) {
        s_done = xSemaphoreCreateBinary();
        if (s_done == NULL) {
            return MP_ENOMEM;
        }
    }
    xSemaphoreTake(s_done, 0);              // a drive that ended by itself gave it
    if (W == NULL) {
        W = moy_kalloc(sizeof(work_t), MOY_KMEM_PSRAM);
        if (W == NULL) {
            return MP_ENOMEM;
        }
    }
    W->sec_lba = NONE;
    gate_open();
    int rc = phy_up();
    if (rc != 0) {
        return rc;
    }
    s_ejected = 0;
    s_on = 1;                               // a state the task reads as it starts
    tusb_rhport_init_t init = {.role = TUSB_ROLE_DEVICE, .speed = CFG_TUD_MAX_SPEED == OPT_MODE_HIGH_SPEED ? TUSB_SPEED_HIGH : TUSB_SPEED_FULL};
    if (!tusb_init(MOY_TUSB_RHPORT, &init)) {
        s_on = 0;
        phy_down();
        return MP_EIO;
    }
    s_run = 1;
    if (!moy_task_spawn(MOY_TASK_USBDISK, task, NULL, &s_task)) {
        s_run = 0;
        s_on = 0;
        tud_deinit(MOY_TUSB_RHPORT);
        phy_down();
        return MP_ENOMEM;
    }
    #if SWITCHES_USJ
    if (!s_shutdown) {
        esp_register_shutdown_handler(on_shutdown);
        s_shutdown = 1;
    }
    #endif
    return 0;
}

void moy_usbdisk_stop(void) {
    if (!s_on) {
        return;
    }
    s_run = 0;
    xSemaphoreTake(s_done, portMAX_DELAY);  // the task gave the port back
}

int moy_usbdisk_state(void) {
    if (!s_on) {
        return MOY_USBDISK_STOPPED;
    }
    if (s_ejected) {
        return MOY_USBDISK_EJECTED;
    }
    return tud_mounted() ? MOY_USBDISK_MOUNTED : MOY_USBDISK_WAITING;
}
