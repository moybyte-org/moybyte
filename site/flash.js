// The site's board flasher: writes a CI-built image onto a board over USB,
// from the page, with esptool-js driving the Web Serial port.
//
// What the page ships is the SAME image the `make firmware-flash-*` targets
// write over a cable -- same file, same offset, same "keep the header as built"
// flash parameters. site/build.py owns that table (site/build.py BOARDS) and
// emits it as the JSON blob read below, so the two can only disagree by an edit
// to one file.
//
// Nothing here is loaded from a CDN: the esptool-js bundle is vendored under
// site/vendor/ and the .bin comes from this site's own origin. That is not
// tidiness -- GitHub serves neither Actions artifacts nor release assets with
// CORS headers, so a cross-origin fetch of a firmware image is impossible and
// the file HAS to be same-origin. See tools/fetch_ci_firmware.py.

import { ESPLoader, Transport } from "./vendor/esptool-js/bundle.js";

const blob = document.getElementById("fw-manifest");
const MANIFEST = blob ? JSON.parse(blob.textContent) : { boards: [] };
const BOARDS = {};
for (const b of MANIFEST.boards || []) BOARDS[b.id] = b;

const SUPPORTED = "serial" in navigator;
let busy = false;

// --- one board card ---------------------------------------------------------

class Card {
  constructor(el) {
    this.el = el;
    this.board = BOARDS[el.dataset.board];
    this.go = el.querySelector(".go");
    this.erase = el.querySelector(".erase input");
    this.hand = el.querySelector(".manual input");
    this.bar = el.querySelector(".prog i");
    this.progress = el.querySelector(".prog");
    this.log = el.querySelector(".log");
    this.state = el.querySelector(".state");
    if (this.go) this.go.addEventListener("click", () => this.flash());
  }

  say(line) {
    if (!this.log) return;
    this.log.hidden = false;
    this.log.textContent += line + "\n";
    this.log.scrollTop = this.log.scrollHeight;
  }

  status(text, kind) {
    if (!this.state) return;
    // textContent, always: this carries esptool-js's error strings too.
    this.state.textContent = text;
    this.state.className = "state" + (kind ? " " + kind : "");
  }

  // The one status line that is markup rather than a message: the per-board
  // "what now" from site/build.py's BOARDS table.
  finished(html) {
    if (!this.state) return;
    this.state.innerHTML = html;
    this.state.className = "state ok";
  }

  pct(fraction) {
    if (!this.progress) return;
    this.progress.hidden = false;
    this.bar.style.width = Math.max(0, Math.min(1, fraction)) * 100 + "%";
  }

  // esptool-js logs through this. A RAW sink, deliberately: it writes progress
  // without newlines ("Connecting..." then a dot per attempt), so anything that
  // buffers until a newline swallows exactly the messages you need when a
  // connect fails -- which is how the first failure here arrived with an empty
  // log. Append verbatim, like a terminal.
  terminal() {
    const put = (d) => {
      if (!this.log) return;
      this.log.hidden = false;
      this.log.textContent += String(d).replace(/\r/g, "");
      this.log.scrollTop = this.log.scrollHeight;
    };
    return {
      clean: () => { if (this.log) this.log.textContent = ""; },
      writeLine: (d) => put(d + "\n"),
      write: put,
    };
  }

  // What to try next. esptool-js reports how it failed but never what to do,
  // and one of these is a host-side trap nobody would guess:
  //
  // "The device has been lost" -- the port is fine and still enumerated. Any
  // pyserial-based tool (esptool, miniterm, the on-glass test driver) leaves
  // VMIN=0/VTIME=0 on the tty, that setting outlives the process, and Chrome
  // maps the resulting zero-byte read to a disconnect. Replugging resets the
  // termios; so does `stty -F <port> min 1`. Reproduced and confirmed on a
  // P4 by opening an idle port with no data flowing at all.
  advice(msg) {
    if (/device has been lost/i.test(msg))
      return " — unplug the board and plug it back in, then try again. A "
           + "serial tool used before this leaves the port in a state Chrome "
           + "reads as a disconnect; replugging clears it.";
    if (/connect/i.test(msg) && this.hand && !this.hand.checked)
      return " — try the checkbox above: put the board in download mode "
           + "yourself, then flash again.";
    return "";
  }

