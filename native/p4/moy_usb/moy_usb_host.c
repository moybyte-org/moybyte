// moy_usb's class drivers over ESP-IDF's USB Host Library (moy_usb.h): the
// board half, on the OTG port the host library takes by default (a P4's
// high-speed one).
//
// TWO TASKS, made at the first open: the library's daemon, which runs the
// bus (enumeration, the root port's power), and this file's client, which
// takes each new device -- its descriptors walked for the first HID, CDC-ACM
// or USB-MIDI interface, the interfaces claimed, a HID boot device put in its
// boot protocol, an IN transfer kept submitted on its IN endpoint -- and
// lets each gone one go: its endpoints halted and flushed, and once no
// transfer of it is in flight, its transfers freed, its interfaces released
// and the device closed. Every transfer callback runs on the client task.
//
// The state is one PSRAM allocation; the tasks' stacks are the RTOS's.

#include "moy_usb.h"

#include <string.h>

#if defined(__has_include)
#if __has_include("esp_heap_caps.h") && __has_include("usb/usb_host.h")
#define USB_ON_BOARD 1
#endif
#endif

#if defined(USB_ON_BOARD) && defined(MOY_API_USB)

#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#include "usb/usb_host.h"

#define IN_BYTES 512
#define OUT_BYTES 512

typedef struct {
    usb_device_handle_t h;
    uint8_t used, addr, cls, closing;
    uint8_t ifs[2], nif, comm_if;
    uint8_t ep_in, ep_out;
    uint16_t mps_in;
    usb_transfer_t *in, *out, *ctl;
    volatile uint8_t inflight, out_busy;
} dev_t;

typedef struct {
    usb_host_client_handle_t client;
    dev_t dev[MOY_USB_DEVS];
    uint8_t pending[MOY_USB_DEVS];      // addresses the client has yet to take
    uint8_t npending;
} host_t;

static host_t *H;

static dev_t *by_addr(uint8_t addr) {
    for (int i = 0; i < MOY_USB_DEVS; i++) {
        if (H->dev[i].used && H->dev[i].addr == addr) {
            return &H->dev[i];
        }
    }
    return NULL;
}

// -- transfers ---------------------------------------------------------------------

static void in_cb(usb_transfer_t *x) {
    dev_t *d = x->context;
    d->inflight--;
    if (x->status == USB_TRANSFER_STATUS_COMPLETED && x->actual_num_bytes > 0) {
        moy_usb_on_in(d->addr, x->data_buffer, (size_t)x->actual_num_bytes);
    }
    if (!d->closing && (x->status == USB_TRANSFER_STATUS_COMPLETED
                        || x->status == USB_TRANSFER_STATUS_TIMED_OUT)) {
        if (usb_host_transfer_submit(x) == ESP_OK) {
            d->inflight++;
        }
    }
}

static void out_cb(usb_transfer_t *x) {
    dev_t *d = x->context;
    d->inflight--;
    d->out_busy = 0;
    moy_usb_on_sent(d->addr, x->status == USB_TRANSFER_STATUS_COMPLETED ? 0 : -MOY_USB_EIO);
}

static void ctl_cb(usb_transfer_t *x) {
    dev_t *d = x->context;
    d->inflight--;
}

// A class request to interface `iface` (bmRequestType 0x21), its data `d`.
static int ctl(dev_t *d, uint8_t req, uint16_t value, uint8_t iface, const uint8_t *data,
               uint16_t n) {
    if (d->ctl == NULL || d->closing) {
        return -MOY_USB_ENODEV;
    }
    for (int tries = 0; d->inflight && tries < 50 && d->ctl->status == 0; tries++) {
        vTaskDelay(1);
    }
    usb_setup_packet_t *s = (usb_setup_packet_t *)d->ctl->data_buffer;
    s->bmRequestType = 0x21;
    s->bRequest = req;
    s->wValue = value;
    s->wIndex = iface;
    s->wLength = n;
    if (n) {
        memcpy(d->ctl->data_buffer + sizeof(usb_setup_packet_t), data, n);
    }
    d->ctl->num_bytes = (int)(sizeof(usb_setup_packet_t) + n);
    d->ctl->device_handle = d->h;
    d->ctl->bEndpointAddress = 0;
    d->ctl->callback = ctl_cb;
    d->ctl->context = d;
    d->inflight++;
    if (usb_host_transfer_submit_control(H->client, d->ctl) != ESP_OK) {
        d->inflight--;
        return -MOY_USB_EIO;
    }
    return 0;
}

