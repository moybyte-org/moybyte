"""The ≡ dropdown / system menu's UI layer (#52), extracted from Workstation
(runtime/console.py).

The split follows the console's other sub-UIs, but the menu is more woven into
the tested `ws.` surface than the update/perf screens, so the seam is drawn to
keep that surface intact:
  * STAYS on Workstation: the `sysmenu` Popup object, the `_about` modal flag,
    the injected `reboot_hook`, and `toggle_sysmenu()` itself -- all are read or
    driven directly by tests (ws.sysmenu / ws.toggle_sysmenu() / ws._about /
    ws.reboot_hook) and by the device. `toggle_sysmenu` just calls
    `self.menu_ui._sysmenu_items()` for the row list now.
  * SystemMenuUI (here): the row builder (_sysmenu_items), the per-item action
    callbacks (_menu_restart_cart / _menu_delete_cart / _menu_about /
    _menu_reboot), and the drawing (_draw_sysmenu / _draw_about /
    _firmware_version_text). The action callbacks are stored (as bound methods)
    in the Popup's item tuples and fired on activation, so they work unchanged
    wherever the activation is handled.

Dependency profile (the facade lens, shell_architecture_v1.md §2) -- this is the
surface where the *dangerous* privileged verbs concentrate, through `self.ws`:
  * shared (non-privileged): ws.sys_canvas, ws.sysmenu, ws._dirty, ws.cart,
                             ws.launcher, ws.apply, ws.go_home, ws.open_settings,
                             ws.updater (read-only, for the version string)
  * privileged (draft make_system_api): ws.reboot_hook (REBOOT), ws.del_cart
                             (DELETE a cart), ws._about (open the about modal)

`NAMES` is injected at construction (circular-import reason as the other UIs).
The `_POPUP_*` layout constants are duplicated here (foundational chrome geometry,
the same duplication BlockEditorUI's `_BLK_*`/`_BASE_W` use) so the drawing bodies
stay byte-for-byte identical to the pre-extraction versions.
"""

# Mirrors console.py's _POPUP_* (the ≡ dropdown geometry); duplicated rather than
# imported back to avoid the circular import console.py -> system_menu_ui -> console.
_POPUP_Y = 18                 # top edge flush under the 18px bar (== _STATUS_H)
_POPUP_ROW_H = 12             # per-row height (selectable + header rows alike)
_POPUP_PAD_X = 4              # text inset from the panel left
_POPUP_SEP_H = 1              # separator line height


