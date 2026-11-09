# The native and WASI page fixtures' page as a Python app
# (tests/fixtures/elf/elf_page.moy, tests/fixtures/wasm/wasi_page.moy): a
# label, a toggle, a text field and a button whose press quits.
# tests/test_ui_linked.py holds the three to the same pixels.

on = True
name = ""


def _draw():
    global on, name
    ui.page("Native page")
    ui.label("Hello")
    on = ui.toggle("Sound", on)
    name, _ = ui.field("Name", name, 32)
    if ui.button("Done", "primary"):
        quit()
