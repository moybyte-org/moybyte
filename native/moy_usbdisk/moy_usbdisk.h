// moy_usbdisk: the card on a computer over USB mass storage
// (docs/os_2026-11/between_apps.md section 2; runtime/system/usb_drive.py is
// the mode over it).
//
// A TinyUSB device with one MSC interface whose one LUN is the board's card,
// its sectors read and written through moy_sd_card_io. It starts only on a
// card the store has let go of (native/moy_store/moy_card.c's hold) and the
// store cannot mount the card again until it stops: never both at once.
//
// WHICH PORT, by chip:
//   ESP32-S3 (the T-Deck)  the OTG controller on the one internal PHY, switched
//                          from USB-Serial/JTAG while it runs: the dev channel
//                          and the console are gone until it stops, which
//                          switches the PHY back (the USJ device enumerates
//                          again). A reset never comes up on the OTG side: a
//                          restart switches back first (a shutdown handler),
//                          and the image's start puts the PHY back on
//                          USB-Serial/JTAG before anything prints. The BOOT
//                          button reaches the ROM loader either way.
//   ESP32-P4 (the Waveshare) the high-speed OTG port (the UTMI PHY), which the
//                          `usb` device API's host also drives
//                          (native/p4/moy_usb): each refuses while the other
//                          holds the port. The dev channel (the CH343) stays.
//
// The driver's own task gives the port back with no VM: WAIT_US with no
// computer configuring it (never, or unplugged before it was), or EJECTED_US
// after an eject the console did not take back (moy_usbdisk.c). The store
// stays held until the console sees the drive stopped and reopens it.
//
// On a card that shares the panel's SPI host (the T-Deck's), every transfer
// waits for a gap between the panel's bands (moy_flush's band-gap gate), as
// the LoRa radio's do.
//
// The TinyUSB stack is espressif/tinyusb, built with moy_tusb_config.h by
// this module's micropython.cmake; the image's MicroPython USB device stays
// off (MICROPY_HW_ENABLE_USBDEV 0).

#ifndef MOY_USBDISK_H
#define MOY_USBDISK_H

enum {
    MOY_USBDISK_STOPPED = 0,
    MOY_USBDISK_WAITING,            // on the bus, no computer has configured it
    MOY_USBDISK_MOUNTED,            // a computer configured it
    MOY_USBDISK_EJECTED,            // the computer ejected it
};

// Start: 0, or an errno (EBUSY: the card is not held, or the port's host is
// up; ENODEV: no card; EIO: the stack did not start).
int moy_usbdisk_start(void);
void moy_usbdisk_stop(void);
int moy_usbdisk_state(void);
// Whether it is running: what the `usb` host refuses on.
int moy_usbdisk_on(void);

#endif // MOY_USBDISK_H