// -- a new device --------------------------------------------------------------------

static void name_of(const usb_str_desc_t *s, char *out) {
    out[0] = 0;
    if (s == NULL) {
        return;
    }
    int n = (s->bLength - 2) / 2, k = 0;
    for (int i = 0; i < n && k < MOY_USB_NAME; i++) {
        uint16_t c = s->wData[i];
        out[k++] = c >= 32 && c < 127 ? (char)c : '?';
    }
    out[k] = 0;
}

static void take(uint8_t addr) {
    dev_t *d = NULL;
    for (int i = 0; i < MOY_USB_DEVS && d == NULL; i++) {
        if (!H->dev[i].used) {
            d = &H->dev[i];
        }
    }
    if (d == NULL) {
        return;
    }
    memset(d, 0, sizeof(*d));
    if (usb_host_device_open(H->client, addr, &d->h) != ESP_OK) {
        return;
    }
    d->used = 1;
    d->addr = addr;
    moy_usb_dev_t info;
    memset(&info, 0, sizeof(info));
    info.addr = addr;
    info.iface_class = 0xff;
    const usb_device_desc_t *dd;
    const usb_config_desc_t *cd;
    usb_device_info_t di;
    if (usb_host_get_device_descriptor(d->h, &dd) == ESP_OK) {
        info.vid = dd->idVendor;
        info.pid = dd->idProduct;
    }
    if (usb_host_device_info(d->h, &di) == ESP_OK) {
        name_of(di.str_desc_product, info.name);
    }
    if (usb_host_get_active_config_descriptor(d->h, &cd) == ESP_OK) {
        // the first interface of a class the OS drives, and its endpoints
        const uint8_t *p = (const uint8_t *)cd, *end = p + cd->wTotalLength;
        int cur = -1, cur_cls = 0, cur_sub = 0, cur_proto = 0;
        while (p + 2 <= end && p[0] >= 2) {
            if (p[1] == USB_B_DESCRIPTOR_TYPE_INTERFACE && p[0] >= 9) {
                const usb_intf_desc_t *it = (const usb_intf_desc_t *)p;
                cur = -1;
                if (it->bAlternateSetting == 0) {
                    cur_cls = it->bInterfaceClass;
                    cur_sub = it->bInterfaceSubClass;
                    cur_proto = it->bInterfaceProtocol;
                    if (info.iface_class == 0xff) {
                        info.iface_class = (uint8_t)cur_cls;
                    }
                    int want = cur_cls == 3 ? MOY_USB_HID
                             : cur_cls == 0x0a ? MOY_USB_CDC
                             : cur_cls == 1 && cur_sub == 3 ? MOY_USB_MIDI : 0;
                    if (cur_cls == 2 && d->cls == 0) {
                        d->comm_if = it->bInterfaceNumber;
                    }
                    if (want && (d->cls == 0 || d->cls == want) && d->nif < 2) {
                        if (d->cls == 0) {
                            d->cls = (uint8_t)want;
                            if (want == MOY_USB_HID && cur_sub == 1) {
                                info.kind = cur_proto == 1 ? MOY_USB_KIND_KEYBOARD
                                          : cur_proto == 2 ? MOY_USB_KIND_MOUSE : 0;
                            }
                        }
                        if (want == d->cls) {
                            cur = it->bInterfaceNumber;
                            d->ifs[d->nif++] = (uint8_t)cur;
                        }
                    }
                }
            } else if (p[1] == USB_B_DESCRIPTOR_TYPE_ENDPOINT && p[0] >= 7 && cur >= 0) {
                const usb_ep_desc_t *ep = (const usb_ep_desc_t *)p;
                int type = ep->bmAttributes & 3;
                int is_in = (ep->bEndpointAddress & 0x80) != 0;
                if (is_in && d->ep_in == 0 && (type == 2 || type == 3)) {
                    d->ep_in = ep->bEndpointAddress;
                    d->mps_in = ep->wMaxPacketSize & 0x7ff;
                } else if (!is_in && d->ep_out == 0 && type == 2) {
                    d->ep_out = ep->bEndpointAddress;
                }
            }
            p += p[0];
        }
    }
    info.cls = d->cls;
    for (int i = 0; i < d->nif; i++) {
        usb_host_interface_claim(H->client, d->h, d->ifs[i], 0);
    }
    if (d->cls == MOY_USB_CDC && d->comm_if != d->ifs[0]) {
        usb_host_interface_claim(H->client, d->h, d->comm_if, 0);
    }
    usb_host_transfer_alloc(sizeof(usb_setup_packet_t) + 64, 0, &d->ctl);
    if (d->cls == MOY_USB_HID && info.kind != 0) {
        ctl(d, 0x0b, 0, d->ifs[0], NULL, 0);        // SET_PROTOCOL: boot
    }
    if (d->ep_in != 0 && d->mps_in != 0) {
        int n = d->cls == MOY_USB_HID ? d->mps_in : IN_BYTES - IN_BYTES % d->mps_in;
        if (usb_host_transfer_alloc((size_t)n, 0, &d->in) == ESP_OK) {
            d->in->num_bytes = n;
            d->in->device_handle = d->h;
            d->in->bEndpointAddress = d->ep_in;
            d->in->callback = in_cb;
            d->in->context = d;
            d->inflight++;
            if (usb_host_transfer_submit(d->in) != ESP_OK) {
                d->inflight--;
            }
        }
    }
    if (d->ep_out != 0) {
        usb_host_transfer_alloc(OUT_BYTES, 0, &d->out);
    }
    moy_usb_on_attach(&info);
}

