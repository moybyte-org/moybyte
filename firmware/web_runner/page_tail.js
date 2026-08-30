
// ---- web runner transport tail (#151, threading #176) ------------------------
// The console runs in a WORKER (worker.js) and PUSHES frames; THIS thread only
// blits. It used to step the console inside the rAF callback, which put ~7ms of
// console work + ~2ms of blit on a 16.7ms budget -- so a worst-case frame
// (full repaint, GC sweep) missed vsync and dropped.
//
// Since moycore stage 4 a frame is a finished RGB565 FRAMEBUFFER (the wasm
// rasterizes with the boards' own kernel) plus a small metadata object. The
// page's JS draw-command replayer is gone, and with it the per-surface delta,
// the keyframe/resync protocol and the /assets pixel payload: a frame is
// self-contained, so dropping one costs exactly one stale frame.
//
// The worker paces itself at 60fps, so this loop has no frame gate: rAF presents
// the most recent frame and DROPS stale ones (a slow main thread must never make
// frames queue up).
var WORKER=null,pendingFrame=null,pendingFB=null,assetsJSON=null,assetsWait=[],
gotAssets=false,assetsBusy=false,assetsAt=0;
// Assets are METADATA now (size, title, audio rate, input hint) -- a few dozen
// bytes, fetched on a cart change rather than per missing cover. The coalescing
// throttle stays because a cart change and a reload can land together.
var ASSETS_MIN_MS=1000;
function fetchAssets(){
return new Promise(function(res,rej){
var now=Date.now();
if(!WORKER){if(assetsJSON)res(JSON.parse(assetsJSON));else assetsWait.push([res,rej]);return;}
if(assetsJSON&&(assetsBusy||now-assetsAt<ASSETS_MIN_MS)){res(JSON.parse(assetsJSON));return;}
assetsWait.push([res,rej]);
assetsBusy=true;assetsAt=now;
WORKER.postMessage({t:"assets"});});}
function onWorker(m){
if(m.t==="status"){sEl.textContent=m.s;}
else if(m.t==="assets"){assetsJSON=m.json;assetsBusy=false;
if(assetsWait.length){var ws=assetsWait;assetsWait=[];
for(var i=0;i<ws.length;i++)ws[i][0](JSON.parse(m.json));}
// An UNREQUESTED push is the dev hot-reload's fresh cart: re-run getA() so the
// new title/size/input hint land (the cart title may be unchanged, so df()'s
// own detection never fires).
else if(gotAssets&&typeof getA==="function"){getA().catch(function(){});}
gotAssets=true;}
// Keep only the NEWEST frame. A superseded framebuffer goes straight back to the
// worker's pool -- it is a transferable, so holding it here would starve the
// ping-pong and force a fresh 1.2MB allocation on the next painted frame.
else if(m.t==="frame"){
if(pendingFB&&WORKER)WORKER.postMessage({t:"fbret",b:pendingFB},[pendingFB]);
pendingFrame=m.s;pendingFB=m.fb||null;}
// The worker's own account of where the carts went, kept where a probe (or a
// devtools console) can read it -- console logs get trimmed by whatever is
// capturing them, and this is the evidence that the store is being written.
else if(m.t==="persist"){window.__moyPersist={mode:m.mode,s:m.s,d:m.d||"",n:m.n||0};
pzMode(m.mode,m.s);}
else if(m.t==="keep"){pzKeep();}
else if(m.t==="carts"){pzFill(m.names);}
else if(m.t==="exported"){pzDownload(m.name,m.buf);}
else if(m.t==="imported"){window.__moyImported=m.s;pzSay(m.s,!m.ok);if(m.ok)pzAsk();
if(m.report)p8Report(m.report,m.ok,m.dir);}
else if(m.t==="update"){updBound(m);}
else if(m.t==="edited"){window.__moyEdited=m.s;pzSay(m.s,!m.ok);}
else if(m.t==="pin"){pinAsk(m.tried);}
else if(m.t==="wperf"){console.log("[moy worker] "+m.s);}
// THE SEAM the pump that sees failures calls. Nothing counts failures yet --
// the sync push still requeues forever on a board that has gone -- and when
// something does, it says so HERE rather than growing a second panel of its
// own. `kind` is the whole contract: "lost" adds the unsynced-work warning,
// anything else is a restart somebody asked for.
else if(m.t==="lost"){linkLost(m.kind||"lost",m.head||"the console is not answering",
m.body||"",m.risk!==false);}
else if(m.t==="error"){console.error("[moy]",m.s);
sEl.textContent="console crash (see devtools)";sEl.style.color="#ff004d";}}
// ---- the pin prompt (2026-08-25) --------------------------------------------
// A board serves its console to anyone and its CARTS to nobody without the
// pairing pin, so a page opened by hand -- typing the address instead of
// scanning the QR -- has to ask. The worker discovers it (carts.json answers
// 403) and stops before booting the VM; this is the asking.
//
// REMEMBERED PER ORIGIN, in localStorage: a kid types four digits once per
// browser per board, not once per visit. Keyed by origin because that IS the
// board -- two consoles on one network are two origins with two pins, and one
// key would have them overwriting each other.
//
// SUBMITTING RELOADS with ?pin= on the url rather than re-messaging the worker.
// It costs a wasm re-fetch (~1.5s off a board) on a gesture that happens once,
// and it buys the one thing worth having here: after it, every path that reads
// location.search -- the worker's own boot, PLAY ON DEVICE below, the sync
// pump's batches -- sees the pin with no second source of truth to keep in step.
var PINEL=document.getElementById("pin"),PINF=document.getElementById("pinf"),
PINB=document.getElementById("pinb"),PINM=document.getElementById("pinm");
function pinKey(){return "moybyte.pin:"+location.origin;}
function pinStored(){try{return localStorage.getItem(pinKey())||"";}
catch(e){return "";}}
// Called by the loader BEFORE the worker is constructed: if this browser
// already knows this board's pin, put it on the url so the worker's very first
// carts.json carries it and nobody is asked anything.
window.__moyPinRestore=function(){
try{var q=new URLSearchParams(location.search);
if(q.get("pin"))return;                       // a QR arrival: leave it alone
var p=pinStored();if(!p)return;
q.set("pin",p);
history.replaceState(null,"","?"+q.toString()+location.hash);}
catch(e){/* no history, no storage: the prompt still works */}};
function pinAsk(tried){if(!PINEL)return;
// A pin was offered and refused: say so plainly. Without this the same empty
// box comes back and reads as the page having ignored the keystrokes.
PINM.textContent=tried?"that pin did not work -- try again":"";
if(tried){try{localStorage.removeItem(pinKey());}catch(e){}}
PINEL.style.display="flex";
try{PINF.value="";PINF.focus();}catch(e){}}
function pinSubmit(){var v=(PINF.value||"").trim();
if(!v){PINM.textContent="type the four digits";return;}
try{localStorage.setItem(pinKey(),v);}catch(e){/* private window: this visit only */}
// Keep whatever the url already said (?handheld=1, ?dev=1, ?cart=...): dropping
// those would answer the pin question by silently changing which console loads.
var q=new URLSearchParams(location.search);q.set("pin",v);
PINM.textContent="opening...";
location.search="?"+q.toString();}
if(PINB)PINB.addEventListener("click",pinSubmit);
if(PINF)PINF.addEventListener("keydown",function(e){
if(e.key==="Enter"){e.preventDefault();pinSubmit();}});
// ---- persistence row (#193) -------------------------------------------------
// The worker decides the MODE (board vs browser-local) and this only reports it.
// The row appears in EVERY mode, including board-served. It used to be hidden
// there, on the reading that "the console owns the carts" -- but the 2026-08-25
// owner call this cited says something narrower: a board-served page uses the
// BOARD's store and never a second one, which is about where carts LIVE, not
// about which direction they may travel. That call names ".moy export/import
// and the p8 converter" as ordinary parts of the picture. A board-served page
// already writes to the board's store on every edit, through the same sync
// push an import rides -- so importing there was never a new kind of write,
// and exporting is a read. (Owner correction, 2026-08-29.)
var pzEl=document.getElementById("pz"),pzS=document.getElementById("pzs"),
pzC=document.getElementById("pzc"),pzE=document.getElementById("pze"),
pzI=document.getElementById("pzi"),PZMODE=null;
function pzSay(s,warn){pzS.textContent=s;pzS.className=warn?"warn":"";}
function pzMode(mode,s){PZMODE=mode;pzSay(s,mode==="none");
pzEl.style.display="flex";pzAsk();}
function pzAsk(){if(WORKER)WORKER.postMessage({t:"carts"});}
// The worker decided this page keeps its carts HERE, in OPFS -- which the
// browser may evict under disk pressure unless the origin is asked to be made
// durable. THIS thread has to do the asking: storage.persist() is
// [Exposed=Window] and simply does not exist on the worker's navigator, so the
// worker (which owns the mode, and only asks in site mode) sends {t:"keep"}
// and this answers. Never throws and never blocks anything: a refusal, a
// missing API and a locked-down profile are all just states the chip reports.
function pzKeep(){var st=navigator.storage;
if(!st||typeof st.persist!=="function")return;
Promise.resolve().then(function(){return st.persist();})
.then(function(okd){if(WORKER)WORKER.postMessage({t:"kept",state:okd?"granted":"denied"});})
.catch(function(){});}
function pzFill(names){var keep=pzC.value;pzC.innerHTML="";
for(var i=0;i<names.length;i++){var o=document.createElement("option");
o.value=names[i];o.textContent=names[i];pzC.appendChild(o);}
if(keep&&names.indexOf(keep)>=0)pzC.value=keep;}
// A .moy zip is bytes the page never inspects -- the worker built it from the
// live VFS, which is the same folder a board reads.
function pzDownload(name,buf){var url=URL.createObjectURL(new Blob([buf],{type:"application/zip"}));
var a=document.createElement("a");a.href=url;a.download=name;document.body.appendChild(a);a.click();
document.body.removeChild(a);setTimeout(function(){URL.revokeObjectURL(url);},4000);
pzSay("exported "+name,false);}
pzE.addEventListener("click",function(){if(!WORKER||!pzC.value)return;
pzSay("exporting "+pzC.value+"...",false);WORKER.postMessage({t:"export",cart:pzC.value});});
pzC.addEventListener("mousedown",pzAsk);
function pzImport(file){if(!WORKER||!file)return;pzSay("importing "+file.name+"...",false);
file.arrayBuffer().then(function(buf){
WORKER.postMessage({t:"import",name:file.name,buf:buf},[buf]);})
.catch(function(e){pzSay("could not read that file",true);console.error(e);});}
pzI.addEventListener("change",function(){if(pzI.files&&pzI.files[0])pzImport(pzI.files[0]);
pzI.value="";});
// Drop a .moy zip or a PICO-8 cart (.p8 / .p8.png, #194) anywhere on the page.
// In every mode, once the worker has reported one. On a board-served page the
// new cart lands in the VFS like any other change and the sweep ships it to the
// board -- the identical path Make -> +New already takes, pin and all. The
// import deliberately does NOT rebase the sync watcher for exactly this reason:
// an import is a CHANGE and must stay pending until the far end has it.
window.addEventListener("dragover",function(e){if(PZMODE)e.preventDefault();});
window.addEventListener("drop",function(e){if(!PZMODE)return;
e.preventDefault();var f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];
if(f)pzImport(f);});
// ---- the PICO-8 import report (#194) ----------------------------------------
// "Report, don't crash": the cart RUNS now, so what this says is where it will
// stop agreeing with PICO-8 -- the verbs the generated shim has no answer for,
// and the ones it answers differently. That second kind is why the panel exists
// at all: a cart that quietly draws the wrong thing is worse than one that
// refused. The lines come from tools/p8_writer.report_lines so the CLI and the
// browser say the same things; this only paints them, and offers the one action
// the page cannot infer -- opening the new cart in the editor.
var P8=document.getElementById("p8"),P8T=document.getElementById("p8t"),
P8E=document.getElementById("p8e"),P8X=document.getElementById("p8x"),p8Cart=null;
function p8Report(lines,ok,dir){p8Cart=ok?(dir||null):null;
P8T.innerHTML="";
for(var i=0;i<lines.length;i++){var p=document.createElement("p");
if(i===0)p.className="head";p.textContent=lines[i];P8T.appendChild(p);}
P8.className=ok?"":"bad";
P8E.style.display=p8Cart?"":"none";
P8.style.display="block";
// Readable by a harness without scraping the DOM, the same way __moyImported is.
window.__moyReport=lines.join(" | ");}
if(P8X)P8X.addEventListener("click",function(){P8.style.display="none";});
if(P8E)P8E.addEventListener("click",function(){if(!WORKER||!p8Cart)return;
pzSay("opening "+p8Cart+" in the editor...",false);
WORKER.postMessage({t:"edit",cart:p8Cart});});
// The loader module hands the worker over once constructed.
window.__moyAttach=function(w){WORKER=w;w.onmessage=function(e){onWorker(e.data);};};
// ?pad=1 forces the touch controls on ANY device (desktop demos, touch laptops).
// Phones show them automatically -- the core's media query hides them only on
// hover-capable fine-pointer (i.e. mouse) devices; per-cart input hints still
// hide the pad for touch-only carts unless forced here.
if(new URLSearchParams(location.search).get("pad")){
document.getElementById("ctl").style.display="flex";}
var panWas=false;
function pump(){var v=pv();if(v[0]||v[1]){panWas=true;send({type:"pan",dx:v[0],dy:v[1]});}
else if(panWas){panWas=false;send({type:"pan",dx:0,dy:0});}
if(!q.length||!WORKER)return;var b=q;q=[];
WORKER.postMessage({t:"input",json:JSON.stringify({events:b})});}
function tick(ts){requestAnimationFrame(tick);
pump();
// Report the queued audio depth (worklet ring or legacy schedule -- PAGE_CORE's
// audioQueuedSecs abstracts both) so the console tops the cushion back up to
// target each frame (the crackle fix, #170) instead of rendering blind rate*dt.
// -1 = context not running yet -> the legacy per-dt render.
if(WORKER)WORKER.postMessage({t:"ahead",
v:(typeof audioQueuedSecs==="function")?audioQueuedSecs():-1});
var f=pendingFrame,fb=pendingFB;pendingFrame=null;pendingFB=null;
if(f){PERF.f++;if(fb)PERF.b+=fb.byteLength;HUD.kb=fb?fb.byteLength/1024:HUD.kb;
df(JSON.parse(f),fb);
podTick();
// Hand the buffer back for reuse once it has been blitted.
if(fb&&WORKER)WORKER.postMessage({t:"fbret",b:fb},[fb]);}}
// ---- PLAY ON DEVICE (#197) ---------------------------------------------------
// A page served BY a board can hand the open cart back to the board's own glass.
// The whole protocol is a cart NAME: the board looks it up the same way its
// serial `run` does, plays it, and its own exit returns to the connection screen
// -- so this page never has to model device state.
//
// The link shows only when BOTH are true: the host answered GET /sync (a static
// host -- moybyte.com, an export, a plain file server -- 404s it, and offering
// the button there would be a button that always fails), and a cart is actually
// open here. Probed ONCE at boot: the answer cannot change under a page, and a
// per-frame probe would be a request per frame at a board that is single-threaded.
var POD=document.getElementById("pod"),podHost=false,podLast=undefined;
function podSync(){if(!POD)return;
POD.style.display=(podHost&&assCart)?"":"none";
POD.textContent="play on device";}
function podProbe(){if(!POD)return;
fetch("sync",{method:"GET"}).then(function(r){podHost=r.ok;podSync();})
.catch(function(){});}
// A cart change re-arms the link (and clears any "playing on device: X" the last
// tap left on it). Checked off the frame loop rather than hooked into df(): the
// cart title arrives on every frame payload, so a string compare is the whole
// cost and it needs no second detection of a change page_core already tracks.
function podTick(){if(podLast!==assCart){podLast=assCart;podSync();}}
if(POD)POD.addEventListener("click",function(e){e.preventDefault();
if(!assCart)return;
var was=POD.textContent;POD.textContent="starting...";
// The pin rides THIS page's own url, exactly as the sync push's does: a page
// opened from the board's QR carries it, one opened by hand does not, and the
// board answers 403 either way rather than trusting the request.
var pin=new URLSearchParams(location.search).get("pin");
fetch("run",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({cart:assCart,pin:pin})})
.then(function(r){return r.ok?r.json():Promise.reject(r.status);})
.then(function(j){POD.textContent="playing on device: "+(j.run||assCart);})
.catch(function(err){POD.textContent=(err===403)?"device refused the pin":was;
console.log("[moy] play on device failed: "+err);});});
// ---- the LINK surface: one panel, and the REASON is the point ---------------
// A board stops answering for two very different reasons, and saying the same
// thing about both is wrong in both directions:
//
//   "expected"  somebody just asked for an update, or handed the glass back by
//               turning wasm mode off. The board is restarting on purpose and
//               will come back, possibly on a new version. Nothing is at risk.
//   "lost"      it vanished -- unplugged, rebooted, off the WiFi. In BOARD
//               MODE this page keeps no store of its own BY DESIGN, so
//               anything the sweep had not yet shipped lives only in this tab
//               and a reload loses it.
//
// The warning belongs to "lost" alone, and is appended HERE rather than
// trusted to each caller: a caller that forgot it would under-warn on the one
// case that matters, and a surface that always carried it would cry wolf on
// every ordinary update -- which is the same mistake twice.
//
// AND EVEN WITHIN "lost" it is conditional (2026-08-30), on `risk`: whether
// this page is actually holding work the board never took. A reader who had
// changed nothing, watching a console they had just switched off themselves,
// was told "anything you changed in the last few seconds is only in this tab"
// -- which is alarming, useless, and false. The worker knows the answer
// (`outstanding`), so the surface asks rather than assumes. `risk` defaults to
// TRUE for any caller that does not say, because under-warning is the worse
// direction and window.__moyLinkLost has other callers.
//
// THE FIRST REASON WINS. An update that was asked for will be followed by
// exactly the silence a loss looks like, and re-reporting it as a loss thirty
// seconds later is the wolf again.
//
// This owns the SURFACE. The DETECTION -- counting failed posts until "gone"
// is a fact rather than a hiccup -- belongs with the pump that sees them, next
// to gpio_link's MAX_FAILS and the persist path's give-up, and it reaches this
// through window.__moyLinkLost or the worker's {t:"lost"} above.
var LNK=document.getElementById("lnk"),LNKH=document.getElementById("lnkh"),
LNKB=document.getElementById("lnkb"),linkGone=false;
function linkLost(kind,head,body,risk){
if(linkGone)return;
linkGone=true;
LNKH.textContent=head;
LNKB.textContent=(kind==="lost")
?((body?body+" ":"")+((risk===false)
?"Nothing of yours is waiting to be saved. Reopen the console's WEB CONSOLE "
+"screen to carry on."
:"This page keeps no copy of its own while a console is "
+"serving it, so anything you changed in the last few seconds is only in this "
+"tab -- do not reload until the console is back."))
:body;
LNK.className=(kind==="lost")?"bad":"";
LNK.style.display="block";
// Readable by the browser harness without scraping, the way __moyReport is.
window.__moyLink=kind;}
window.__moyLinkLost=linkLost;

