# Map (grep -n a name to jump there):
#   TextVerbs                     typing, the Editor's tab landings, the Code tab's file
#   TextVerbs.can_type            whether the console holds a keyboard that types
#   TextVerbs.edit_theme          a theme opened in the Editor's theme mode
#   TextVerbs.open_code_file      the Code tab on another of the cart's scripts
#   -- the Code tab's FILE        a cart's scripts and the one the Code tab shows
"""The Workstation's TEXT verbs: whether the console can type (`can_type`, a
board's declared keyboards, a connected Bluetooth one, a page's soft keyboard),
the Editor's tab landings that stay the tested `ws.` entry points
(`set_menu_view`, `_open_menu`/`_open_paint`/`_open_map`/..., `edit_theme`,
`_leave_menu`) and the Code tab's file (`code_sources`/`code_file_name`/
`open_code_file`). A mixin of `Workstation`; the keyboard's text mode is the
input policy's (runtime/shell/input_policy.py).
"""

import ui_draw as _ui_draw

# The block vocabulary/compiler (#29). Imported under whichever name it's known by:
# bare `blocks` on the device (frozen top-level) and on the host once host_app has
# aliased it, or `runtime.blocks` when a test loads console/moy_runtime directly
# without that alias (the device path is plain `import blocks`). Mirrors
# moy_carts._import_blocks so neither module hard-depends on import order.
import blocks as _blocks_mod

# The inputs that type (`can_type`).
_TYPING_INPUTS = ("keys", "tdeck")
# The input source a paired Bluetooth keyboard is (`use_inputs`): it types
# only while it is connected.
_BLE_SOURCE = "ble"


