
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
else if(m.t==="wperf"){console.log("[moy worker] "+m.s);}
else if(m.t==="error"){console.error("[moy]",m.s);
sEl.textContent="console crash (see devtools)";sEl.style.color="#ff004d";}}
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
// Hand the buffer back for reuse once it has been blitted.
if(fb&&WORKER)WORKER.postMessage({t:"fbret",b:fb},[fb]);}}
// The loader module calls this on the play-button gesture (which also unlocks
// WebAudio), once the worker has booted and shipped its assets.
window.__moyStart=function(){
getA().then(function(){sEl.textContent="live";sEl.style.color="#00e436";
if(WORKER)WORKER.postMessage({t:"run"});
requestAnimationFrame(tick);setInterval(plog,PERF_MS);cv.focus();})
.catch(function(e){console.error(e);sEl.textContent="no assets";sEl.style.color="#ff004d";});};
window.__moyRefetchAssets=function(){getA().catch(function(){});};
</script>
<script type=module>
// ---- loader: spawn the console worker, wire it to the page -------------------
// The VM used to boot HERE, on the main thread. It now lives in worker.js; this
// module only constructs it, forwards the query string (tier + cart) and owns the
// page-side splash and dev-reload polling.
const sEl2 = document.getElementById("s");
try {
  const w = new Worker("worker.js", { type: "module" });
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