// A gone device: its endpoints stopped; the client task lets it go once
// nothing of it is in flight.
static void gone(usb_device_handle_t h) {
    for (int i = 0; i < MOY_USB_DEVS; i++) {
        dev_t *d = &H->dev[i];
        if (d->used && d->h == h && !d->closing) {
            d->closing = 1;
            uint8_t eps[2] = {d->ep_in, d->ep_out};
            for (int k = 0; k < 2; k++) {
                if (eps[k]) {
                    usb_host_endpoint_halt(h, eps[k]);
                    usb_host_endpoint_flush(h, eps[k]);
                }
            }
            moy_usb_on_detach(d->addr);
        }
    }
}

static void reap(void) {
    for (int i = 0; i < MOY_USB_DEVS; i++) {
        dev_t *d = &H->dev[i];
        if (!d->used || !d->closing || d->inflight) {
            continue;
        }
        usb_transfer_t *xs[3] = {d->in, d->out, d->ctl};
        for (int k = 0; k < 3; k++) {
            if (xs[k] != NULL) {
                usb_host_transfer_free(xs[k]);
            }
        }
        for (int k = 0; k < d->nif; k++) {
            usb_host_interface_release(H->client, d->h, d->ifs[k]);
        }
        if (d->cls == MOY_USB_CDC && d->comm_if != d->ifs[0]) {
            usb_host_interface_release(H->client, d->h, d->comm_if);
        }
        usb_host_device_close(H->client, d->h);
        d->used = 0;
    }
}

static void client_cb(const usb_host_client_event_msg_t *m, void *arg) {
    (void)arg;
    if (m->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
        if (H->npending < MOY_USB_DEVS) {
            H->pending[H->npending++] = m->new_dev.address;
        }
    } else if (m->event == USB_HOST_CLIENT_EVENT_DEV_GONE) {
        gone(m->dev_gone.dev_hdl);
    }
}