class SystemMenuUI:
    def __init__(self, ws, names):
        self.ws = ws
        # Injected instead of imported back from console.py (see module docstring).
        self._NAMES = names

    def _sysmenu_items(self):
        """The rows for this open of the ≡ menu (see class note for the tuple form).
        The cart group is OMITTED entirely (not greyed) when no cart is open."""
        rows = []
        if self.ws.cart is not None:
            rows.append(("header", "CART"))
            rows.append(("item", "RESTART CART", self._menu_restart_cart))
            rows.append(("item", "DELETE CART", self._menu_delete_cart))
            rows.append(("sep",))
        rows.append(("header", "SYSTEM"))
        rows.append(("item", "SETTINGS", self.ws.open_settings))
        rows.append(("item", "ABOUT", self._menu_about))
        rows.append(("item", "REBOOT", self._menu_reboot))
        return rows

    def _menu_restart_cart(self):
        # Re-run the open cart from its current config (TIC-80 restart), landing back
        # on the running-cart screen -- exactly what GO/apply does.
        if self.ws.cart is not None:
            self.ws.apply()

    def _menu_delete_cart(self):
        # Delete the OPEN cart (del_cart targets self.cart when a cart is open -- which a
        # picker-opened cart is, even if it's not the launcher selection), then go home.
        # del_cart guards read-only / last-cart. Count the FULL cart list (a wallpaper
        # isn't in the launcher run-grid) to detect the deletion.
        before = len(self.ws._all_carts)
        self.ws.del_cart()
        if len(self.ws._all_carts) < before:
            self.ws.go_home()

    def _menu_about(self):
        # A tiny dismissible info modal (any tap / ESC / B closes it), drawn on top.
        self.ws._dirty = True
        self.ws._about = True

    def _menu_reboot(self):
        # Device: the injected reboot hook (machine.reset). Host / no hook: a safe
        # fallback to the home launcher (a hard reset would kill the sim window).
        self.ws._dirty = True
        hook = self.ws.reboot_hook
        if hook is not None:
            try:
                hook()
                return
            except Exception as exc:  # noqa: BLE001
                print("Moybyte reboot failed:", exc)
        self.ws.go_home()                 # safe stub when no reboot hook is wired

    def _draw_sysmenu(self):
        """The ≡ dropdown (#52): a left-anchored panel flush under the bar, one row per
        item. The selected row gets a full-width bright accent fill + light label;
        unselected rows sit on the panel base fill; headers read dim grey and a 1px
        line separates groups. Index-only verbs + petme128 text (host == device). All
        on the SYSTEM canvas, on top of everything."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        m = self.ws.sysmenu
        # Geometry scales with the popup's fs (set by toggle_sysmenu from the
        # effective font scale, #39/#58): the rows hold fs-scaled petme128 text,
        # so unscaled 12px rows overlap at font 2 (glass-found on the P4). fs=1
        # keeps every product byte-identical (the 320x240 baseline).
        fs = m.fs
        x, y, w, h = m.panel_rect()
        cv.rect(x, y, w, h, NAMES["dark_purple"])          # panel base fill
        cv.rectb(x, y, w, h, NAMES["indigo"])              # framed edge
        cy = _POPUP_Y * fs
        for idx in range(len(m.items)):
            it = m.items[idx]
            kind = it[0]
            if kind == "sep":
                cv.rect(x + 1, cy, w - 2, _POPUP_SEP_H * fs, NAMES["indigo"])
                cy += _POPUP_SEP_H * fs
                continue
            label = it[1]
            tx = x + _POPUP_PAD_X * fs
            ty = cy + 2 * fs
            if kind == "header":
                cv.print(label, tx, ty, NAMES["dark_grey"], 1)   # dim section title
            elif idx == m.sel:
                cv.rect(x + 1, cy, w - 2, _POPUP_ROW_H * fs, NAMES["indigo"])  # highlight
                cv.print(label, tx, ty, NAMES["white"], 1)
            else:
                cv.print(label, tx, ty, NAMES["light_grey"], 1)
            cy += _POPUP_ROW_H * fs

    def _draw_about(self):
        """The ABOUT info modal (#52): a small centered panel with the console name +
        firmware version, dismissed by any tap / ESC / B. Drawn on top of everything."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        lines = ("MOYBYTE CONSOLE", "v0.4", "", "TAP TO CLOSE")
        ver = self._firmware_version_text()
        if ver:
            lines = ("MOYBYTE CONSOLE", ver, "", "TAP TO CLOSE")
        fs = getattr(cv, "font_scale", 1)   # scaled text -> scaled panel (#39/#58)
        fw = 8 * fs
        w = 0
        for ln in lines:
            w = max(w, len(ln) * fw)
        w += 24 * fs
        w = min(w, cv.w - 16 * fs)
        h = 20 * fs + len(lines) * 12 * fs
        x = (cv.w - w) // 2
        y = (cv.h - h) // 2
        cv.rect(x, y, w, h, NAMES["black"])
        cv.rectb(x, y, w, h, NAMES["pink"])
        ly = y + 10 * fs
        for ln in lines:
            cv.print(ln, x + (w - len(ln) * fw) // 2, ly, NAMES["white"], 1)
            ly += 12 * fs

    def _firmware_version_text(self):
        """A short firmware-version string for ABOUT, or "" when unknown (host). Reads
        the injected updater's version when present (device moy_ota.FIRMWARE_VERSION)."""
        u = self.ws.updater
        if u is not None:
            v = getattr(u, "version", None)
            try:
                v = v() if callable(v) else v
            except Exception:  # noqa: BLE001
                v = None
            if v is not None:
                return "FW " + str(v)
        return ""
