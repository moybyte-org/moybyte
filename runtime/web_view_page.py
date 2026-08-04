"""Moybyte web view -- the browser page (PAGE_HTML): a self-contained <canvas> +
JS replayer for the payload-diet protocol (#41), extracted from web_view.py to shrink it.

ONE page for BOTH transports (the device raw-socket server + the host http.server).
web_view.py imports PAGE_HTML back and re-exports it, so `web_view.PAGE_HTML` is
unchanged for both. Pure data (one string) -- trivially device-freezable; canonical home
runtime/web_view_page.py, staged into the firmware modules/ tree by build.sh so the
device freezes it as top-level web_view_page.
"""


# ---------------------------------------------------------------------------
# The browser page: a self-contained replayer for the payload-diet protocol (#41). ONE page
# for BOTH transports. It fetches /assets (palette + font + sheet + tilemap) ONCE over HTTP,
# then opens a persistent WebSocket (/ws) for the live channel (frames PUSH down, input pushes
# up). The live channel is WebSocket-ONLY now -- BOTH the device (raw-socket) and the host
# (http.server) speak WS, so a closed socket just reconnects; there is no HTTP poll fallback.
# Replay is PIXEL-IDENTICAL to the panel:
#   defspr  -> cache the bitmap by index (ATL[index]).
#   spr     -> atlas form (by index) OR self-contained (full-pixel) -> blit with scale/flip.
#   map     -> walk the CACHED tilemap over the CACHED sheet (kept current by settiles).
#   imgref  -> blit a /assets-cached paint image (IMG[name]) by NAME (#63 Fold 4); img is the
#              inline-pixel fallback for a nameless paint image.
#   deflayer/blit_layer -> replay a layer's stream into an off-screen buffer once, then blit.
# On a cart change (a frame's cart != assCart) -> refetch /assets; ATL/LAY reset on `gen` change.
# A deflayer may carry an imgref whose IMG isn't loaded yet (a re-shipped layer racing the async
# /assets fetch); an imgref cache-MISS latches imgWant, and df() re-fetches /assets (which makes
# the server re-ship the deflayer) until the image is cached -- so a ship-once layer converges.
# ---------------------------------------------------------------------------

# The page is built from TWO pieces so a third transport can reuse the replayer
# (#151 web runner): PAGE_CORE = everything transport-agnostic (markup, replay,
# input capture, HUD, audio unlock) with ONE seam -- getA() sources its assets
# from a `fetchAssets()` the transport tail defines; PAGE_LIVE_TAIL = the
# HTTP + WebSocket live transport + the boot call. PAGE_HTML (both live
# transports) = PAGE_CORE + PAGE_LIVE_TAIL, unchanged for every consumer. The
# wasm runner builds PAGE_CORE + its own direct-call tail instead.
PAGE_CORE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Moybyte device</title><style>
html,body{margin:0;height:100%;background:#0b0f1a;color:#c2c3c7;
font:14px ui-monospace,Menlo,Consolas,monospace;display:flex;flex-direction:column;
align-items:center}
h1{font-size:14px;color:#fff1e8;margin:8px;max-width:96vw;white-space:nowrap;
overflow:hidden;text-overflow:ellipsis}#s{color:#ffec27}
/* Small phones (e.g. iPhone 13 mini): the full title used to wrap to two rows and
   steal canvas height -- drop the "device" suffix + the backtick hint (meaningless
   without a physical keyboard) on coarse pointers; nowrap+ellipsis backstops the rest. */
@media (pointer:coarse){h1 .dv,h1 small{display:none}}
/* Long-press guard: HOLDING a control (the hold-to-exit burger, a d-pad direction, A/B)
   used to start the OS text-selection -- iOS magnifier/blue highlight, Android context
   menu -- because plain user-select isn't enough there: iOS wants the -webkit- prefix +
   -webkit-touch-callout, and the page text (title/status) had no guard at all for a hold
   that drifts off a button. Touch devices only; desktop keeps normal text selection.
   #kbin stays selectable -- its caret/selection is driven programmatically (setSelectionRange)
   and must not inherit the guard. */
