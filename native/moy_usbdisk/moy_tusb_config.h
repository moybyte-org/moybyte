// The TinyUSB configuration this module builds espressif/tinyusb with
// (micropython.cmake names it as CFG_TUSB_CONFIG_FILE for the component and
// for moy_usbdisk.c): a device with one MSC interface, under FreeRTOS, on the
// S3's full-speed OTG controller (root port 0) or the P4's high-speed one
// (root port 1).

#ifndef MOY_TUSB_CONFIG_H
#define MOY_TUSB_CONFIG_H

#include "sdkconfig.h"

#define CFG_TUSB_OS OPT_OS_FREERTOS
#define CFG_TUD_ENABLED 1
#define CFG_TUD_ENDPOINT0_SIZE 64
#define CFG_TUD_MSC 1
#define CFG_TUD_DWC2_DMA_ENABLE 0
// the events between the controller's interrupt and the task: one transfer
// is in flight at a time
#define CFG_TUD_TASK_QUEUE_SZ 8

#if CONFIG_IDF_TARGET_ESP32P4
#define MOY_TUSB_RHPORT 1
#define CFG_TUD_MAX_SPEED OPT_MODE_HIGH_SPEED
#define CFG_TUSB_RHPORT1_MODE (OPT_MODE_DEVICE | OPT_MODE_HIGH_SPEED)
// a transfer's sectors at a time: 16 per callback on the P4, whose RAM it is
#define CFG_TUD_MSC_EP_BUFSIZE 8192
#else
#define MOY_TUSB_RHPORT 0
#define CFG_TUD_MAX_SPEED OPT_MODE_FULL_SPEED
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
// one full-speed packet: the S3's internal SRAM is the scarce thing, not the
// bus, so a sector is gathered in PSRAM (moy_usbdisk.c's card_io)
#define CFG_TUD_MSC_EP_BUFSIZE 64
#endif

#endif // MOY_TUSB_CONFIG_H