class TextVerbs:

    def set_menu_view(self, view):
        # The tab builder moved to EditorApp.set_tab (Stage 3, editor_app.py); this
        # stays as the tested ws. entry point (cards_layer/block_editor_ui + tests +
        # the _open_* forwards call it).
        self.editor_app.set_tab(view)

    def can_type(self):
        """Can the console type into a text field? Where it holds a keyboard:
        one its board declares (`use_inputs` names a `keys` or `tdeck` input,
        the desk's, the T-Deck's), a paired Bluetooth keyboard while it is
        connected, or a page's own soft keyboard (`soft_keyboard`, the
        browser's). It is the device's answer, not the last event's: a tap on
        a T-Deck leaves its keyboard able to type. Touch alone or a gamepad
        cannot, and a field there says NO_KEYBOARD_TEXT in place of a hint
        that promises a keystroke (#239 is the on-screen keyboard). A console
        that declares no inputs answers for the input in use."""
        if self.soft_keyboard:
            return True
        kinds = self.input_kinds
        if kinds is None:
            return _ui_draw.input_kind() in _TYPING_INPUTS
        for source, kind in kinds.items():
            if kind in _TYPING_INPUTS and (source != _BLE_SOURCE or self._ble_typing()):
                return True
        return False

    def _ble_typing(self):
        """Is the paired Bluetooth keyboard connected? It is the T-Deck's
        `ble_keyboard` beside its own, the touch boards' `keyboard`; the
        service that answers Settings (`settings_capable`) says."""
        for svc in (getattr(self, "ble_keyboard", None), getattr(self, "keyboard", None)):
            if getattr(svc, "settings_capable", False):
                try:
                    return svc.settings_status()[1] == "ready"
                except Exception:  # noqa: BLE001 -- a radio error is no keyboard
                    return False
        return False

    # The per-tab landing entry points moved to EditorApp (Stage 3, editor_app.py);
    # these stay as the tested ws. entry points (bar_layer's tool switcher + the
    # player pause-B + tests call them). _open_menu is the Config-first landing --
    # EditorApp.open(project) -- so it forwards the open workspace.
    def _open_menu(self):
        self.editor_app.open(self.run.project)

    def _open_paint(self):
        self.editor_app.open_paint()

    @property
    def _editing_icons(self):
        """Whether the Editor holds a theme (its theme mode, runtime/editors/theme_editor.py):
        the windowed WM's name for it, whose window X commits it through
        `look.save_icons`."""
        app = getattr(self, "editor_app", None)
        return app is not None and app.theme is not None

    def edit_theme(self, made=None):
        """Appearance's Edit theme… (`Appearance.edit_file`, which makes `custom`
        first): the theme opened in the Editor's theme mode, on its Config tab;
        `made` names the theme a copy was just made from, which Config's status
        line names once. Done comes back to the surface it was opened from."""
        from theme_editor import ThemeDoc
        doc = ThemeDoc(self, made)
        self.editor_app.open_theme(doc, self.wm.top_kind())
        return True

    def _open_map(self):
        self.editor_app.open_map()

    def _open_scene(self):
        self.editor_app.open_scene()

    def _open_blocks(self):
        self.editor_app.open_blocks()

    def _open_music(self):
        self.editor_app.open_music()

    def _cart_has_handwritten_code(self):
        """True if the current cart's main.py is real, hand-written code that the
        block editor must not overwrite: there is non-trivial source AND it was NOT
        emitted by the block compiler (no BLOCK_MARKER) AND it isn't the throwaway
        new-cart template. A brand-new / template-only cart returns False, so a kid
        can freely start authoring it with blocks."""
        cart = self.cart
        if cart is None:
            return False
        src = cart.get("src") or ""
        if _blocks_mod.is_block_authored_source(src):
            return False                         # already block-authored main.py
        # The default new-cart template is fair game to blockify (it's boilerplate,
        # not the kid's own code) -- treat it as no real code.
        tmpl = getattr(self.carts_store, "NEW_TEMPLATE", None) if self.carts_store else None
        if tmpl is not None and src.strip() == str(tmpl.get("src", "")).strip():
            return False
        # Any remaining non-whitespace source is the kid's own code -> protect it.
        return bool(src.strip())

    def _leave_menu(self):
        # PLAY: leaving a tab RUNS the cart -- moved to EditorApp.leave (Stage 3,
        # editor_app.py). Stays as the tested ws. entry point (cards_layer/map/music/
        # block/code surfaces + host_app.escape + tests dispatch to it).
        self.editor_app.leave()

    # -- the Code tab's FILE (SPEC.md 4, #89) --------------------------------
    #
    # A hand-written cart is one script and the Code tab is that file; it always
    # was, and nothing below changes for it. A PICO-8 port is not: it arrives as
    # p8.lua (the generated layer), main.lua (the game) and one file per PICO-8
    # tab, and until now the two thirds of it that are not main.lua were
    # reachable only through the Config tab's Files -- which opens them
    # in the text handle, where there is no PLAY, no journal and no
    # crash-to-code. A crash reported at `p8.lua:412:` threw the kid at a tab
    # that could not show line 412.
    #
    # There is deliberately NO create verb here. The file list is a list; the
    # one door that adds a script is the Config tab's Files, where a
    # project's own folder already lives (docs/history/text_editing_2026-09.md step 5).

    def code_sources(self):
        """The scripts the Code tab can open, in load order. One entry for a
        one-file cart -- and at one entry the switcher draws nothing at all."""
        if self.cart is None or self.carts_store is None:
            return []
        return self.carts_store.cart_sources(self.cart)

    def code_source_size(self, name):
        """Script `name`'s size in bytes, or None where the cart has none."""
        text = self.carts_store.source_text(self.cart, name) if self.cart else None
        return len(text) if text is not None else None

    def code_file_name(self):
        """The script the Code tab is on. Never None once a cart with code is
        open; a compiled cart's code is its first `src/` file, and one that
        ships none has no Code tab and answers None."""
        if self.code_file:
            return self.code_file
        cart = self.cart or {}
        if cart.get("runtime") == "wasm":
            srcs = self.code_sources()
            return srcs[0] if srcs else None
        return cart.get("main", "main.py")

    def open_code_file(self, name):
        """Show another of the cart's scripts in the Code tab.

        A FILE switch is an exit path for the file being left, exactly as a TAB
        switch is for the tab being left (#111/#154): the outgoing buffer is
        hard-committed before the editor is rebuilt, so a half-typed line
        survives the switch, and the in-RAM op history re-baselines at the
        boundary rather than carrying one file's ops into another's journal
        line. Reusing the tab-switch discipline rather than inventing a second
        one is the whole reason this is three lines and not a subsystem."""
        if self.cart is None or name not in self.code_sources():
            return False
        if name == self.code_file_name():
            return True
        if self.editor is not None and self.run.project is self.editor_app.project:
            self.editor_app.save_current()
        self.code_file = name
        self.editor = None            # set_menu_view rebuilds it over the new file
        self.code_err = None
        self.code_err_row = None
        self.set_menu_view("code")
        self._dirty = True
        return True
