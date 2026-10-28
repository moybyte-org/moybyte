"""The mp_task call-list guard (tools/mp_task_calls.py), over the esp32 port's
main.c as MicroPython tagged it at v1.28.0 and v1.29.0
(tests/fixtures/mp_task/, verbatim, MIT), and the kernel's copy of mp_task
(native/moy_kernel/moy_kernel.c) held to the record."""

import os
import subprocess

import pytest

import tools.mp_task_calls as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "mp_task")
RECORD = os.path.join(ROOT, "native", "moy_kernel", "mp_task_calls.txt")
KERNEL = os.path.join(ROOT, "native", "moy_kernel", "moy_kernel.c")


def _main_c(tag):
    with open(os.path.join(FIX, "main_%s.c" % tag)) as f:
        return f.read()


def test_the_list_is_the_teardown_in_order():
    calls = g.call_list(_main_c("v1.28.0"))
    i = calls.index
    assert calls[0] == "[MICROPY_ESP_IDF_ENTRY]"
    assert i("label soft_reset") < i("call gc_init") < i("call mp_init")
    assert (i("label soft_reset_exit") < i("call machine_uart_deinit_all")
            < i("call machine_timer_deinit_all") < i("call gc_sweep_all")
            < i("call machine_deinit") < i("call mp_deinit") < i("goto soft_reset"))
    gated = calls[i("call machine_uart_deinit_all") - 1]
    assert gated == "#if MICROPY_PY_MACHINE_UART"


def test_v1_28_to_v1_29_is_a_change_the_guard_names():
    """The bump this guard was written for: v1.29.0 adds the WLAN CSI deinit
    ahead of the UARTs and drops machine_timer_deinit_all (timers are freed by
    their finaliser, inside gc_sweep_all)."""
    old, new = g.call_list(_main_c("v1.28.0")), g.call_list(_main_c("v1.29.0"))
    report = g.compare(old, new, "v1.28.0", "v1.29.0")
    assert report.startswith("!! mp_task changed shape")
    removed = {l[1:] for l in report.splitlines() if l.startswith("-") and not l.startswith("---")}
    added = {l[1:] for l in report.splitlines() if l.startswith("+") and not l.startswith("+++")}
    assert removed == {"call machine_timer_deinit_all"}
    assert added == {"#if MICROPY_PY_NETWORK_WLAN_CSI", "call wifi_csi_deinit", "#endif"}


def test_the_committed_record_is_the_pinned_tags():
    tag, functions, recorded = g.read_record(RECORD)
    assert tag == "v1.28.0"
    assert functions == list(g.DEFAULT_FUNCTIONS)
    assert g.compare(recorded, g.call_list(_main_c("v1.28.0"), functions), tag, "v1.28.0") == ""
    assert g.compare(recorded, g.call_list(_main_c("v1.29.0"), functions), tag, "v1.29.0") != ""


def test_the_kernels_copy_is_the_record_plus_its_own_calls():
    """The VM service is mp_task copied: with the kernel's own calls taken out
    (every `moy_*`), its list is the record's, but for the two calls the copy
    replaces -- a missing first heap area lands on the recovery floor instead
    of `esp_restart`, and the pin sweep is the kernel's, which spares its own
    ISRs, instead of `machine_pins_deinit`. A tag bump that moves the record
    moves this test with it, so the copy cannot fall behind the review."""
    with open(KERNEL) as f:
        src = f.read()
    mine = g.call_list(src, ("app_main", "moy_vm_task", "platform_mbedtls_time"))
    rename = {"[app_main]": "[MICROPY_ESP_IDF_ENTRY]", "[moy_vm_task]": "[mp_task]"}
    mine = [rename.get(e, e) for e in mine if not e.startswith("call moy_")]
    _tag, _functions, recorded = g.read_record(RECORD)
    i = recorded.index("call esp_restart")
    assert recorded[i - 1] == "call printf"        # "mp_task_heap allocation failed!"
    want = recorded[:i] + recorded[i + 1:]
    want.remove("call machine_pins_deinit")
    assert mine == want
    assert recorded.count("call esp_restart") == 1
    assert "call machine_pins_deinit" not in mine
    assert "moy_kernel_pins_deinit();" in src


