# A terminal on the public UI API's text area (ui.text_view,
# docs/ui_api_2026-10.md): a scrollback that follows its foot as lines come,
# a name in colour on each line (an SGR escape), and an input line with its
# caret at the bottom. term_lua.moy is the same app in Lua, and
# tests/test_text_view.py holds the two to the same pixels. pmem 0 is the
# lines written, 1 the first line shown, 2 the rows shown.

log = ["\x1b[36mmoy\x1b[0m: hello", "\x1b[32mana\x1b[0m: hi there", "\x1b[90m-- joined --\x1b[0m"]
typed = "ls -la"
frames = 0


def _draw():
    global frames
    frames += 1
    if frames % 3 == 0 and frames <= 30:
        n = len(log)
        log.append("\x1b[3%dmuser%d\x1b[0m: line %d of the log, a little longer than most" % (
            1 + n % 6, n % 7, n))
    ui.page("Term")
    top, rows = ui.text_view("log", log, {"rows": 9, "follow": True, "wrap": True})
    ui.text_view("input", ["> " + typed], {"rows": 1, "caret": (0, 2 + len(typed)),
                                           "syntax": "plain"})
    pmem(0, len(log))
    pmem(1, top)
    pmem(2, rows)
