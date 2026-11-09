# The native page fixture's page as a Python app
# (tests/fixtures/elf/elf_page.moy): a label, a toggle and a button whose
# press quits. tests/test_ui_linked.py holds the two to the same pixels.

on = True


def _draw():
    global on
    ui.page("Native page")
    ui.label("Hello")
    on = ui.toggle("Sound", on)
    if ui.button("Done", "primary"):
        quit()
