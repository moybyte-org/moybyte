// moy_dev: a compiled program's device handles, the kernel's (docs/os_2026-11/
// hardware.md section 4; native/os_calls.json's `moybyte.dev_*` rows, how
// "here"). A WASI program, an ELF module and a compiled app hold a lease or a
// device by HANDLE where a Python program holds an object; this table serves
// every lease kind and the device APIs below, each over a driver that is
// safe on the caller's own task, so they answer with no VM, through an S3's
// VM stop too, on every image that links moy_app (each links this kernel
// too). A device API's verbs are its driver module's (moy_<driver>_dev.c,
// moy_dev_kinds.h), reached weakly: an image without the driver answers
// ABSENT. The other device APIs are the console's Python
// (runtime/system/dev_handles.py): a call this table does not own answers
// MOY_DEV_PASS, and the door hands it to Python on the VM's task
// (native/moy_app/moy_app.c, dev_row).
//
// WHAT IT OWNS (the buses' drivers are moy_devbus.h's):
//   gpio<n>  a pin of the board's gpio row, leased (moy_lease.h) for the
//            process: cfg mode=in|out|od pull=up|down|none value=0|1; set
//            the same and edges=rising|falling|both|off; get value; read the
//            edges since the last read, 8 bytes each (the stamp in
//            microseconds and the level after it, each a little-endian
//            uint32), whole records only, FULL where `cap` holds none
//   pins     the `pins` permission's digital pins, each leased for the
//            process at its first use (MOY_LEASE_PINS): set gpio<n>=0|1
//            drives each as an output; get gpio<n> reads one, an input where
//            it was not driven; a pin another process holds is BUSY; its
//            close gives back the pins it took; one a process
//   uart<n>  the board's row of that name (`lease:uart<n>`): cfg baudrate
//            (115200) bits (5..8, 8) parity=none|even|odd stop (1, 2) tx rx
//            (the row's pins by default, any of the gpio row instead) timeout
//            (taken; a read never waits) rxbuf (256) txbuf (0); set the same
//            but the pins and the buffers, which are the open's; read: the
//            bytes waiting, at most `cap`; write: with no txbuf what the FIFO
//            takes now, with one into it; get any (the bytes waiting)
//   spi<n>   the board's row of that name (`lease:spi<n>`, spi2 the chip's
//            SPI2; a host whose bus another driver brought up is BUSY): cfg
//            baudrate (500000) polarity phase (0, 1) bits (8 only)
//            firstbit=msb|lsb sck mosi miso (the row's by default); set the
//            same but the pins; read: `cap` bytes clocked in, 0 clocked out;
//            write; xfer: full duplex, as many bytes in as out (FULL where
//            `cap` is shorter). No CS: a program drives its own on a pin
//   i2c      the I2C unit the kernel's bus leaves free, on two pins of the
//            gpio row (`lease:gpio`; one holder at a time): cfg scl sda
//            (required, two pins) freq (400000) addr (0..127); set addr; get
//            addr (-1 before one); read: `cap` bytes from the device at addr;
//            write: to it; xfer: a write, a repeated start and a read of
//            `cap` (a register read); BAD before an addr, ABSENT where no
//            device answers, IO where the bus times out
//   pwm<n>   pin n of the gpio row driven by an LEDC channel (`lease:gpio`;
//            MOY_DEVBUS_PWM_SLOTS at a time, FULL past them or where the
//            LEDC book, moy_ledc.h, has no channel or timer left): cfg freq
//            (5000) duty (0..65535, 32768); set the same; get freq, duty
//   adc<n>   pin n of the gpio row read by its ADC channel (`lease:gpio`; BAD
//            for a pin with none, BUSY for a unit MicroPython's machine.ADC
//            holds): get value (0..65535)
//
// THE DEVICE APIS (each its permission, MOY_DEV_P_*; one handle a process
// but midi's and http's, BUSY for a second):
//   lora     the LoRa radio (native/moy_net/moy_lora.h; its one holder the
//            lease table's MOY_LEASE_LORA, BUSY while another process holds
//            it): cfg freq sf bw cr power sync preamble (868100000, 9, 125000,
//            5, 14, 0x12, 8 at the open; a set changes the keys it names)
//            listen=0|1 (1 at the open); set the same; write: a packet out;
//            read: the next packet in, cut to `cap`; get rssi, snr (dB x 4)
//            and port of the packet read last. LoRaWAN (the driver's OTAA,
//            class A, EU868; its session the driver's): set join=otaa
//            dev_eui=<8 bytes hex> join_eui=<8> app_key=<16> (dr=0..5, 5)
//            starts a join; send=<port> (1..223) makes each write an uplink
//            on that port, send=raw a packet again, with confirmed=0|1 and
//            dr; a downlink is read as a packet is, its port from get port
//            (0: a raw packet); get joined, fcnt (uplinks sent), busy (1
//            while a transmission, a join or an uplink's receive windows
//            run: a join nobody accepted reads joined 0 once busy is 0); an
//            uplink before a join is ABSENT, one while busy BUSY;
//            a value outside the radio's range is BAD. Every word is checked
//            before any applies
//   mic      the microphone's ring (native/moy_audio/moy_mic.h, its holder
//            MOY_LEASE_MIC's): no cfg; read: PCM16 mono samples,
//            little-endian, oldest first, as many whole samples as `cap`
//            holds; get rate, drops
//   midi     one MIDI port a handle (native/moy_input/moy_midi.h; the driver's
//            one holder shared with the process's `moybyte.midi`, BUSY while
//            another process holds it, the API let go with the process's
//            last port), named by the open's cfg: uart=<row> (baud=<n>,
//            31250; the row's `lease:<row>` too, leased and its driver up
//            for the port's life), usb=<addr> (a USB-MIDI device, as a usb
//            handle's ATTACH names it; ABSENT for none there) or
//            ble=aa:bb:cc:dd:ee:ff (a BLE-MIDI peripheral: connected, its
//            MIDI characteristic found and subscribed by the driver on the
//            BLE driver's events, moy_midi_ble); write: MIDI bytes, whole
//            messages (no system exclusive; at most 256 bytes, 120 to a BLE
//            port), to it; read: the messages it sent, 3 bytes each
//            (status, data1, data2: 0 where the message has fewer), whole
//            messages only (FULL where one does not fit); get port (the
//            driver's), state (0 while a BLE port connects, 1 once messages
//            flow, 2 failed or gone); a write to a BLE port before it is
//            ready is ABSENT. Many handles a process
//   http     a GET (native/moy_net/moy_fetch.h, served off the caller's task
//            by the client's worker; the process holds the radio's book,
//            tag "http", from its first handle to its last, moy_lease.h's
//            THE RADIO): cfg url (required) cap (a body kept up to cap
//            bytes, 16384) or window (a body of any length through a window
//            of that many bytes); read: the body's next bytes, 0 while it
//            runs and at its end; get status (0 while it runs, the HTTP
//            status, IO where it got no response), errno (why), length (a
//            kept body's), ended (a window's); BAD for a URL that does not
//            parse and for https where nothing verifies it, FULL with every
//            request of the client's taken. Many handles a process
//   ble      general Bluetooth LE (native/moy_input/moy_gatt.h; its one holder
//            the lease table's MOY_LEASE_BLE, shared with the process's
//            `moybyte.ble`, BUSY while another process holds it, kept
//            through the VM's end): set, one action a set, its outcome a
//            record -- scan=<ms>, connect=aa:bb:cc:dd:ee:ff | off,
//            find=<uuid> (16-bit hex, 0x.. or not, or the 128-bit string),
//            read=<handle>, subscribe=<handle>, unsubscribe=<handle>,
//            advertise=<name> | off (with data=<hex>, the manufacturer
//            data, and interval=<ms>); write=<handle> (with response=0|1)
//            names where a write goes; cfg the same; write: to it (BAD
//            before a write=); get connected; read: the driver's events,
//            whole records (FULL where the next does not fit, which waits):
//            a 12-byte head -- the event (u16, MOY_GATT_EV_*: 1 ADV, 2
//            SCAN_DONE, 3 CONNECTED, 4 DISCONNECTED, 5 FOUND, 6 READ, 7
//            WRITTEN, 8 NOTIFY, 9 ADVERTISING), n (u16), a and b (i32, the
//            driver's: a status is 0 or -errno) -- then n bytes: ADV's the
//            address (6 bytes, as printed) and the name (a the device's
//            index, b its RSSI), READ's the value read, NOTIFY's the
//            notification's (a its handle, b its length)
//   usb      the USB host's devices (native/p4/moy_usb/moy_usb.h; its one
//            holder shared with the process's `moybyte.usb`, BUSY while
//            another process holds it; a device is its bus address, 1..127):
//            set serial=<addr> (baud=<n>, 115200) opens a CDC-ACM device's
//            line, write=<addr> names where a write goes (a serial or a
//            USB-MIDI device); cfg the same; write: to it (BAD before a
//            write=); read: the driver's events, whole records (FULL where
//            the next does not fit, which waits): a 12-byte head -- the
//            event (u16, MOY_USB_EV_*: 1 ATTACH, 2 DETACH, 3 REPORT, 4 DATA,
//            5 SENT), n (u16), a the device's address and b the driver's
//            value (i32 each: ATTACH's its class, 1 HID, 2 CDC, 3 MIDI, 0
//            OTHER; REPORT's the report's length; DATA's the bytes that
//            waited; SENT's 0 or -errno) -- then n bytes: ATTACH's the class
//            and the kind (u8 each: a HID boot keyboard 1, mouse 2), vid and
//            pid (u16) and the name, REPORT's the HID report, DATA's the
//            bytes the device sent. The open reads as an ATTACH for each
//            device already plugged in
// A device a handle holds stays through the VM's end (moy_lease.h's THE
// VM'S END, moy_dev_holds) and goes with its process.
//
// Every other name, and a handle without MOY_DEV_BASE, is MOY_DEV_PASS.
//
// THE GRAMMAR. `cfg` is words `key=value` split by spaces, a value a number
// (decimal, 0x.. hex, a sign) where the key takes one; a word with no `=` or
// no key, a key the handle does not take or a value it does not is BAD.
//
// THE ANSWERS are moy_app.h's codes, negated (MOY_DEV_E*): DENIED without
// the permission (MOY_DEV_P_* in the caller's mask, from its grant), BUSY
// where another process holds the pin, the unit or the device (a Python
// program's lease or face, or another handle) or the process holds `pins`
// or that device already, BAD for a name, a row the board does not offer, a
// key, a value or a verb its kind does not take, ABSENT for a handle not the
// caller's, a console with no gpio row and a device it lacks, FULL where a
// read's `cap` holds no record or the table is full, NOMEM, IO where a
// driver failed. A lease the table takes holds no VM stop off (no
// MOY_LEASE_VM).
//
// A handle is its process's; its end closes every handle it left open
// (moy_dev_sweep, which the process's event queue's close runs, moy_evq.h).
// Every call holds the lease table's lock (moy_lease_lock) throughout, a
// bus's transfer and a device's driver call included, so any task may make
// one.

