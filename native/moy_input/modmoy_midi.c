// moy_midi's MicroPython binding: the `midi` device API's driver (moy_midi.h)
// as the module `moy_midi`, its names those face.json lists, every row
// generated into modmoy_midi_face.h. A refusal is
// a negative errno, which the face (runtime/system/dev_midi.py) raises.
//
//   open(owner) close(owner) sweep(owner) owner() source()
//   port(owner, transport, unit) -> port  unport(owner, port)
//   ports(owner) -> n      the ports it holds, a compiled program's handles' too
//   uart(owner, port)  feed(port, buf)  ble_in(owner, port, buf)
//   ble(owner, port, addr, type)  ble_state(owner, port)
//   ble_send(owner, port, msg, ms)
//   usb_pack(msg, out) -> n  ble_pack(msg, ms, out) -> n  reset()

#include "py/runtime.h"

#include "moy_midi.h"

#include "modmoy_midi_face.h"

static const mp_rom_map_elem_t midi_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_midi) },
    MOY_FACE_MOY_MIDI_ROWS
};
static MP_DEFINE_CONST_DICT(midi_globals, midi_globals_table);

const mp_obj_module_t moy_midi_module = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&midi_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_midi, moy_midi_module);