def test_the_teardown_gives_the_vms_off_heap_buffers_back():
    """moy_alloc's registry outlives the VM, and the views that named its
    buffers do not: after the sweep the teardown frees every buffer still
    live. Without it a soft reset stranded what a VM held at its end (on the
    Guition S3 a cover's bytes and its decode scratch, 46,456 B of PSRAM a
    cycle) and a few dozen `kstop` cycles ran the boot out of memory. The
    on-glass half is each console suite's `kstop 20`."""
    with open(KERNEL) as f:
        calls = g.call_list(f.read(), ("moy_vm_task",))
    i = calls.index
    assert i("call gc_sweep_all") < i("call moy_alloc_vm_swept") < i("call mp_deinit")
    with open(os.path.join(ROOT, "native", "moy_alloc", "modmoy_alloc.c")) as f:
        alloc = f.read()
    body = alloc.split("void moy_alloc_vm_swept(void) {", 1)[1].split("\n}\n", 1)[0]
    assert "heap_caps_free(node->ptr);" in body and "moy_buf_live = node->next;" in body


def test_comments_strings_and_formatting_are_not_changes():
    src = _main_c("v1.29.0")
    noisy = (src.replace("gc_sweep_all();", "gc_sweep_all( /* foo(); */ );  // bar();")
                .replace('mp_hal_stdout_tx_str("MPY: soft reboot\\r\\n");',
                         'mp_hal_stdout_tx_str(\n        "x(); y();");'))
    assert noisy != src
    assert g.call_list(noisy) == g.call_list(src)


@pytest.mark.parametrize("edit", [
    ("    mp_deinit();\n", "    mp_deinit();\n    extra_deinit();\n"),          # added
    ("    machine_deinit();\n", ""),                                               # removed
    ("    machine_deinit();\n", "    #if X\n    machine_deinit();\n    #endif\n"),  # newly gated
])
def test_an_added_removed_or_gated_call_is_a_change(edit):
    src = _main_c("v1.29.0")
    assert src.count(edit[0]) == 1
    assert g.call_list(src.replace(*edit)) != g.call_list(src)


def test_a_moved_call_is_a_change():
    src = _main_c("v1.29.0")
    a = "    gc_sweep_all();\n"
    b = "    machine_deinit();\n"
    moved = src.replace(a, "").replace(b, b + a)
    assert g.call_list(moved) != g.call_list(src)


def _repo(tmp_path, main_c):
    """A one-commit checkout whose ports/esp32/main.c is main_c."""
    d = tmp_path / "mpy"
    (d / "ports" / "esp32").mkdir(parents=True)
    (d / "ports" / "esp32" / "main.c").write_text(main_c)
    run = lambda *a: subprocess.run(["git", "-C", str(d)] + list(a), check=True,
                                    capture_output=True)
    run("init", "-q")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "tag")
    return d


def test_check_reads_head_not_the_patched_working_file(tmp_path, capsys):
    d = _repo(tmp_path, _main_c("v1.29.0"))
    rec = tmp_path / "rec.txt"
    assert g.main(["record", str(d), str(rec)]) == 0
    main_c = d / "ports" / "esp32" / "main.c"
    main_c.write_text(main_c.read_text().replace("    gc_sweep_all();\n", ""))
    assert g.main(["check", str(d), str(rec)]) == 0


def test_check_fails_loudly_on_a_bumped_checkout(tmp_path, capsys):
    d = _repo(tmp_path, _main_c("v1.29.0"))
    rec = tmp_path / "rec.txt"
    rec.write_text(g.render_record("v1.28.0", g.call_list(_main_c("v1.28.0"))))
    assert g.main(["check", str(d), str(rec)]) == 1
    err = capsys.readouterr().err
    assert "-call machine_timer_deinit_all" in err
    assert "+call wifi_csi_deinit" in err