// The loader module calls this on the play-button gesture (which also unlocks
// WebAudio), once the worker has booted and shipped its assets. It is the whole
// page's entry point: without it nothing starts and the canvas stays black.
// It lived at the TAIL of the update strip's block, after code it had nothing
// to do with, and went out with it (2026-08-29) -- a black screen everywhere,
// past a green suite.
window.__moyStart=function(){
getA().then(function(){sEl.textContent="live";sEl.style.color="#00e436";
if(WORKER)WORKER.postMessage({t:"run"});
podProbe();
requestAnimationFrame(tick);setInterval(plog,PERF_MS);cv.focus();})
.catch(function(e){console.error(e);sEl.textContent="no assets";sEl.style.color="#ff004d";});};
window.__moyRefetchAssets=function(){getA().catch(function(){});};
// The worker bound the update bridge: the board serving this page offers
// firmware updates, and the console's Settings row is live. Recorded rather
// than drawn -- the page owns no update UI now.
// `bound`/`services` come from the CONSOLE (web_boot.services_json), not from
// this message having been sent -- the sending only ever proved the board's
// probe answered, which is a different fact and was reported as this one.
function updBound(m){window.__moyUpdate={running:m.running,screen:!!m.screen,
bound:(m.bound===undefined?null:m.bound),services:m.services||null};}
// ---- loader: spawn the console worker, wire it to the page -------------------
// The VM used to boot HERE, on the main thread. It now lives in worker.js; this
// module only constructs it, forwards the query string (tier + cart) and owns the
// page-side splash and dev-reload polling.
const sEl2 = document.getElementById("s");
try {
  // Stamped by build.sh with worker.js's content hash (see below). Left as the
  // literal placeholder by a hand-assembled index.html, which still works --
  // any token makes the url differ from the bare "worker.js" a browser may have
  // cached -- it just stops changing between builds.
  const MOY_BUILD = "@MOY_BUILD@";
  // The worker url carries a CACHE BUSTER, and it is not superstition: browsers
  // cache worker scripts stubbornly, and a hard reload that refreshes the
  // document will happily keep an old worker.js. That is not hypothetical --
  // the board served a correct new console for an hour while a browser ran the
  // previous worker, and the tier decision lives in exactly that file. The
  // document is no-store from the board, so a reload always fetches this line
  // fresh, and a changed token makes the worker a different url the cache has
  // never seen.
  // A pin this browser already knows for this board goes onto the url FIRST, so
  // the worker's first carts.json carries it and a returning kid is never asked
  // again (see __moyPinRestore). A QR arrival already has one and is untouched.
  window.__moyPinRestore();
  const w = new Worker("worker.js?v=" + MOY_BUILD, { type: "module" });
  window.__moyAttach(w);
  w.postMessage({ t: "init", search: location.search });
  // ---- play-button splash ----------------------------------------------------
  // The p8-web-player pattern: the game starts on a CLICK, which is also the user
  // gesture that unlocks WebAudio, so sound works from frame one.
  // ?dev=1 (the CLI hot-reload loop) skips it -- devs restart constantly.
  if (new URLSearchParams(location.search).get("dev")) {
    window.__moyStart();
  } else {
    const ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;display:flex;align-items:center;" +
      "justify-content:center;background:rgba(11,15,26,.88);cursor:pointer;z-index:20";
    ov.innerHTML = "<div style='width:110px;height:110px;border-radius:50%;" +
      "background:#7e2553;border:4px solid #fff1e8;display:flex;align-items:center;" +
      "justify-content:center'><div style='width:0;height:0;margin-left:10px;" +
      "border-top:26px solid transparent;border-bottom:26px solid transparent;" +
      "border-left:42px solid #fff1e8'></div></div>";
    ov.addEventListener("click", () => { ov.remove(); window.__moyStart(); },
      { once: true });
    document.body.appendChild(ov);
  }
  // The tier link is a TOGGLE, not a one-way door: from the desk it offers the
  // small screen, from the small screen it offers the desk back. "?" alone is
  // the desktop, because an empty query is the default now.
  try {
    const a = document.getElementById("tier");
    if (a && new URLSearchParams(location.search).get("handheld")) {
      a.href = "?";
      a.textContent = "full desktop";
    }
  } catch (e) { /* the link is decoration; never break boot over it */ }
  // ---- dev hot-reload (?dev=1, the moy CLI's watch loop) ---------------------
  // Poll the CLI server's /stamp (latest cart-file mtime); on change the WORKER
  // refetches carts.json, restarts the cart and pushes fresh assets. Static
  // production hosting never has ?dev=1, so this costs nothing there.
  if (new URLSearchParams(location.search).get("dev")) {
    let stamp = null;
    setInterval(async () => {
      try {
        const s = await (await fetch("stamp")).text();
        if (stamp === null) { stamp = s; return; }
        if (s === stamp) return;
        stamp = s;
        w.postMessage({ t: "reload" });
        console.log("[moy] cart reload requested");
      } catch (e) { console.error("[moy] reload failed", e); }
    }, 400);
  }
} catch (e) {
  console.error(e);
  sEl2.textContent = "boot failed (see devtools)";
  sEl2.style.color = "#ff004d";
}
</script>
</body></html>