#ifndef MOY_DEV_H
#define MOY_DEV_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_DEV_BASE 0x40000000         // every handle of this table has it
#define MOY_DEV_HANDLES 32u
#define MOY_DEV_EDGE_DEPTH 64u          // a pin's edge ring
#define MOY_DEV_PASS (-1000)            // not this table's: the console's Python serves it

enum {
    MOY_DEV_ESTALE = -1, MOY_DEV_EFULL = -2, MOY_DEV_ENOMEM = -3, MOY_DEV_EDENIED = -4,
    MOY_DEV_EIO = -6, MOY_DEV_EBAD = -7, MOY_DEV_EABSENT = -8, MOY_DEV_EBUSY = -11,
};

// The device permissions a grant's mask holds (moy_app's moy_app_grant_devs).
#define MOY_DEV_P_GPIO (1u << 0)        // lease:gpio
#define MOY_DEV_P_PINS (1u << 1)        // pins
#define MOY_DEV_P_MIC (1u << 2)
#define MOY_DEV_P_LORA (1u << 3)
#define MOY_DEV_P_BLE (1u << 4)
#define MOY_DEV_P_USB (1u << 5)
#define MOY_DEV_P_MIDI (1u << 6)
#define MOY_DEV_P_CAMERA (1u << 7)
#define MOY_DEV_P_MEDIA (1u << 8)
#define MOY_DEV_P_MEDIA_URL (1u << 9)
#define MOY_DEV_P_HTTP (1u << 10)
#define MOY_DEV_P_ROW 16                // lease:<row> of offer i: 1 << (MOY_DEV_P_ROW + i)

