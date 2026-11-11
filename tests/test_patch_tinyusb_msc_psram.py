"""tools/patch_tinyusb_msc_psram.py: TinyUSB's MSC endpoint buffer becomes a
PSRAM block mscd_init takes, every hunk or none."""

import pytest

from tools import patch_tinyusb_msc_psram as p

STOCK = (
    "#if (CFG_TUD_ENABLED && CFG_TUD_MSC)\n"
    "static mscd_interface_t _mscd_itf;\n"
    "\n"
    "CFG_TUD_MEM_SECTION static struct {\n"
    "  TUD_EPBUF_DEF(buf, CFG_TUD_MSC_EP_BUFSIZE);\n"
    "} _mscd_epbuf;\n"
    "\n"
    "void mscd_init(void) {\n"
    "  tu_memclr(&_mscd_itf, sizeof(mscd_interface_t));\n"
    "}\n"
    "\n"
    "uint16_t mscd_open(uint8_t rhport, tusb_desc_interface_t const * itf_desc, uint16_t max_len) {\n"
    "  // only support SCSI's BOT protocol\n"
    "}\n"
    "#endif\n"
)


def _tree(tmp_path, text):
    f = tmp_path / p.FILE
    f.parent.mkdir(parents=True)
    f.write_text(text, encoding="utf-8")
    return f


def test_the_buffer_becomes_a_psram_block_taken_at_init(tmp_path):
    f = _tree(tmp_path, STOCK)
    assert p.apply(str(tmp_path)) == "patched"
    out = f.read_text(encoding="utf-8")
    assert "} _mscd_epbuf;" not in out
    assert "#define _mscd_epbuf (*moy_mscd_epbuf)" in out
    assert out.index("MALLOC_CAP_SPIRAM") < out.index("MALLOC_CAP_INTERNAL")
    assert "TU_VERIFY(moy_mscd_epbuf != NULL, 0);" in out


def test_a_patched_tree_is_left_as_it_is(tmp_path):
    f = _tree(tmp_path, STOCK)
    p.apply(str(tmp_path))
    once = f.read_text(encoding="utf-8")
    assert p.apply(str(tmp_path)) == "already"
    assert f.read_text(encoding="utf-8") == once


def test_a_component_that_changed_shape_is_refused_and_not_written(tmp_path):
    f = _tree(tmp_path, STOCK.replace("void mscd_init(void) {", "void mscd_init(void){"))
    before = f.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        p.apply(str(tmp_path))
    assert f.read_text(encoding="utf-8") == before