@media (pointer:coarse){body{-webkit-user-select:none;user-select:none;
-webkit-touch-callout:none}
#kbin{-webkit-user-select:text;user-select:text}}
/* Presentation scale: the OS renders at its DESIGN resolution (320x240 /
   1024x600); fit() uses the whole available viewport while the browser keeps
   nearest-neighbour sampling. Desktop users have real keyboard/pointer input,
   so the touch controls do not consume a permanent 120px-tall band there. */
canvas{image-rendering:pixelated;background:#000;border:1px solid #1d2b53;border-radius:6px;
width:min(96vw,112vh);height:auto;max-width:100%;touch-action:none;cursor:crosshair}
/* Control sizes scale with the viewport (min(px, vw)): full desktop-tablet size when the
   width allows, proportionally smaller on narrow phones -- adding the ⌨ button made the
   fixed-px row overflow a 375px iPhone 13 mini, and flex "resolved" it by squashing the
   joystick half off-screen and overlapping the circles. flex-shrink is 0 everywhere so the
   row can never squash; the sizes are tuned to fit ~360px at their vw floors. */
#ctl{display:flex;justify-content:space-between;align-items:center;gap:min(8px,1vw);
width:min(96vw,112vh);
max-width:100%;padding:12px 4px;box-sizing:border-box;touch-action:none;user-select:none;
--bb:min(72px,15vw);--sb:min(40px,9vw);--joy:min(120px,26vw)}
#joy{flex:0 0 auto;position:relative;width:var(--joy);height:var(--joy);border-radius:50%;
background:#1d2b53;
border:2px solid #29366f}#th{position:absolute;top:50%;left:50%;
width:calc(var(--joy)*0.43);height:calc(var(--joy)*0.43);
margin:calc(var(--joy)*-0.215) 0 0 calc(var(--joy)*-0.215);
border-radius:50%;background:#5f6f9f;border:2px solid #c2c3c7;
pointer-events:none}.b{flex:0 0 auto;width:var(--bb);height:var(--bb);border-radius:50%;
display:flex;
align-items:center;justify-content:center;font:700 min(26px,5.5vw) ui-monospace;
color:#fff1e8;
background:#7e2553;border:2px solid #c2c3c7;margin-left:min(18px,2vw)}#bb{background:#29366f}
#bh,#kb{background:#5f574f;width:var(--sb);height:var(--sb);font-size:min(16px,4vw)}
.pr{background:#ffec27;color:#1d2b53}
/* The middle cluster (#42 Thread 2 rework): the soft-keyboard summon lives in the bottom
   control bar next to the burger, NEVER floating over the canvas -- the old pinned corner
   toggle sat exactly on the console's OS status zone / context-X and ate its taps. #mid is
   always shown on touch (even for touch-only carts, so HOME/exit stays reachable); only the
   joystick + A/B gate on the cart's input hint, and only they hide while typing. */
#mid{flex:1 1 0;min-width:0;display:flex;justify-content:center;align-items:center}
#mid .b{margin:0 min(9px,1.5vw)}
#ab{flex:0 0 auto;display:flex;align-items:center}
/* #kbin is the real input that gets focused -- pinned 1x1px at the viewport origin so it
   never occludes the canvas or pushes the page around; font-size 16px so iOS doesn't zoom
   the page in on focus. */
#kbin{position:fixed;top:0;left:0;width:1px;height:1px;padding:0;margin:0;border:0;
opacity:0;font-size:16px;background:transparent;color:transparent;caret-color:transparent}
@media (hover:hover) and (pointer:fine){#ctl{display:none}}
/* Debug HUD (#41): toggled with the ` key; lightweight live stream stats. */
#hud{position:fixed;top:6px;left:6px;z-index:9;display:none;padding:6px 8px;border-radius:5px;
background:rgba(11,15,26,.82);border:1px solid #1d2b53;color:#00e436;font:12px ui-monospace;
white-space:pre;pointer-events:none}#hud b{color:#ffec27}#hud .w{color:#ff004d}</style></head><body>
<div id=hud></div>
<h1>Moybyte<span class=dv> &mdash; device</span> <span id=s>connecting...</span> <small style="color:#5f6f9f">(press ` for stats)</small></h1>
<canvas id=cv width=320 height=240 tabindex=0></canvas>
<input id=kbin type=text autocapitalize=off autocomplete=off autocorrect=off spellcheck=false>
<div id=ctl><div id=joy><div id=th></div></div>
<div id=mid><span class=b id=kb title="keyboard">&#9000;</span><span class=b id=bh>&#9776;</span></div>
<div id=ab><span class=b id=bb>B</span><span class=b id=ba>A</span></div></div>
<script>
var FPS=30,cv=document.getElementById("cv"),cx=cv.getContext("2d"),sEl=document.getElementById("s");
cx.imageSmoothingEnabled=false;
var W=320,H=240,PAL=null,FONT=null,ready=false,assCart=undefined,idx=null,img=null,rgba=null;
// Payload-diet caches (#41): SHEET = the cart sprite sheet; TM = the cart tilemap (kept
// current by settiles); ATL = the per-session sprite atlas filled by defspr.
var SHEET=null,TM=null,ATL=[],curGen=-1;
// #42 Thread 3: the open cart's manifest input hint (null/"buttons"/"touch"/"keyboard" list;
// null = undeclared -> show every control, today's behaviour). applyInputHint() gates the
// virtual gamepad (#ctl) on "buttons" and the soft-keyboard summon (#kb) on "keyboard".
var INPUT=null,padWanted=true,kbWanted=true;
// #76 per-surface DELTA: the last full command list per WM-surface id. A frame entry
// {"same":1} replays from here instead of re-shipping the commands; wiped with ATL/LAY
// on a gen change (the server's SurfaceDelta resets in lock-step on connect/reset).
var SURF={};
// Paint-image cache (#63 Fold 4): IMG[name] = {w,h,px:Uint8Array of raw MOY64 indices},
// loaded from /assets (once per cart) so an ["imgref",x,y,name] blits without carrying pixels.
// imgWant latches an imgref cache MISS (a deflayer racing the async /assets fetch); assLoading
// guards a single in-flight /assets fetch so the miss-retry (df) doesn't spam the server.
var IMG={},imgWant=false,assLoading=false;
var HUD={on:false,fps:0,kb:0,unknown:0,el:document.getElementById("hud"),last:0};
// Periodic perf LOG (#41): recv + render fps, bandwidth, avg/peak payload, AND the device's
// push-rate + free heap (from f.perf), one console.log line every PERF_MS. The recv/dev/bw
// figures are 2s MEANS -- which hide a stutter (a lone slow frame averages away), so we also
// fold the device's per-frame instants (gap/draw/js/tx) into a window MAX + count throttled
// frames: mg=worst inter-frame gap ms (THE stutter number), md=worst device draw+commit ms,
// mj/mt=worst json-encode/socket-send ms, thr=# of bandwidth-throttled pushes this window.
var PERF_MS=2000,PERF={f:0,b:0,pk:0,t:0,dh:0,pf:0,lpf:0,js:0,tx:0,mg:0,md:0,mj:0,mt:0,thr:0,
// page-side halves (surface model Phase B): m* = worst, s*/sn = mean.
// mr/sr replay, mb/sb index->RGBA, mu/su putImageData upload.
mr:0,sr:0,mb:0,sb:0,mu:0,su:0,sn:0};
function PT(){return (window.performance&&performance.now)?performance.now():Date.now();}
function plog(){var now=(window.performance&&performance.now)?performance.now():Date.now();
if(!PERF.t){PERF.t=now;PERF.lpf=PERF.pf;return;}var dt=(now-PERF.t)/1000;if(dt<=0)return;
console.log("[moybyte] "+(assCart||"?")+" | recv "+(PERF.f/dt).toFixed(1)+" render "+HUD.fps.toFixed(1)
+" dev "+((PERF.pf-PERF.lpf)/dt).toFixed(1)+" fps | worst gap "+PERF.mg+" draw "+PERF.md+" js "+PERF.mj
+" tx "+PERF.mt+" ms | PAGE replay "+(PERF.sn?PERF.sr/PERF.sn:0).toFixed(2)+"/"+PERF.mr.toFixed(1)
+" rgba "+(PERF.sn?PERF.sb/PERF.sn:0).toFixed(2)+"/"+PERF.mb.toFixed(1)
+" upload "+(PERF.sn?PERF.su/PERF.sn:0).toFixed(2)+"/"+PERF.mu.toFixed(1)+" ms mean/worst"
+" | thr "+PERF.thr+" | bw "+(PERF.b/dt/1024).toFixed(1)+" KB/s avg "
+(PERF.f?(PERF.b/PERF.f/1024):0).toFixed(2)+" peak "+(PERF.pk/1024).toFixed(2)
+" KB | heap "+PERF.dh+" KB | unknown "+HUD.unknown);
PERF.f=0;PERF.b=0;PERF.pk=0;PERF.t=now;PERF.lpf=PERF.pf;PERF.mg=0;PERF.md=0;PERF.mj=0;PERF.mt=0;PERF.thr=0;
PERF.mr=0;PERF.sr=0;PERF.mb=0;PERF.sb=0;PERF.mu=0;PERF.su=0;PERF.sn=0;}
// Audio (host web console + wasm runner): play the server's FINISHED PCM (no
// JS synth). The device streams no audio (f.audio ""), so this is a no-op there.
// Playback is ONE AudioWorklet pulling from a sample ring with CONTINUOUS
// linear resampling (#170 round 4): the old per-chunk AudioBufferSource path
// resampled every ~184-sample chunk independently and rounded each start() to
// the context's sample grid -- a seam (click) at every chunk boundary, ~60/s
// (owner: "still some clicking" after the cushion fix). The ring has no seams;
// starvation DECAYS the last sample to zero instead of hard-cutting. The chunk
// scheduler survives as the fallback (no AudioWorklet) and as the bridge until
// the worklet module finishes loading -- the swap happens only at a stream gap,
// because the two paths' clocks aren't aligned and a mid-stream swap would
// itself click.
var AUDIO_RATE=11025,actx=null,audioNext=0,audioBlocked=false;
var awNode=null,awReady=false,awOn=false,awDepth=0;
var AW_SRC="class MoyPCM extends AudioWorkletProcessor{"+
"constructor(){super();this.b=new Float32Array(1<<16);this.r=0;this.w=0;this.n=0;"+
"this.pos=0;this.rate=11025;this.last=0;this.k=0;"+
"this.port.onmessage=(e)=>{var d=e.data;if(d.rate){this.rate=d.rate;return;}"+
"var a=d.p;for(var i=0;i<a.length;i++){if(this.n>=this.b.length)break;"+
"this.b[this.w]=a[i];this.w=(this.w+1)%this.b.length;this.n++;}};}"+
"process(ins,outs){var o=outs[0][0],st=this.rate/sampleRate;"+
"for(var i=0;i<o.length;i++){"+
"if(this.n>1){var v0=this.b[this.r],v1=this.b[(this.r+1)%this.b.length];"+
"o[i]=v0+(v1-v0)*this.pos;this.last=o[i];this.pos+=st;"+
"while(this.pos>=1&&this.n>1){this.pos-=1;this.r=(this.r+1)%this.b.length;this.n--;}}"+
"else{this.last*=0.995;o[i]=this.last;}}"+
"if(++this.k>=8){this.k=0;this.port.postMessage(this.n);}return true;}}"+
"registerProcessor('moy-pcm',MoyPCM);";
function ensureAudio(){if(!actx){var AC=window.AudioContext||window.webkitAudioContext;
if(AC){try{actx=new AC();}catch(e){actx=null;}}
if(actx&&actx.audioWorklet&&window.URL&&window.Blob){
try{var u=URL.createObjectURL(new Blob([AW_SRC],{type:"application/javascript"}));
actx.audioWorklet.addModule(u).then(function(){
awNode=new AudioWorkletNode(actx,"moy-pcm",{numberOfInputs:0,outputChannelCount:[1]});
awNode.port.onmessage=function(e){awDepth=e.data;};
awNode.port.postMessage({rate:AUDIO_RATE});
awNode.connect(actx.destination);awReady=true;}).catch(function(){});}catch(e){}}}
if(actx&&actx.state==="suspended")actx.resume();}
// Seconds of PCM still queued for playback -- the runner reports this to the
// console each frame so the synth tops the cushion back up (the crackle fix).
// Between the worklet's periodic depth reports playPCM adds pushes optimistically.
function audioQueuedSecs(){if(!actx||actx.state!=="running")return -1;
if(awOn)return awDepth/AUDIO_RATE;
return Math.max(0,audioNext-actx.currentTime);}
// Audio frames that arrive while the context is blocked by the browser's autoplay
// policy used to be dropped SILENTLY -- undiagnosable on a phone. Surface the state
// in the status chip instead, and self-heal once a gesture unlocks the context.
function playPCM(b64){if(!b64)return;
if(!actx||actx.state!=="running"){if(!audioBlocked){audioBlocked=true;
sEl.textContent="tap screen to enable sound";sEl.style.color="#ffa300";}return;}
if(audioBlocked){audioBlocked=false;ok=false;}
var bin=atob(b64),n=bin.length>>1;if(n<=0)return;
var f=new Float32Array(n);
for(var i=0;i<n;i++){var v=bin.charCodeAt(i*2)|(bin.charCodeAt(i*2+1)<<8);if(v>=32768)v-=65536;f[i]=v/32768;}
if(awReady&&!awOn&&audioNext<=actx.currentTime)awOn=true;   // swap at a gap
if(awOn){awDepth+=n;awNode.port.postMessage({p:f},[f.buffer]);return;}
var buf=actx.createBuffer(1,n,AUDIO_RATE);buf.getChannelData(0).set(f);
var src=actx.createBufferSource();src.buffer=buf;src.connect(actx.destination);
var t=Math.max(actx.currentTime+0.02,audioNext);src.start(t);audioNext=t+buf.duration;}
function fit(){/* Fill the available viewport without the old integer-scale
cliff (e.g. 1.95x used to collapse all the way to 1x on an ultrawide). Sized off
the VISUAL viewport when the browser has one: iOS collapses it for the soft
keyboard WITHOUT firing window.resize on close, which is how the canvas used to
stick tiny after typing + PLAY (owner report 2026-07-23). */
var hh=document.querySelector("h1"),ct=document.getElementById("ctl");
var vv=window.visualViewport;
var iw=vv?vv.width:window.innerWidth,ih=vv?vv.height:window.innerHeight;
var rw=Math.max(64,iw-16);
var rh=Math.max(64,ih-((hh?hh.offsetHeight:0)+(ct?ct.offsetHeight:0)+28));
// While the soft keyboard is up (#42) the viewport HEIGHT collapses (and the
// gamepad block still counts against it), which used to shrink the canvas to a
// stamp -- so size by width alone then; a phone is width-limited anyway.
var ae=document.activeElement;if(ae&&ae.id==="kbin")rh=1e9;
var s=Math.min(rw/W,rh/H);
cv.style.width=Math.round(W*s)+"px";cv.style.height=Math.round(H*s)+"px";}
window.addEventListener("resize",fit);
// The visual viewport is what actually changes when the soft keyboard opens/
// closes -- listen permanently (kbScroll's focus-scoped listener is separate).
if(window.visualViewport)window.visualViewport.addEventListener("resize",fit);
function alloc(){/* Blank the retained buffer ONLY on a real size change: getA()
re-runs per cover cache-miss (one per built shelf thumbnail), and zeroing idx
each time blacked the screen until the next push (owner 2026-07-23). */
var fresh=cv.width!==W||cv.height!==H||!idx;
if(fresh){cv.width=W;cv.height=H;cx=cv.getContext("2d");cx.imageSmoothingEnabled=false;
idx=new Uint8Array(W*H);img=cx.createImageData(W,H);rgba=img.data;rs();}
fit();}
function getA(){assLoading=true;return fetchAssets().then(function(a){
W=a.w;H=a.h;PAL=a.palette;FONT=a.font;assCart=a.cart;SHEET=a.sheet||null;
if(a.audio_rate){AUDIO_RATE=a.audio_rate;if(awNode)awNode.port.postMessage({rate:AUDIO_RATE});}
INPUT=a.input||null;applyInputHint();
TM=a.tilemap?{w:a.tilemap.w,h:a.tilemap.h,cells:a.tilemap.cells.slice()}:null;
// Decode each paint image's base64 raw indices into a Uint8Array ONCE (#63 Fold 4), so an
// imgref just blits the cached bytes (index->palette). Keyed by the SAME name image('name') tags.
// INCREMENTAL (a.partial): the serialiser may ship ONLY images this client lacks --
// re-serialising the whole set cost 360-560ms per request in the wasm worker (json.dumps
// over ~644KB), which starved the frame loop to 1-7fps whenever a lazily-built cover kept
// imgWant latched. A partial payload MERGES; a full one replaces (cart change / reload).
if(!a.partial)IMG={};
if(a.images){for(var nm in a.images){var gi=a.images[nm],bs=atob(gi.b64),bn=bs.length,bp=new Uint8Array(bn);
for(var bk=0;bk<bn;bk++)bp[bk]=bs.charCodeAt(bk);IMG[nm]={w:gi.w,h:gi.h,px:bp};}}
assLoading=false;alloc();ready=true;}).catch(function(e){assLoading=false;throw e;});}
// NB: do NOT clear ATL here -- it resets on `gen` change (see df). imgWant is cleared by df's retry.
var caX=0,caY=0,cl0=0,cm0=0,cl1=W,cm1=H,pm=null,pt=null;
// WM-OWNED VIEW (#175). A ["view",ox,oy,scale,w,h] bracket places a cart's whole
// draw span inside a window rect, so the cart's COMMANDS ship (~16.7 KB/f) instead
// of a rasterized full-frame image (~102 KB/f, and ~85 ms/f of pure-Python pixel
// work in the wasm). It is deliberately OUTSIDE cart draw state: the cart's own
// camera/clip compose INSIDE it and reset_state cannot clear it -- which is why
// this is a separate op rather than the WM borrowing `camera`/`clip` (a cart that
// called either would otherwise clobber its own placement and bleed onto the desk).
// vBX/vBY are the camera BASE the view installs; vL/vT/vR/vB its clip bounds;
// vCW/vCH the cart's logical surface (what cls() covers). scale>1 is NOT applied
// yet -- see the "view" op in rep().
// vOX/vOY = target origin, vS = integer scale, vCW/vCH = the cart's logical surface
// (what cls() covers). A cart coord maps to target ((c - cam) * vS + vO).
var vOX=0,vOY=0,vS=1,vCW=W,vCH=H,vOn=false;
// With NO view, the bounds are the CURRENT canvas -- never W/H captured at script
// load. W/H change after load (getA() sets them from assets, dfl() per layer), and
// caching identity bounds pinned every draw into the top-left 320x240 of a 1024x600
// desktop. Derive them live instead.
function vclip(){if(vOn){cl0=Math.max(0,vOX);cm0=Math.max(0,vOY);
cl1=Math.min(W,vOX+vCW*vS);cm1=Math.min(H,vOY+vCH*vS);}else{cl0=0;cm0=0;cl1=W;cm1=H;}}
// Raw TARGET-space filled rect (already transformed + scaled): the one place that
// clips and writes. put/fr both funnel here so scale costs nothing when vS is 1.
function fr0(x,y,w,h,c){var a=Math.max(cl0,x),b=Math.max(cm0,y),
e=Math.min(cl1,x+w),f=Math.min(cm1,y+h);if(e<=a||f<=b)return;var ci=pm[c&63];
for(var yy=b;yy<f;yy++){var bs=yy*W;for(var xx=a;xx<e;xx++)idx[bs+xx]=ci;}}
function rs(){caX=0;caY=0;vclip();pm=new Uint8Array(64);pt=new Uint8Array(64);
for(var i=0;i<64;i++)pm[i]=i;}rs();
function put(x,y,c){var X=(((x|0)-caX)*vS+vOX)|0,Y=(((y|0)-caY)*vS+vOY)|0;
if(vS>1){fr0(X,Y,vS,vS,c);return;}                 // a cart pixel is an SxS block
if(X<cl0||X>=cl1||Y<cm0||Y>=cm1)return;idx[Y*W+X]=pm[c&63];}
function fr(x,y,w,h,c){fr0((((x|0)-caX)*vS+vOX)|0,(((y|0)-caY)*vS+vOY)|0,
(w|0)*vS,(h|0)*vS,c);}
function rb(x,y,w,h,c){fr(x,y,w,1,c);fr(x,y+h-1,w,1,c);fr(x,y,1,h,c);fr(x+w-1,y,1,h,c);}
function ln(x0,y0,x1,y1,c){x0|=0;y0|=0;x1|=0;y1|=0;var dx=Math.abs(x1-x0),dy=-Math.abs(y1-y0),
sx=x0<x1?1:-1,sy=y0<y1?1:-1,er=dx+dy,e2;while(true){put(x0,y0,c);if(x0==x1&&y0==y1)break;
e2=2*er;if(e2>=dy){er+=dy;x0+=sx;}if(e2<=dx){er+=dx;y0+=sy;}}}
function ci(cxx,cyy,r,c){cxx|=0;cyy|=0;r|=0;for(var dy=-r;dy<=r;dy++){var sp=Math.floor(Math.sqrt(r*r-dy*dy));
fr(cxx-sp,cyy+dy,2*sp+1,1,c);}}
function cb(cxx,cyy,r,c){cxx|=0;cyy|=0;r|=0;var x=r,y=0,er=0;while(x>=y){
var p=[[x,y],[y,x],[-y,x],[-x,y],[-x,-y],[-y,-x],[y,-x],[x,-y]];
for(var i=0;i<8;i++)put(cxx+p[i][0],cyy+p[i][1],c);y++;if(er<=0){er+=2*y+1;}else{x--;er-=2*x+1;}}}
// Blit a bitmap (raw pixels px, sw x sh, transparent index t) at x,y with scale+flip. Mirrors
// DeviceCanvas.spr / Canvas.spr exactly. Used by spr (atlas + self-contained) and map.
function blt(px,sw,sh,t,x,y,sc,fl){x|=0;y|=0;sc|=0;fl|=0;var fx=fl&1,fy=(fl>>1)&1;
for(var yy=0;yy<sh;yy++){var ry=fy?sh-1-yy:yy,bs=ry*sw;for(var xx=0;xx<sw;xx++){var rx=fx?sw-1-xx:xx,
p=px[bs+rx];if(p===t||p<0||pt[p&63])continue;if(sc<=1)put(x+xx,y+yy,p);else fr(x+xx*sc,y+yy*sc,sc,sc,p);}}}
// spr by atlas index. Unknown index = no-op (a missing defspr is the dropped-frame bug).
function sp(ix,x,y,sc,fl){var a=ATL[ix];if(!a){HUD.unknown++;return;}blt(a.px,a.w,a.h,a.t,x,y,sc,fl);}
// img (#63 Fold 3): a paint image (a big MOY64 index bitmap) as base64 of its RAW indices --
// the INLINE FALLBACK for a nameless paint image. atob -> write indices OPAQUE (index>=64
// skipped) into the CURRENT target (idx/W/H), the browser twin of blit_indices. Honors
// CAMERA + CLIP like every other draw op (the raster Canvas.spr clips a paint image the
// same way -- an unclipped im was how shelf edge-card covers bled outside the Library
// panel on the web while the host/device clipped them; clip bounds are always inside the
// canvas, so they subsume the old raw bounds checks).
function im(x,y,w,h,b64,sc){var s=atob(b64);sc=((sc|0)||1)*vS;
x=(((x|0)-caX)*vS+vOX)|0;y=(((y|0)-caY)*vS+vOY)|0;w|=0;h|=0;
if(sc==1){for(var yy=0;yy<h;yy++){var ty=y+yy;if(ty<cm0||ty>=cm1)continue;var sr=yy*w,dr=ty*W;
for(var xx=0;xx<w;xx++){var tx=x+xx;if(tx<cl0||tx>=cl1)continue;var p=s.charCodeAt(sr+xx);if(p<64)idx[dr+tx]=p;}}return;}
// scaled (the full-frame game/wallpaper composite, ["img",...,b64,scale]): each source
// pixel paints an sc x sc block, clipped -- the browser twin of the scaled spr blit.
for(var yy=0;yy<h;yy++){var sr=yy*w;for(var r=0;r<sc;r++){var ty=y+yy*sc+r;if(ty<cm0||ty>=cm1)continue;var dr=ty*W;
for(var xx=0;xx<w;xx++){var p=s.charCodeAt(sr+xx);if(p>=64)continue;var t0=x+xx*sc;
for(var q=0;q<sc;q++){var tx=t0+q;if(tx>=cl0&&tx<cl1)idx[dr+tx]=p;}}}}}
// imgref (#63 Fold 4): a paint image by NAME, blitted from the /assets IMG cache -- the normal
// path (pixels shipped once, not per-frame). Same opaque index->target blit as im (camera +
// clip honored), but reading the pre-decoded Uint8Array. A cache MISS latches imgWant so df()
// re-fetches /assets (the layer deflayer re-ships once assets arrive) -- a ship-once layer can
// otherwise strand its background.
function imr(x,y,nm,sc){var G=IMG[nm];if(!G){imgWant=true;return;}var s=G.px,w=G.w,h=G.h;
sc=((sc|0)||1)*vS;x=(((x|0)-caX)*vS+vOX)|0;y=(((y|0)-caY)*vS+vOY)|0;
if(sc==1){for(var yy=0;yy<h;yy++){var ty=y+yy;if(ty<cm0||ty>=cm1)continue;var sr=yy*w,dr=ty*W;
for(var xx=0;xx<w;xx++){var tx=x+xx;if(tx<cl0||tx>=cl1)continue;var p=s[sr+xx];if(p<64)idx[dr+tx]=p;}}return;}
// scaled imgref (#113: the ship-once wallpaper backdrop composite) -- sc x sc blocks, clipped.
for(var yy=0;yy<h;yy++){var sr=yy*w;for(var r=0;r<sc;r++){var ty=y+yy*sc+r;if(ty<cm0||ty>=cm1)continue;var dr=ty*W;
for(var xx=0;xx<w;xx++){var p=s[sr+xx];if(p>=64)continue;var t0=x+xx*sc;
for(var q=0;q<sc;q++){var tx=t0+q;if(tx>=cl0&&tx<cl1)idx[dr+tx]=p;}}}}}
// map(): walk the cached tilemap region over the cached sheet (step=tile*scale, colorkey
// transparent), mirroring the device map() cell layout.
function mp(mx,my,w,h,sx,sy,sc,ck){if(!SHEET||!TM)return;sc=sc<1?1:sc;
var tile=SHEET.tile,step=tile*sc,cols=SHEET.cols,sw=SHEET.w,spx=SHEET.pix,tw=TM.w,th=TM.h,cells=TM.cells;
for(var cy=0;cy<h;cy++){var ty=my+cy;for(var cx=0;cx<w;cx++){var gx=mx+cx;
var tid=(gx>=0&&gx<tw&&ty>=0&&ty<th)?cells[ty*tw+gx]-1:-1;if(tid<0)continue;
var ox=(tid%cols)*tile,oy=((tid/cols)|0)*tile,dx=sx+cx*step,dy=sy+cy*step;
for(var ly=0;ly<tile;ly++){var srow=(oy+ly)*sw+ox;for(var lx=0;lx<tile;lx++){var p=spx[srow+lx];
if(p===ck||p<0||pt[p&63])continue;if(sc<=1)put(dx+lx,dy+ly,p);else fr(dx+lx*sc,dy+ly*sc,sc,sc,p);}}}}}
// settiles: overwrite the cached tilemap (a cart mutated it via mset).
function st(w,h,cells){TM={w:w,h:h,cells:cells};}
// scr (#113 scroll-as-blit): shift the pixels inside rect (rx,ry,rw,rh) of the RETAINED
// index buffer by (dx,dy) in place -- the browser twin of Canvas.scroll_rect (exposed
// strips keep stale content; the stream's band repaint covers them). copyWithin is
// memmove-semantics (horizontal overlap safe); vertical order follows dy.
function scr(rx,ry,rw,rh,dx,dy){dx|=0;dy|=0;if(!dx&&!dy)return;
var x0=Math.max(0,rx|0),y0=Math.max(0,ry|0),x1=Math.min(W,(rx|0)+(rw|0)),y1=Math.min(H,(ry|0)+(rh|0));
var tx0=x0+(dx>0?dx:0),tx1=x1+(dx<0?dx:0),ty0=y0+(dy>0?dy:0),ty1=y1+(dy<0?dy:0);
if(tx0>=tx1||ty0>=ty1)return;var cw=tx1-tx0;
if(dy>0){for(var ty=ty1-1;ty>=ty0;ty--){var s0=(ty-dy)*W+(tx0-dx);idx.copyWithin(ty*W+tx0,s0,s0+cw);}}
else{for(var ty2=ty0;ty2<ty1;ty2++){var s1=(ty2-dy)*W+(tx0-dx);idx.copyWithin(ty2*W+tx0,s1,s1+cw);}}}
// OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar): deflayer (re)builds an off-screen
// index buffer by REPLAYING the layer's recorded stream (reusing rep()); blit_layer copies a
// window (draw_layer) or the full layer (blit_strip) into idx. LAY keeps each built buffer.
var LAY={};
function dfl(id,lw,lh,cmds){var sI=idx,sW=W,sH=H,sX=caX,sY=caY,s0=cl0,s1=cm0,s2=cl1,s3=cm1,sm=pm,spt=pt;
// A layer renders in its OWN space, so the WM view must not leak into it (and must
// survive it) -- saved/restored alongside camera+clip, identity while inside.
var q0=vOX,q1=vOY,q2=vS,q3=vCW,q4=vCH,q5=vOn;
vOX=0;vOY=0;vS=1;vCW=lw;vCH=lh;vOn=false;
var buf=new Uint8Array(lw*lh);idx=buf;W=lw;H=lh;rs();rep(cmds);
vOX=q0;vOY=q1;vS=q2;vCW=q3;vCH=q4;vOn=q5;
idx=sI;W=sW;H=sH;caX=sX;caY=sY;cl0=s0;cm0=s1;cl1=s2;cm1=s3;pm=sm;pt=spt;LAY[id]={w:lw,h:lh,buf:buf};}
// LAYER BLITS MUST HONOR THE WM VIEW (#175). These copy already-palette-mapped
// layer pixels, so they never re-apply pm -- but they DO have to place, scale and
// clip like every other primitive. They did not: both wrote straight into idx
// against the full canvas W/H, so a windowed cart's draw_layer painted its layer
// across the WHOLE desktop at 1:1 (owner screenshot 2026-07-31: sakura / Sky Run /
// Hop Quest / Letter Blitz smeared over the desk -- precisely the carts that use
// make_layer; cls-only carts were unaffected).
// Raw (already-mapped) block fill, target space -- the scaled counterpart of fr0.
function fr0r(x,y,w,h,ci){var a=Math.max(cl0,x),b=Math.max(cm0,y),
e=Math.min(cl1,x+w),f=Math.min(cm1,y+h);if(e<=a||f<=b)return;
for(var yy=b;yy<f;yy++){var bs=yy*W;for(var xx=a;xx<e;xx++)idx[bs+xx]=ci;}}
// Copy a w x h rect of `src` (row stride sw, origin sx,sy) to CART coords (cxo,cyo).
function blsrc(src,sw,sx,sy,cxo,cyo,w,h){
if(!vOn&&vS===1){                       // identity: the original tight loops
for(var r=0;r<h;r++){var ty=cyo+r;if(ty<cm0||ty>=cm1)continue;var d0=ty*W,o0=(sy+r)*sw+sx;
for(var x=0;x<w;x++){var tx=cxo+x;if(tx<cl0||tx>=cl1)continue;idx[d0+tx]=src[o0+x];}}return;}
for(var r=0;r<h;r++){var Y=((cyo+r)*vS+vOY)|0,o0=(sy+r)*sw+sx;
for(var x=0;x<w;x++){var X=((cxo+x)*vS+vOX)|0,p=src[o0+x];
if(vS>1){fr0r(X,Y,vS,vS,p);continue;}
if(X<cl0||X>=cl1||Y<cm0||Y>=cm1)continue;idx[Y*W+X]=p;}}}
// draw_layer's window copy: a viewport-sized window of the layer at the CART origin.
// The viewport is the cart's logical surface under a view (vCW/vCH), the canvas otherwise.
function blw(L,cx,cy){cx=cx<0?0:cx|0;cy=cy<0?0:cy|0;var sw=L.w,src=L.buf;
var dw=vOn?vCW:W,dh=vOn?vCH:H;
if(sw<=0||dw<=0||dh<=0)return;if(cx+dw>sw)dw=sw-cx;if(dw<=0)return;var sr=(src.length/sw)|0;
if(cy+dh>sr)dh=sr-cy;if(dh<=0)return;blsrc(src,sw,cx,cy,0,0,dw,dh);}
// blit_strip: the WHOLE layer at cart position (dx,dy). Source-side clipping keeps
// the copy inside the cart surface; blsrc clips the rest in target space.
function blf(L,dx,dy){dx|=0;dy|=0;var sw=L.w,sh=L.h,src=L.buf;
var vw=vOn?vCW:W,vh=vOn?vCH:H;if(sw<=0||vw<=0||vh<=0)return;
var sx=0,sy=0,w=sw,h=sh;
if(dx<0){sx=-dx;w+=dx;dx=0;}if(dy<0){sy=-dy;h+=dy;dy=0;}
if(dx+w>vw)w=vw-dx;if(dy+h>vh)h=vh-dy;if(w<=0||h<=0)return;
blsrc(src,sw,sx,sy,dx,dy,w,h);}
function bl(c){var L=LAY[c[1]];if(!L)return;if(c.length>4&&c[4]==="full")blf(L,c[2],c[3]);else blw(L,c[2],c[3]);}
// print walks BYTES, one 8px cell each (moy SPEC.md 6). Text arrives as a plain
// string while it is ASCII -- where a char IS its byte -- and as an ARRAY of byte
// values when it is not: JSON cannot carry a byte like 0xFF inside a string, and
// charCodeAt over decoded text would yield a CODEPOINT, spending one cell where
// the device spends two. tb() collapses the two forms; .length is right for both.
function tb(s,k){return typeof s==="string"?s.charCodeAt(k):s[k];}
function tx(s,x,y,c,sc){if(!FONT)return;var X=x|0;y|=0;sc=(sc|0)||1;
var fi=FONT.first,gw=FONT.w,g=FONT.glyphs,n=g.length;
if(sc==1){for(var k=0;k<s.length;k++){var gi=tb(s,k)-fi,co=(gi>=0&&gi<n)?g[gi]:g[0];
for(var j=0;j<gw;j++){var bt=co[j],py=y;while(bt){if(bt&1)put(X+j,py,c);bt>>=1;py++;}}X+=gw;}return;}
// scaled system text (#39): each glyph bit paints an sc x sc block (fr respects clip/camera,
// like the host SystemCanvas.print's rect blocks).
for(var k=0;k<s.length;k++){var gi=tb(s,k)-fi,co=(gi>=0&&gi<n)?g[gi]:g[0];
for(var j=0;j<gw;j++){var bt=co[j],row=0;while(bt){if(bt&1)fr(X+j*sc,y+row*sc,sc,sc,c);bt>>=1;row++;}}X+=gw*sc;}}
function rep(cs){for(var i=0;i<cs.length;i++){var c=cs[i],o=c[0];
if(o=="cls"){if(vOn)fr(0,0,vCW,vCH,c[1]);else idx.fill(pm[c[1]&63]);}else if(o=="pix")put(c[1],c[2],c[3]);
else if(o=="line")ln(c[1],c[2],c[3],c[4],c[5]);else if(o=="rect")fr(c[1],c[2],c[3],c[4],c[5]);
else if(o=="rectb")rb(c[1],c[2],c[3],c[4],c[5]);else if(o=="circ")ci(c[1],c[2],c[3],c[4]);
else if(o=="circb")cb(c[1],c[2],c[3],c[4]);
else if(o=="defspr")ATL[c[1]]={w:c[2],h:c[3],t:c[4],px:c[5]};
// spr has TWO shapes: atlas ["spr",idx,x,y,sc,fl] (<=6 fields) and self-contained
// ["spr",x,y,sc,w,h,t,pix,fl] (a pix array at c[7]). Branch on the pix array.
else if(o=="spr"){if(c.length>7&&c[7]&&c[7].length!==undefined)blt(c[7],c[4],c[5],c[6],c[1],c[2],c[3],c[8]||0);else sp(c[1],c[2],c[3],c[4],c[5]||0);}
else if(o=="img")im(c[1],c[2],c[3],c[4],c[5],c[6]);else if(o=="imgref")imr(c[1],c[2],c[3],c[4]);
else if(o=="deflayer")dfl(c[1],c[2],c[3],c[4]);else if(o=="blit_layer")bl(c);
else if(o=="scr")scr(c[1],c[2],c[3],c[4],c[5],c[6]);
else if(o=="settiles")st(c[1],c[2],c[3]);else if(o=="map")mp(c[1],c[2],c[3],c[4],c[5],c[6],c[7],c[8]);
else if(o=="print")tx(c[1],c[2],c[3],c[4],c[5]);else if(o=="reset_state")rs();
else if(o=="camera"){caX=c[1]|0;caY=c[2]|0;}
// A cart clip is in cart SCREEN space (canvas.py applies camera, then clips): scale
// and offset it into the target, then INTERSECT the view rect so a clipping cart
// still cannot draw outside its window.
else if(o=="clip"){if(c.length>1){var a=((c[1]|0)*vS+vOX)|0,b=((c[2]|0)*vS+vOY)|0,
w=(c[3]|0)*vS,h=(c[4]|0)*vS;
var qL=vOn?Math.max(0,vOX):0,qT=vOn?Math.max(0,vOY):0,
qR=vOn?Math.min(W,vOX+vCW*vS):W,qB=vOn?Math.min(H,vOY+vCH*vS):H;
cl0=Math.max(qL,a);cm0=Math.max(qT,b);cl1=Math.min(qR,a+w);cm1=Math.min(qB,b+h);}else vclip();}
// The WM view bracket: place the enclosed cart draw span at (ox, oy), integer-scaled
// by c[3], clipped to its w x h surface. ["view"] with no args restores identity.
else if(o=="view"){if(c.length>1){vOX=c[1]|0;vOY=c[2]|0;vS=(c[3]|0)||1;
vCW=c[4]|0;vCH=c[5]|0;vOn=true;}
else{vOX=0;vOY=0;vS=1;vCW=W;vCH=H;vOn=false;}
caX=0;caY=0;vclip();}
else if(o=="pal"){if(c.length>1)pm[c[1]&63]=c[2]&63;else for(var q=0;q<64;q++)pm[q]=q;}
else if(o=="palt"){if(c.length>1)pt[c[1]&63]=c[2]?1:0;else pt.fill(0);}}}
// PAGE-SIDE PERF (surface model Phase B): the index->RGBA conversion and the
// putImageData upload are timed SEPARATELY. In node both look cheap; in a real
// browser the upload of a 1024x600 ImageData (2.4MB) is a CPU->GPU texture
// write every frame, which no headless probe models -- so the page must report
// its own numbers or the desktop tier gets optimized blind.
function blit(){var t0=PT();var n=W*H,j=0;for(var i=0;i<n;i++){var p=PAL[idx[i]];rgba[j++]=p[0];rgba[j++]=p[1];
rgba[j++]=p[2];rgba[j++]=255;}var t1=PT();cx.putImageData(img,0,0);var t2=PT();
if(t1-t0>PERF.mb)PERF.mb=t1-t0;if(t2-t1>PERF.mu)PERF.mu=t2-t1;
PERF.sb+=t1-t0;PERF.su+=t2-t1;PERF.sn++;}
var q=[];function send(e){q.push(e);}
function xy(cX,cY){var r=cv.getBoundingClientRect();var x=Math.floor((cX-r.left)/r.width*W),
y=Math.floor((cY-r.top)/r.height*H);return{x:Math.max(0,Math.min(W-1,x)),y:Math.max(0,Math.min(H-1,y))};}
var drag=false;
cv.addEventListener("pointerdown",function(e){cv.focus();cv.setPointerCapture(e.pointerId);drag=true;
var p=xy(e.clientX,e.clientY);send({type:"down",x:p.x,y:p.y});e.preventDefault();});
cv.addEventListener("pointermove",function(e){var p=xy(e.clientX,e.clientY);
if(!drag){
// HOVER (2026-07-31): report the idle pointer too, or the shell's hover
// feedback (desk icons, cards) never sees the mouse -- it looked dead on the
// web while working in the pygame sim, which polls the mouse every frame.
// COALESCED: a mouse fires far more moves than frames, so replace the queued
// hover instead of appending -- only the newest position matters, and the
// queue drains once per frame anyway.
if(q.length&&q[q.length-1].type==="hover")q[q.length-1]={type:"hover",x:p.x,y:p.y};
else send({type:"hover",x:p.x,y:p.y});
return;}
send({type:"move",x:p.x,y:p.y});e.preventDefault();});
function up(e){if(!drag)return;drag=false;send({type:"up"});if(e)e.preventDefault();}
cv.addEventListener("pointerup",up);cv.addEventListener("pointercancel",up);
var jE=document.getElementById("joy"),tE=document.getElementById("th"),jA=false,jP=null,
jH={left:false,right:false,up:false,down:false};
function jAp(d){["left","right","up","down"].forEach(function(n){var w=!!d[n];if(w!=jH[n]){jH[n]=w;
send({type:"hold",name:n,down:w});}});}
function jT(e){var r=jE.getBoundingClientRect(),cX=r.left+r.width/2,cY=r.top+r.height/2,
dx=e.clientX-cX,dy=e.clientY-cY,rad=r.width/2,d=Math.sqrt(dx*dx+dy*dy);
if(d>rad&&d>0){var s=rad/d;dx*=s;dy*=s;}tE.style.transform="translate("+dx+"px,"+dy+"px)";
var dz=rad*0.35;jAp({left:dx<-dz,right:dx>dz,up:dy<-dz,down:dy>dz});}
jE.addEventListener("pointerdown",function(e){jA=true;jP=e.pointerId;jE.setPointerCapture(e.pointerId);
jT(e);e.preventDefault();});
jE.addEventListener("pointermove",function(e){if(!jA||e.pointerId!=jP)return;jT(e);e.preventDefault();});
function jEnd(e){if(!jA||(e&&e.pointerId!=jP))return;jA=false;jP=null;jAp({});
tE.style.transform="translate(0,0)";if(e)e.preventDefault();}
jE.addEventListener("pointerup",jEnd);jE.addEventListener("pointercancel",jEnd);
function wb(id,nm){var el=document.getElementById(id),dn=false;
function pr(e){if(dn)return;dn=true;el.classList.add("pr");send({type:"hold",name:nm,down:true});if(e)e.preventDefault();}
function rl(e){if(!dn)return;dn=false;el.classList.remove("pr");send({type:"hold",name:nm,down:false});if(e)e.preventDefault();}
el.addEventListener("pointerdown",function(e){el.setPointerCapture(e.pointerId);pr(e);});
el.addEventListener("pointerup",rl);el.addEventListener("pointercancel",rl);el.addEventListener("pointerleave",rl);}
wb("ba","a");wb("bb","b");wb("bh","home");  // &#9776; = HOME (Stage 5 EXIT key): a HELD wb button streams "home" down, so holding it ~700ms is the hold-to-exit gesture (server-side synthesized hold)
// Android long-press fires contextmenu even with pointerdown preventDefault'd -- swallow it
// on the play surfaces (canvas + control bar) only, so desktop right-click elsewhere is normal.
document.addEventListener("contextmenu",function(e){var t=e.target;
if(t===cv||(t.closest&&t.closest("#ctl")))e.preventDefault();});
var PAN={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},
NAV={a:"left",d:"right",w:"up",s:"down"},SC={Enter:"run",z:"a",x:"b"},pH={},nH={};
// No letter->HOME shortcut: page buttons BYPASS the device's text-mode alias
// suppression, so h-as-HOME stole the letter h from typing carts (Letter
// Blitz). EXIT from the page (Stage 5) = HOLD the burger button ~700ms for a
// game (it streams a held "home" -> the hold-to-exit gesture), or tap the tool
// bar's X for a tool. A single Backspace outside text mode is sent as typed cd=8
// and the SERVER also maps it to a HOME press (a plain key edge the cart reads,
// mirroring the physical key). In text mode Backspace stays a typed 0x08 (DELETE
// for a tool -- never an exit).
function nv(e){var k=e.key.length==1?e.key.toLowerCase():e.key;return NAV[k];}
var AN={ArrowLeft:"left",ArrowRight:"right",ArrowUp:"up",ArrowDown:"down"};
cv.addEventListener("keydown",function(e){if(e.key in PAN){
// Arrows are CONTEXTUAL: while a cart owns the keyboard (INPUT = the effective
// input hint, non-null only during play) they are the D-PAD (PICO-8 muscle
// memory: arrows + Z/X). On SYSTEM surfaces they NAVIGATE -- the same
// left/right/up/down buttons w/a/s/d send -- because in a browser the MOUSE is
// the cursor (owner call 2026-07-31): steering a virtual cursor with the arrow
// keys made the desktop feel like neither a console nor a PC, and it painted
// cursor trails. The trackball-pan mapping stays alive in the code (pv/pump)
// for backends that really have a trackball; nothing on this transport arms it.
var an=AN[e.key];
if(!nH[an]){nH[an]=true;send({type:"hold",name:an,down:true});}
e.preventDefault();return;}
if(e.key=="Escape"){
// In play (INPUT set), Esc is RESET -- the p8-web-player expectation (their pause
// menu's reset). Transports without a reset handler ignore the event safely.
send(INPUT?{type:"reset"}:{type:"esc"});e.preventDefault();return;}var cd=null;
if(e.key=="Enter")cd=13;
// A physically HELD Backspace also streams a sustained "home" (bshold down/up on the
// press/release edges) -- the desktop-keyboard twin of the burger button's hold, so
// hold-Backspace-to-exit works from a desktop browser. Text-mode gating is server-side.
else if(e.key=="Backspace"){cd=8;if(!e.repeat)send({type:"bshold",down:true});}
else if(e.key.length==1&&e.key.charCodeAt(0)>=32&&e.key.charCodeAt(0)<=126)cd=e.key.charCodeAt(0);
if(cd!==null)send({type:"key",code:cd});
// A held printable key must STREAM in game mode (the device's raw matrix
// reports a held key every frame; browser autorepeat is ~30Hz and gappy --
// key() read per frame flapped code/0/code). khold latches the physical hold;
// the server feeds it every frame outside text mode.
if(cd!==null&&cd>=32&&!e.repeat)send({type:"khold",code:cd,down:true});
// Enter/Z/X are BUTTONS (run/a/b): hold semantics, not one-frame press edges --
// a p8 cart holding jump reads btn() the whole hold; btnp() is the edge anyway.
var s=SC[e.key.length==1?e.key.toLowerCase():e.key];
if(s&&!nH[s]){nH[s]=true;send({type:"hold",name:s,down:true});}
var n=nv(e);if(n&&!nH[n]){nH[n]=true;send({type:"hold",name:n,down:true});}
if(s||n||cd!==null)e.preventDefault();});
cv.addEventListener("keyup",function(e){if(e.key in PAN){delete pH[e.key];
var an=AN[e.key];if(nH[an]){delete nH[an];send({type:"hold",name:an,down:false});}
e.preventDefault();return;}
if(e.key=="Backspace")send({type:"bshold",down:false});
if(e.key.length==1&&e.key.charCodeAt(0)>=32&&e.key.charCodeAt(0)<=126)
send({type:"khold",code:e.key.charCodeAt(0),down:false});
var s=SC[e.key.length==1?e.key.toLowerCase():e.key];
if(s&&nH[s]){delete nH[s];send({type:"hold",name:s,down:false});}
var n=nv(e);if(n&&nH[n]){delete nH[n];send({type:"hold",name:n,down:false});}});
// Soft keyboard (#42 Thread 2): #kb (in the bottom bar's middle cluster, beside the burger)
// focuses the hidden #kbin so a touch device's on-screen keyboard opens; it never appears on
// desktop (#ctl is media-query hidden there) and #kbin's events are entirely separate from
// cv's keydown above, so a physical keyboard is never double-counted. Typed characters are read by DIFFING #kbin's value against a single-space
// SENTINEL kept at all times -- soft keyboards routinely fire keydown with no usable key (IME
// composition reports keyCode 229/"Unidentified"), so the value delta is the only reliable
// signal; the sentinel exists so Backspace on an otherwise-empty field still fires an `input`
// event (deleting the last real character leaves nothing to delete otherwise).
var kbBtn=document.getElementById("kb"),kbInp=document.getElementById("kbin");
function kbReset(){kbInp.value=" ";try{kbInp.setSelectionRange(1,1);}catch(e){}}
kbReset();
function kbScroll(){cv.scrollIntoView({block:"center",inline:"center"});}
kbInp.addEventListener("input",function(){var v=kbInp.value;
  if(v.length<1){send({type:"key",code:8});kbReset();return;}      // sentinel gone -> Backspace
  var add=v.charAt(0)===" "?v.slice(1):v;                          // typed text lands after the sentinel
  for(var i=0;i<add.length;i++){var c=add.charCodeAt(i);
    if(c===10||c===13)send({type:"key",code:13});
    else if(c>=32&&c<=126)send({type:"key",code:c});}              // letters/digits/punctuation incl. = [ ] { } < > %
  kbReset();});
// A single-line <input> never inserts a newline, so Enter needs its own listener (still
// reliable on soft keyboards -- unlike printable keys, the Enter key name is widely reported).
kbInp.addEventListener("keydown",function(e){if(e.key==="Enter"){send({type:"key",code:13});e.preventDefault();}});
// While typing, the joystick + A/B are dead weight that eat the (already
// keyboard-shrunken) viewport -- hide them so the canvas keeps the room; blur
// restores them. The middle cluster (#kb + burger) STAYS so the toggle and
// HOME/exit remain reachable mid-type.
var abEl=document.getElementById("ab");
// #42 Thread 3: syncCtl() shows the joystick/A-B only when BOTH the cart wants buttons
// (padWanted, from the manifest input hint) AND we're not mid-type (kbInp focused);
// applyInputHint() re-derives padWanted/kbWanted from INPUT (set by getA() on every
// /assets fetch, i.e. every cart change). #kb gates on kbWanted; the burger never hides
// (a touch-only cart still needs an exit affordance -- the bar itself is only ever
// hidden by the desktop media query).
function syncCtl(){var pad=(padWanted&&document.activeElement!==kbInp)?"":"none";
  jE.style.display=pad;abEl.style.display=pad;kbBtn.style.display=kbWanted?"":"none";}
function applyInputHint(){padWanted=!INPUT||INPUT.indexOf("buttons")>=0;
  kbWanted=!INPUT||INPUT.indexOf("keyboard")>=0;syncCtl();}
kbInp.addEventListener("focus",function(){kbBtn.classList.add("pr");
  syncCtl();fit();
  if(window.visualViewport)window.visualViewport.addEventListener("resize",kbScroll);kbScroll();});
kbInp.addEventListener("blur",function(){kbBtn.classList.remove("pr");
  syncCtl();fit();
  // The keyboard-close animation outlives the blur: re-fit after it settles,
  // even on browsers that fire no resize event at all for it.
  setTimeout(fit,300);setTimeout(fit,700);
  if(window.visualViewport)window.visualViewport.removeEventListener("resize",kbScroll);});
kbBtn.addEventListener("click",function(e){
  if(document.activeElement===kbInp)kbInp.blur();else{kbReset();kbInp.focus();}
  e.preventDefault();});
var ok=false;
function pv(){return[(pH.ArrowRight?1:0)-(pH.ArrowLeft?1:0),(pH.ArrowDown?1:0)-(pH.ArrowUp?1:0)];}
// df(): render ONE frame payload. Atlas reset is driven by the device's `gen` (lock-step with
// its served reset), NOT the cart change -- so scrolling the launcher (cart_title -> /assets
// refetch) no longer wipes ATL and strands sprites (the unknown-growth bug, #41).
function df(f){if(f.perf){var p=f.perf;PERF.dh=p.heap;PERF.pf=p.pf;PERF.js=p.js;PERF.tx=p.tx;
// Fold this frame's device instants into the window MAX (undefined on a host that omits them
// stays 0 -- `undefined>0` is false). thr counts bandwidth-throttled pushes.
if(p.js>PERF.mj)PERF.mj=p.js;if(p.tx>PERF.mt)PERF.mt=p.tx;
if(p.dr>PERF.md)PERF.md=p.dr;if(p.gap>PERF.mg)PERF.mg=p.gap;if(p.thr)PERF.thr++;}
if(f.gen!==curGen){curGen=f.gen;ATL=[];LAY={};SURF={};HUD.unknown=0;}
if(f.cart!==assCart){assCart=f.cart;getA().catch(function(){});}
// #42 Thread 3: the EFFECTIVE input hint rides every frame (the cart's manifest hint
// while it owns the keyboard, null on any system surface) -- so the ⌨ summon comes
// back the moment PLAY exits to the Editor, without waiting for an /assets refetch.
var fi=f.input||null;
if(JSON.stringify(fi)!==JSON.stringify(INPUT)){INPUT=fi;applyInputHint();}
// Stage 9: the browser as a SECOND window manager -- when the frame carries per-WM-surface
// streams (f.surfaces: bar / app-content / player-viewport, each id-tagged), COMPOSITE them
// in order (bottom->top) reusing the same rep() interpreter + global ATL/LAY caches; the
// leading "_defs" surface ships the ship-once bitmaps/layers first. A flat frame (the device
// + web-view-off path) has no f.surfaces and replays f.cmds unchanged.
// #76 delta: a {"same":1} surface replays its cached commands; a full one updates the
// cache ("_defs" is ship-once-incremental, never cached) then replays.
var _rt0=PT();
if(f.surfaces){for(var si=0;si<f.surfaces.length;si++){var s=f.surfaces[si];
if(s.same){rep(SURF[s.id]||[]);}else{if(s.id!=="_defs")SURF[s.id]=s.cmds||[];rep(s.cmds||[]);}}}
else{rep(f.cmds||[]);}
var _rt=PT()-_rt0;if(_rt>PERF.mr)PERF.mr=_rt;PERF.sr+=_rt;
blit();
// A deflayer's imgref cache-MISS (racing the async /assets fetch) latched imgWant: re-fetch
// /assets (the server re-ships the deflayer on reset) until the paint image is cached (#63 F4).
if(imgWant&&!assLoading){imgWant=false;getA().catch(function(){});}
if(f.audio)playPCM(f.audio);
var t=(window.performance&&performance.now)?performance.now():Date.now();if(HUD.last){var inst=1000/Math.max(1,t-HUD.last);
HUD.fps=HUD.fps?HUD.fps+(inst-HUD.fps)*0.2:inst;}HUD.last=t;if(HUD.on)drawHud();
if(!ok){ok=true;sEl.textContent="live";sEl.style.color="#00e436";}}
function drawHud(){var n=0;for(var i=0;i<ATL.length;i++)if(ATL[i])n++;
var u=HUD.unknown?'<span class=w>'+HUD.unknown+'</span>':'0';
HUD.el.innerHTML="fps <b>"+HUD.fps.toFixed(1)+"</b>   "+HUD.kb.toFixed(2)+" KB/f<br>atlas <b>"+n+"</b>   unknown "+u;}
window.addEventListener("keydown",function(e){if(e.key==="`"||e.key==="~"){HUD.on=!HUD.on;
HUD.el.style.display=HUD.on?"block":"none";if(HUD.on)drawHud();e.preventDefault();}});
document.addEventListener("pointerdown",ensureAudio);
document.addEventListener("touchend",ensureAudio);  // legacy iOS only unlocks here
document.addEventListener("keydown",ensureAudio);
"""

# The LIVE transport tail (host http.server + device raw-socket -- both speak this):
# fetchAssets over HTTP, frames over a persistent WebSocket, boot at the end.
PAGE_LIVE_TAIL = """
function fetchAssets(){return fetch("/assets").then(function(r){return r.json();});}
// THE LIVE CHANNEL (#41): a persistent WebSocket, the ONLY transport now. Frames PUSH down
// (ws.onmessage -> df), input pushes up (pump() sends queued events per tick). BOTH the device
// and the host speak WS, so a closed/failed socket just reconnects with a small backoff -- no
// HTTP poll fallback. The onmessage byte-count feeds the perf log's bw/avg on both.
var ws=null,wsOpen=false,reconn=null,panWas=false;
function pump(){var v=pv();if(v[0]||v[1]){panWas=true;send({type:"pan",dx:v[0],dy:v[1]});}
else if(panWas){panWas=false;send({type:"pan",dx:0,dy:0});}
if(!q.length)return;var b=q;q=[];
if(wsOpen){try{ws.send(JSON.stringify({events:b}));}catch(e){}}}
function connect(){if(reconn){clearTimeout(reconn);reconn=null;}
try{ws=new WebSocket((location.protocol=="https:"?"wss://":"ws://")+location.host+"/ws");}
catch(e){retry();return;}
ws.onopen=function(){wsOpen=true;ok=false;sEl.textContent="live";sEl.style.color="#00e436";};
ws.onmessage=function(ev){var n=ev.data.length;HUD.kb=n/1024;PERF.f++;PERF.b+=n;if(n>PERF.pk)PERF.pk=n;
var f;try{f=JSON.parse(ev.data);}catch(e){return;}df(f);};
ws.onclose=function(){wsOpen=false;retry();};
ws.onerror=function(){try{ws.close();}catch(e){}};}
// Reconnect with a small fixed backoff (the socket dropped / the server restarted).
function retry(){wsOpen=false;sEl.textContent="reconnecting...";sEl.style.color="#ff004d";
if(reconn)return;reconn=setTimeout(function(){reconn=null;connect();},800);}
// Fetch /assets once over HTTP, then open the WebSocket live channel; pump queued input up on
// a timer.
getA().then(function(){connect();setInterval(pump,Math.round(1000/FPS));setInterval(plog,PERF_MS);}).catch(function(){
sEl.textContent="no assets";sEl.style.color="#ff004d";});cv.focus();
</script></body></html>"""

# ONE page for both live transports, exactly as before the PAGE_CORE split.
PAGE_HTML = PAGE_CORE + PAGE_LIVE_TAIL
