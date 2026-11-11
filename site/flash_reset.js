// The flasher's way out of the ROM loader, driven on the Web Serial port's
// DTR/RTS lines. What each board needs is data: site/build.py derives
// `after_lines` and `rest` from the board's own board.toml (`[flash]` and
// `[serial]`) and the page only plays them.
//
// `after_lines` is esptool's custom reset grammar: steps joined by "|", each
// D (DTR) or R (RTS) with 1 (asserted) or 0 (released), or W with a wait in
// milliseconds. Every D/R step is one setSignals() call, so the line states the
// board sees are exactly the ones written down, in that order.
//
// `rest` is the line state the board rests in. It is set in ONE setSignals()
// call, because the two lines changed one at a time pass through a mixed state,
// and RTS asserted with DTR released is a reset on every board here.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function parseLines(seq) {
  return String(seq).split("|").map((step) => {
    const m = /^([DRW])(\d+)$/.exec(step.trim());
    if (!m || (m[1] !== "W" && m[2] !== "0" && m[2] !== "1"))
      throw new Error("not a line sequence step: " + JSON.stringify(step));
    return m[1] === "W" ? { wait: Number(m[2]) }
         : m[1] === "D" ? { dataTerminalReady: m[2] === "1" }
         : { requestToSend: m[2] === "1" };
  });
}

export async function playLines(port, seq, wait = sleep) {
  for (const step of parseLines(seq)) {
    if ("wait" in step) await wait(step.wait);
    else await port.setSignals(step);
  }
}

export async function restLines(port, rest) {
  if (!rest) return;
  await port.setSignals({ dataTerminalReady: !!rest.dtr, requestToSend: !!rest.rts });
}