static void client_task(void *arg) {
    (void)arg;
    for (;;) {
        usb_host_client_handle_events(H->client, pdMS_TO_TICKS(50));
        while (H->npending) {
            uint8_t a = H->pending[--H->npending];
            take(a);
        }
        reap();
    }
}

static void daemon_task(void *arg) {
    (void)arg;
    for (;;) {
        uint32_t flags;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
        if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) {
            usb_host_device_free_all();
        }
    }
}

// -- the ops -------------------------------------------------------------------------

// USB mass storage on the same port (native/moy_usbdisk), where the image
// has it: each refuses while the other holds the port.
extern int moy_usbdisk_on(void) __attribute__((weak));

int moy_usb_host_up(void) {
    return H != NULL;
}

static int op_start(void) {
    if (H != NULL) {
        return 0;
    }
    if (moy_usbdisk_on != NULL && moy_usbdisk_on()) {
        return -MOY_USB_EBUSY;
    }
    host_t *h = heap_caps_calloc(1, sizeof(*h), MALLOC_CAP_SPIRAM);
    if (h == NULL) {
        return -MOY_USB_ENODEV;
    }
    usb_host_config_t hc = {.skip_phy_setup = false, .intr_flags = ESP_INTR_FLAG_LEVEL1};
    if (usb_host_install(&hc) != ESP_OK) {
        heap_caps_free(h);
        return -MOY_USB_ENODEV;
    }
    H = h;
    xTaskCreatePinnedToCore(daemon_task, "usb_lib", 3072, NULL, 6, NULL, 0);
    usb_host_client_config_t cc = {
        .is_synchronous = false,
        .max_num_event_msg = 8,
        .async = {.client_event_callback = client_cb, .callback_arg = NULL},
    };
    if (usb_host_client_register(&cc, &H->client) != ESP_OK) {
        return -MOY_USB_ENODEV;
    }
    xTaskCreatePinnedToCore(client_task, "usb_cli", 4096, NULL, 5, NULL, 0);
    return 0;
}

static int op_line(uint8_t addr, uint32_t baud) {
    dev_t *d = H != NULL ? by_addr(addr) : NULL;
    if (d == NULL) {
        return -MOY_USB_ENODEV;
    }
    uint8_t lc[7] = {(uint8_t)baud, (uint8_t)(baud >> 8), (uint8_t)(baud >> 16),
                     (uint8_t)(baud >> 24), 0, 0, 8};          // 1 stop bit, no parity, 8 bits
    int rc = ctl(d, 0x20, 0, d->comm_if, lc, sizeof(lc));      // SET_LINE_CODING
    if (rc == 0) {
        rc = ctl(d, 0x22, 3, d->comm_if, NULL, 0);             // SET_CONTROL_LINE_STATE: DTR, RTS
    }
    return rc;
}

static int op_send(uint8_t addr, const uint8_t *data, size_t n) {
    dev_t *d = H != NULL ? by_addr(addr) : NULL;
    if (d == NULL || d->out == NULL || d->closing) {
        return -MOY_USB_ENODEV;
    }
    if (d->out_busy || n > OUT_BYTES) {
        return -MOY_USB_EBUSY;
    }
    d->out_busy = 1;
    memcpy(d->out->data_buffer, data, n);
    d->out->num_bytes = (int)n;
    d->out->device_handle = d->h;
    d->out->bEndpointAddress = d->ep_out;
    d->out->callback = out_cb;
    d->out->context = d;
    d->inflight++;
    if (usb_host_transfer_submit(d->out) != ESP_OK) {
        d->inflight--;
        d->out_busy = 0;
        return -MOY_USB_EIO;
    }
    return 0;
}

static const moy_usb_ops_t s_ops = {op_start, op_line, op_send};

const moy_usb_ops_t *moy_usb_board_ops(void) {
    return &s_ops;
}

#endif
