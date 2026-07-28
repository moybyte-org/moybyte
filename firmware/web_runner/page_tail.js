
// ---- web runner transport tail (#151) ----------------------------------------
// Replaces PAGE_LIVE_TAIL's HTTP+WebSocket with DIRECT calls into the
// MicroPython-WASM console (window.MOY, installed by the loader module below).
// Frames come from MOY.step(dt) as a JSON string ("" = redraw skipped, retain);
// input drains to MOY.events(JSON text) -- the same wire shapes the WS speaks.
function fetchAssets(){return Promise.resolve(JSON.parse(MOY.assets()));}
// ?pad=1 forces the touch controls on ANY device (desktop demos, touch laptops).
// Phones show them automatically -- the core's media query hides them only on
// hover-capable fine-pointer (i.e. mouse) devices; per-cart input hints still
// hide the pad for touch-only carts unless forced here.
if(new URLSearchParams(location.search).get("pad")){
document.getElementById("ctl").style.display="flex";}
var panWas=false;
function pump(){var v=pv();if(v[0]||v[1]){panWas=true;send({type:"pan",dx:v[0],dy:v[1]});}
else if(panWas){panWas=false;send({type:"pan",dx:0,dy:0});}
if(!q.length)return;var b=q;q=[];
try{MOY.events(JSON.stringify({events:b}));}catch(e){console.error("input error",e);}}
var lastTs=0;
function tick(ts){requestAnimationFrame(tick);
var dt=lastTs?(ts-lastTs)/1000:1/60;if(dt>0.1)dt=0.1;lastTs=ts;
pump();
var f="";
try{f=MOY.step(dt);}catch(e){sEl.textContent="console crash (see devtools)";sEl.style.color="#ff004d";throw e;}
if(f){PERF.f++;PERF.b+=f.length;if(f.length>PERF.pk)PERF.pk=f.length;HUD.kb=f.length/1024;
df(JSON.parse(f));}}
// The loader module calls this once the VM + console are up.
window.__moyStart=function(){
getA().then(function(){sEl.textContent="live";sEl.style.color="#00e436";
requestAnimationFrame(tick);setInterval(plog,PERF_MS);cv.focus();})
.catch(function(e){console.error(e);sEl.textContent="no assets";sEl.style.color="#ff004d";});};
// The dev hot-reload (moy CLI) refetches assets after a cart restart -- the cart
// TITLE is unchanged so df()'s change detection never would (stale sheet/images).
window.__moyRefetchAssets=function(){getA().catch(function(){});};
</script>
<script type=module>
// ---- loader: boot MicroPython-WASM, mount the console + carts, wire MOY ------
import {loadMicroPython} from "./micropython.mjs";
const sEl2 = document.getElementById("s");
function mkdirs(mp, p){let cur="";for(const part of p.split("/")){if(!part)continue;
cur+="/"+part;try{mp.FS.mkdir(cur);}catch(e){}}}
try {
  sEl2.textContent = "loading vm...";
  const mp = await loadMicroPython({heapsize: 16*1024*1024,
    stdout: (l)=>console.log("[moy]", l)});
  sEl2.textContent = "loading console...";
  // FROZEN-first: the ship build bakes the console into the wasm and ships no
  // modules.json. A --stage-only dev dist adds one; loading it into /modules
  // (first on sys.path) SHADOWS the frozen copies, so runtime/ edits run
  // without a wasm rebuild.
  const [mods, carts] = await Promise.all([
    fetch("modules.json").then((r)=>r.ok?r.json():null).catch(()=>null),
    fetch("carts.json").then(r=>r.json())]);
  let boot = "";
  if (mods) {
    mkdirs(mp, "/modules");
    for (const n in mods) mp.FS.writeFile("/modules/"+n, mods[n]);
    boot = "import sys\nsys.path.insert(0, '/modules')\n";
  }
  mkdirs(mp, "/moy/carts");
  for (const rel in carts) {
    const full = "/moy/carts/"+rel;
    mkdirs(mp, full.slice(0, full.lastIndexOf("/")));
    mp.FS.writeFile(full, carts[rel]);
  }
  sEl2.textContent = "booting console...";
  let cart = new URLSearchParams(location.search).get("cart");
  // Single-cart bundle (the --spec export): boot straight into the game, no
  // shelf stop -- the PICO-8-web-export behaviour.
  const names = [...new Set(Object.keys(carts).map((k)=>k.split("/")[0]))];
  if (!cart && names.length === 1) cart = names[0];
  mp.runPython(boot + "import web_boot\n"
    + "web_boot.boot('/moy/carts'" + (cart ? ", cart=" + JSON.stringify(cart) : "") + ")\n"
    + "from web_boot import assets_json, step_frame_json, apply_events_json");
  window.MOY = {
    assets: mp.globals.get("assets_json"),
    step: mp.globals.get("step_frame_json"),
    events: mp.globals.get("apply_events_json"),
  };
  window.__moyStart();
  // ---- dev hot-reload (?dev=1, the moy CLI's watch loop) ---------------------
  // Poll the CLI server's /stamp (latest cart-file mtime); on change, refetch
  // the live-packed carts.json, rewrite the files in the VFS, restart the cart
  // (web_boot.reload_cart) and refetch assets. Static production hosting never
  // has ?dev=1, so this costs nothing there.
  if (new URLSearchParams(location.search).get("dev")) {
    let stamp = null;
    setInterval(async () => {
      try {
        const s = await (await fetch("stamp")).text();
        if (stamp === null) { stamp = s; return; }
        if (s === stamp) return;
        stamp = s;
        const fresh = await (await fetch("carts.json")).json();
        for (const rel in fresh) {
          const full = "/moy/carts/" + rel;
          mkdirs(mp, full.slice(0, full.lastIndexOf("/")));
          mp.FS.writeFile(full, fresh[rel]);
        }
        mp.runPython("import web_boot as _wb; _wb.reload_cart()");
        window.__moyRefetchAssets();
        console.log("[moy] cart reloaded");
      } catch (e) { console.error("[moy] reload failed", e); }
    }, 400);
  }
} catch (e) {
  console.error(e);
  sEl2.textContent = "boot failed (see devtools)";
  sEl2.style.color = "#ff004d";
}