// The bit of permission `perm` (n bytes), 0 for one that names no device.
uint32_t moy_dev_perm_bit(const char *perm, size_t n);

// The rows, as process `pid` holding the device permissions `perms`: an
// answer >= 0 (a handle, a length, a number), a MOY_DEV_E* code, or
// MOY_DEV_PASS.
int32_t moy_dev_open(uint32_t pid, uint32_t perms, const char *name, size_t n,
                     const char *cfg, size_t cn);
int32_t moy_dev_close(uint32_t pid, int32_t h);
int32_t moy_dev_read(uint32_t pid, int32_t h, uint8_t *out, size_t cap);
int32_t moy_dev_write(uint32_t pid, int32_t h, const uint8_t *data, size_t n);
int32_t moy_dev_xfer(uint32_t pid, int32_t h, const uint8_t *data, size_t n, uint8_t *out,
                     size_t cap);
int32_t moy_dev_set(uint32_t pid, int32_t h, const char *cfg, size_t n);
int32_t moy_dev_get(uint32_t pid, int32_t h, const char *what, size_t n);
// Process `pid` ended: every handle it held closed. How many.
uint32_t moy_dev_sweep(uint32_t pid);
// Whether process `pid` holds lease class `cls`'s device by a handle here.
bool moy_dev_holds(uint32_t pid, uint8_t cls);
// The handles open (every process's).
uint32_t moy_dev_count(void);
// Every handle closed (a test's teardown).
void moy_dev_reset(void);

#endif // MOY_DEV_H