  // The image, checked against the hash the site was built with. Same-origin
  // over HTTPS makes corruption unlikely, but a half-cached 4 MB file is
  // exactly the thing you want to catch BEFORE it is on the board.
  async image() {
    const res = await fetch(this.board.url, { cache: "no-store" });
    if (!res.ok) throw new Error("could not download the image (HTTP " + res.status + ")");
    const buf = await res.arrayBuffer();
    if (buf.byteLength !== this.board.size)
      throw new Error("the image is " + buf.byteLength + " bytes, expected " + this.board.size);
    if (crypto.subtle) {
      const digest = await crypto.subtle.digest("SHA-256", buf);
      const hex = [...new Uint8Array(digest)]
        .map((b) => b.toString(16).padStart(2, "0")).join("");
      if (hex !== this.board.sha256) throw new Error("the image failed its checksum");
      this.say("image verified: sha256 " + hex.slice(0, 16) + "…");
    }
    return new Uint8Array(buf);
  }

  async flash() {
    if (busy) return;
    const b = this.board;
    let port, transport, loader;
    try {
      // Must be the first thing after the click: requestPort() needs the user
      // activation, and an await before it can spend it.
      port = await navigator.serial.requestPort();
    } catch (err) {
      this.status("No port chosen.", "wip");
      return;
    }
    busy = true;
    document.querySelectorAll(".board .go").forEach((g) => (g.disabled = true));
    if (this.log) this.log.textContent = "";
    this.pct(0);
    try {
      this.status("Reading the image…");
      const data = await this.image();

      this.status("Connecting…");
      transport = new Transport(port, false);
      loader = new ESPLoader({
        transport,
        baudrate: b.baud,
        romBaudrate: 115200,
        terminal: this.terminal(),
        // On for the CONNECT only (turned off below, before the write). This is
        // where flashing fails, and esptool-js throws away its own diagnosis at
        // that point: connect() reports a bare "Failed to connect with the
        // device" while the useful line -- wrong boot mode vs. in the loader but
        // no sync reply -- goes only to debug.
        debugLogging: true,
        serialOptions: {
          // Chrome's default is 255 bytes, which is a lot of round trips for a
          // multi-megabyte image. Measured as making no difference to the
          // connect failures this was first added for -- kept because it is
          // the right size for the transfer, not as a fix for anything.
          bufferSize: 16384,
        },
      });
      // "I put it in the loader myself" -- the escape hatch for when the
      // auto-reset does not take. The T-Deck is always this (its reset is
      // no_reset already); the P4 offers it as a checkbox.
      const manual = this.hand && this.hand.checked;
      await loader.main(manual ? "no_reset" : b.reset);
      loader.debugLogging = false;

      // A wrong image is worse than no image: an S3 build on a P4 leaves a
      // board that will not boot until it is flashed again over a cable.
      const found = loader.chip && loader.chip.CHIP_NAME;
      if (found && found !== b.chip)
        throw new Error("this is a " + found + ", and that image is for a " + b.chip);

      this.status("Writing — do not unplug the board…");
      await loader.writeFlash({
        fileArray: [{ data, address: b.offset }],
        // "keep" everywhere: the image already carries the mode/size/frequency
        // the build baked into its header, exactly as the cable flash relies on.
        flashMode: "keep",
        flashFreq: "keep",
        flashSize: "keep",
        eraseAll: !!(this.erase && this.erase.checked),
        compress: true,
        reportProgress: (_i, written, total) => {
          this.pct(written / total);
          this.status("Writing — " + Math.round((written / total) * 100) + "%");
        },
      });
      this.pct(1);

      // Only where the board can actually be reset over the wire. The T-Deck
      // cannot (`after: null`), so asking would log a failure for something
      // that was never going to work -- its card says to press RST instead.
      // Skipped when the board was put in the loader by hand: whatever stopped
      // the reset working on the way in stops it working on the way out.
      if (b.after && !manual) {
        try {
          await loader.after(b.after, b.usb_otg);
        } catch (err) {
          this.say("reset failed (" + err.message + ") — power-cycle the board");
        }
      }
      this.finished(manual && b.after
        ? "Written. Press <b>RESET</b> on the board to start it."
        : b.done);
    } catch (err) {
      const msg = err && err.message ? err.message : String(err);
      this.say("failed: " + msg);
      this.status(msg + this.advice(msg), "warn");
    } finally {
      busy = false;
      document.querySelectorAll(".board .go").forEach((g) => (g.disabled = false));
      if (transport) { try { await transport.disconnect(); } catch (e) { /* gone already */ } }
    }
  }
}

// --- wire up ----------------------------------------------------------------

const cards = [...document.querySelectorAll(".board")].map((el) => new Card(el));

if (!SUPPORTED) {
  for (const c of cards) {
    if (c.go) {
      c.go.disabled = true;
      c.go.title = "This browser has no Web Serial";
    }
  }
  const note = document.getElementById("fw-nowebserial");
  if (note) note.hidden = false;
}
