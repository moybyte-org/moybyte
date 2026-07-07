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

PAGE_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Moybyte device</title><style>
html,body{margin:0;height:100%;background:#0b0f1a;color:#c2c3c7;
font:14px ui-monospace,Menlo,Consolas,monospace;display:flex;flex-direction:column;
align-items:center}
h1{font-size:14px;color:#fff1e8;margin:8px}#s{color:#ffec27}
canvas{image-rendering:pixelated;background:#000;border:1px solid #1d2b53;border-radius:6px;
width:min(96vw,112vh);height:auto;max-width:100%;touch-action:none;cursor:crosshair}
#ctl{display:flex;justify-content:space-between;gap:24px;width:min(96vw,112vh);
max-width:100%;padding:12px 8px;box-sizing:border-box;touch-action:none;user-select:none}
#joy{position:relative;width:120px;height:120px;border-radius:50%;background:#1d2b53;
border:2px solid #29366f}#th{position:absolute;top:50%;left:50%;width:52px;height:52px;
margin:-26px 0 0 -26px;border-radius:50%;background:#5f6f9f;border:2px solid #c2c3c7;
pointer-events:none}.b{width:72px;height:72px;border-radius:50%;display:flex;
align-items:center;justify-content:center;font:700 26px ui-monospace;color:#fff1e8;
background:#7e2553;border:2px solid #c2c3c7;margin-left:18px}#bb{background:#29366f}
#bh{background:#5f574f;width:52px;height:52px;font-size:20px}
.pr{background:#ffec27;color:#1d2b53}
/* Debug HUD (#41): toggled with the ` key; lightweight live stream stats. */
#hud{position:fixed;top:6px;left:6px;z-index:9;display:none;padding:6px 8px;border-radius:5px;
background:rgba(11,15,26,.82);border:1px solid #1d2b53;color:#00e436;font:12px ui-monospace;
white-space:pre;pointer-events:none}#hud b{color:#ffec27}#hud .w{color:#ff004d}</style></head><body>
<div id=hud></div>
<h1>Moybyte &mdash; device <span id=s>connecting...</span> <small style="color:#5f6f9f">(press ` for stats)</small></h1>
<canvas id=cv width=320 height=240 tabindex=0></canvas>
<div id=ctl><div id=joy><div id=th></div></div>
<div><span class=b id=bh>&#9776;</span><span class=b id=bb>B</span><span class=b id=ba>A</span></div></div>
<script>
var FPS=30,cv=document.getElementById("cv"),cx=cv.getContext("2d"),sEl=document.getElementById("s");
cx.imageSmoothingEnabled=false;
var W=320,H=240,PAL=null,FONT=null,ready=false,assCart=undefined,idx=null,img=null,rgba=null;
// Payload-diet caches (#41): SHEET = the cart sprite sheet; TM = the cart tilemap (kept
// current by settiles); ATL = the per-session sprite atlas filled by defspr.
var SHEET=null,TM=null,ATL=[],curGen=-1;
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
var PERF_MS=2000,PERF={f:0,b:0,pk:0,t:0,dh:0,pf:0,lpf:0,js:0,tx:0,mg:0,md:0,mj:0,mt:0,thr:0};
function plog(){var now=(window.performance&&performance.now)?performance.now():Date.now();
if(!PERF.t){PERF.t=now;PERF.lpf=PERF.pf;return;}var dt=(now-PERF.t)/1000;if(dt<=0)return;
console.log("[moybyte] "+(assCart||"?")+" | recv "+(PERF.f/dt).toFixed(1)+" render "+HUD.fps.toFixed(1)
+" dev "+((PERF.pf-PERF.lpf)/dt).toFixed(1)+" fps | worst gap "+PERF.mg+" draw "+PERF.md+" js "+PERF.mj
+" tx "+PERF.mt+" ms | thr "+PERF.thr+" | bw "+(PERF.b/dt/1024).toFixed(1)+" KB/s avg "
+(PERF.f?(PERF.b/PERF.f/1024):0).toFixed(2)+" peak "+(PERF.pk/1024).toFixed(2)
+" KB | heap "+PERF.dh+" KB | unknown "+HUD.unknown);
PERF.f=0;PERF.b=0;PERF.pk=0;PERF.t=now;PERF.lpf=PERF.pf;PERF.mg=0;PERF.md=0;PERF.mj=0;PERF.mt=0;PERF.thr=0;}
// Audio (host web console): play the server's FINISHED PCM (no JS synth). The device streams
// no audio (f.audio ""), so this is a no-op there.
var AUDIO_RATE=11025,actx=null,audioNext=0,audioBlocked=false;
function ensureAudio(){if(!actx){var AC=window.AudioContext||window.webkitAudioContext;
if(AC){try{actx=new AC();}catch(e){actx=null;}}}if(actx&&actx.state==="suspended")actx.resume();}
// Audio frames that arrive while the context is blocked by the browser's autoplay
// policy used to be dropped SILENTLY -- undiagnosable on a phone. Surface the state
// in the status chip instead, and self-heal once a gesture unlocks the context.
function playPCM(b64){if(!b64)return;
if(!actx||actx.state!=="running"){if(!audioBlocked){audioBlocked=true;
sEl.textContent="tap screen to enable sound";sEl.style.color="#ffa300";}return;}
if(audioBlocked){audioBlocked=false;ok=false;}
var bin=atob(b64),n=bin.length>>1;if(n<=0)return;
var buf=actx.createBuffer(1,n,AUDIO_RATE),ch=buf.getChannelData(0);
for(var i=0;i<n;i++){var v=bin.charCodeAt(i*2)|(bin.charCodeAt(i*2+1)<<8);if(v>=32768)v-=65536;ch[i]=v/32768;}
var src=actx.createBufferSource();src.buffer=buf;src.connect(actx.destination);
var t=Math.max(actx.currentTime+0.02,audioNext);src.start(t);audioNext=t+buf.duration;}
function alloc(){cv.width=W;cv.height=H;cx=cv.getContext("2d");cx.imageSmoothingEnabled=false;
idx=new Uint8Array(W*H);img=cx.createImageData(W,H);rgba=img.data;rs();}
function getA(){assLoading=true;return fetch("/assets").then(function(r){return r.json();}).then(function(a){
W=a.w;H=a.h;PAL=a.palette;FONT=a.font;assCart=a.cart;SHEET=a.sheet||null;if(a.audio_rate)AUDIO_RATE=a.audio_rate;
TM=a.tilemap?{w:a.tilemap.w,h:a.tilemap.h,cells:a.tilemap.cells.slice()}:null;
// Decode each paint image's base64 raw indices into a Uint8Array ONCE (#63 Fold 4), so an
// imgref just blits the cached bytes (index->palette). Keyed by the SAME name image('name') tags.
IMG={};if(a.images){for(var nm in a.images){var gi=a.images[nm],bs=atob(gi.b64),bn=bs.length,bp=new Uint8Array(bn);
for(var bk=0;bk<bn;bk++)bp[bk]=bs.charCodeAt(bk);IMG[nm]={w:gi.w,h:gi.h,px:bp};}}
assLoading=false;alloc();ready=true;}).catch(function(e){assLoading=false;throw e;});}
// NB: do NOT clear ATL here -- it resets on `gen` change (see df). imgWant is cleared by df's retry.
var caX=0,caY=0,cl0=0,cm0=0,cl1=W,cm1=H,pm=null,pt=null;
function rs(){caX=0;caY=0;cl0=0;cm0=0;cl1=W;cm1=H;pm=new Uint8Array(64);pt=new Uint8Array(64);
for(var i=0;i<64;i++)pm[i]=i;}rs();
function put(x,y,c){x=(x-caX)|0;y=(y-caY)|0;if(x<cl0||x>=cl1||y<cm0||y>=cm1)return;idx[y*W+x]=pm[c&63];}
function fr(x,y,w,h,c){x=(x|0)-caX;y=(y|0)-caY;w|=0;h|=0;var a=Math.max(cl0,x),b=Math.max(cm0,y),
e=Math.min(cl1,x+w),f=Math.min(cm1,y+h);if(e<=a||f<=b)return;var ci=pm[c&63];
for(var yy=b;yy<f;yy++){var bs=yy*W;for(var xx=a;xx<e;xx++)idx[bs+xx]=ci;}}
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
// skipped) into the CURRENT target (idx/W/H) clamped, the browser twin of blit_indices.
function im(x,y,w,h,b64){var s=atob(b64);x|=0;y|=0;w|=0;h|=0;
for(var yy=0;yy<h;yy++){var ty=y+yy;if(ty<0||ty>=H)continue;var sr=yy*w,dr=ty*W;
for(var xx=0;xx<w;xx++){var tx=x+xx;if(tx<0||tx>=W)continue;var p=s.charCodeAt(sr+xx);if(p<64)idx[dr+tx]=p;}}}
// imgref (#63 Fold 4): a paint image by NAME, blitted from the /assets IMG cache -- the normal
// path (pixels shipped once, not per-frame). Same opaque index->target blit as im, but reading
// the pre-decoded Uint8Array. A cache MISS latches imgWant so df() re-fetches /assets (the layer
// deflayer re-ships once assets arrive) -- a ship-once layer can otherwise strand its background.
function imr(x,y,nm){var G=IMG[nm];if(!G){imgWant=true;return;}var s=G.px,w=G.w,h=G.h;x|=0;y|=0;
for(var yy=0;yy<h;yy++){var ty=y+yy;if(ty<0||ty>=H)continue;var sr=yy*w,dr=ty*W;
for(var xx=0;xx<w;xx++){var tx=x+xx;if(tx<0||tx>=W)continue;var p=s[sr+xx];if(p<64)idx[dr+tx]=p;}}}
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
// OFF-SCREEN LAYERS (#54 scroll + #43 cached top bar): deflayer (re)builds an off-screen
// index buffer by REPLAYING the layer's recorded stream (reusing rep()); blit_layer copies a
// window (draw_layer) or the full layer (blit_strip) into idx. LAY keeps each built buffer.
var LAY={};
function dfl(id,lw,lh,cmds){var sI=idx,sW=W,sH=H,sX=caX,sY=caY,s0=cl0,s1=cm0,s2=cl1,s3=cm1,sm=pm,spt=pt;
var buf=new Uint8Array(lw*lh);idx=buf;W=lw;H=lh;rs();rep(cmds);
idx=sI;W=sW;H=sH;caX=sX;caY=sY;cl0=s0;cm0=s1;cl1=s2;cm1=s3;pm=sm;pt=spt;LAY[id]={w:lw,h:lh,buf:buf};}
function blw(L,cx,cy){cx=cx<0?0:cx|0;cy=cy<0?0:cy|0;var dw=W,dh=H,sw=L.w,src=L.buf;
if(sw<=0||dw<=0||dh<=0)return;if(cx+dw>sw)dw=sw-cx;if(dw<=0)return;var sr=(src.length/sw)|0;
if(cy+dh>sr)dh=sr-cy;if(dh<=0)return;for(var r=0;r<dh;r++){var d0=r*W,o0=(cy+r)*sw+cx;
for(var x=0;x<dw;x++)idx[d0+x]=src[o0+x];}}
function blf(L,dx,dy){dx|=0;dy|=0;var dw=W,dh=H,sw=L.w,sh=L.h,src=L.buf;if(sw<=0||dw<=0||dh<=0)return;
for(var r=0;r<sh;r++){var ty=dy+r;if(ty<0||ty>=dh)continue;var cw=sw,x0=0,t0=dx,o0=r*sw;
if(t0<0){x0=-t0;cw+=t0;t0=0;}if(t0+cw>dw)cw=dw-t0;if(cw<=0)continue;var d0=ty*W+t0;
for(var x=0;x<cw;x++)idx[d0+x]=src[o0+x0+x];}}
function bl(c){var L=LAY[c[1]];if(!L)return;if(c.length>4&&c[4]==="full")blf(L,c[2],c[3]);else blw(L,c[2],c[3]);}
function tx(s,x,y,c){if(!FONT)return;var X=x|0;y|=0;var fi=FONT.first,gw=FONT.w,g=FONT.glyphs,n=g.length;
for(var k=0;k<s.length;k++){var gi=s.charCodeAt(k)-fi,co=(gi>=0&&gi<n)?g[gi]:g[0];
for(var j=0;j<gw;j++){var bt=co[j],py=y;while(bt){if(bt&1)put(X+j,py,c);bt>>=1;py++;}}X+=gw;}}
function rep(cs){for(var i=0;i<cs.length;i++){var c=cs[i],o=c[0];
if(o=="cls")idx.fill(pm[c[1]&63]);else if(o=="pix")put(c[1],c[2],c[3]);
else if(o=="line")ln(c[1],c[2],c[3],c[4],c[5]);else if(o=="rect")fr(c[1],c[2],c[3],c[4],c[5]);
else if(o=="rectb")rb(c[1],c[2],c[3],c[4],c[5]);else if(o=="circ")ci(c[1],c[2],c[3],c[4]);
else if(o=="circb")cb(c[1],c[2],c[3],c[4]);
else if(o=="defspr")ATL[c[1]]={w:c[2],h:c[3],t:c[4],px:c[5]};
// spr has TWO shapes: atlas ["spr",idx,x,y,sc,fl] (<=6 fields) and self-contained
// ["spr",x,y,sc,w,h,t,pix,fl] (a pix array at c[7]). Branch on the pix array.
else if(o=="spr"){if(c.length>7&&c[7]&&c[7].length!==undefined)blt(c[7],c[4],c[5],c[6],c[1],c[2],c[3],c[8]||0);else sp(c[1],c[2],c[3],c[4],c[5]||0);}
else if(o=="img")im(c[1],c[2],c[3],c[4],c[5]);else if(o=="imgref")imr(c[1],c[2],c[3]);
else if(o=="deflayer")dfl(c[1],c[2],c[3],c[4]);else if(o=="blit_layer")bl(c);
else if(o=="settiles")st(c[1],c[2],c[3]);else if(o=="map")mp(c[1],c[2],c[3],c[4],c[5],c[6],c[7],c[8]);
else if(o=="print")tx(c[1],c[2],c[3],c[4]);else if(o=="reset_state")rs();
else if(o=="camera"){caX=c[1]|0;caY=c[2]|0;}
else if(o=="clip"){if(c.length>1){var a=c[1]|0,b=c[2]|0,w=c[3]|0,h=c[4]|0;cl0=Math.max(0,a);cm0=Math.max(0,b);
cl1=Math.min(W,a+w);cm1=Math.min(H,b+h);}else{cl0=0;cm0=0;cl1=W;cm1=H;}}
else if(o=="pal"){if(c.length>1)pm[c[1]&63]=c[2]&63;else for(var q=0;q<64;q++)pm[q]=q;}
else if(o=="palt"){if(c.length>1)pt[c[1]&63]=c[2]?1:0;else pt.fill(0);}}}
function blit(){var n=W*H,j=0;for(var i=0;i<n;i++){var p=PAL[idx[i]];rgba[j++]=p[0];rgba[j++]=p[1];
rgba[j++]=p[2];rgba[j++]=255;}cx.putImageData(img,0,0);}
var q=[];function send(e){q.push(e);}
function xy(cX,cY){var r=cv.getBoundingClientRect();var x=Math.floor((cX-r.left)/r.width*W),
y=Math.floor((cY-r.top)/r.height*H);return{x:Math.max(0,Math.min(W-1,x)),y:Math.max(0,Math.min(H-1,y))};}
var drag=false;
cv.addEventListener("pointerdown",function(e){cv.focus();cv.setPointerCapture(e.pointerId);drag=true;
var p=xy(e.clientX,e.clientY);send({type:"down",x:p.x,y:p.y});e.preventDefault();});
cv.addEventListener("pointermove",function(e){if(!drag)return;var p=xy(e.clientX,e.clientY);
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
cv.addEventListener("keydown",function(e){if(e.key in PAN){pH[e.key]=true;e.preventDefault();return;}
if(e.key=="Escape"){send({type:"esc"});e.preventDefault();return;}var cd=null;
if(e.key=="Enter")cd=13;else if(e.key=="Backspace")cd=8;else if(e.key.length==1&&e.key.charCodeAt(0)>=32&&e.key.charCodeAt(0)<=126)cd=e.key.charCodeAt(0);
if(cd!==null)send({type:"key",code:cd});var s=SC[e.key.length==1?e.key.toLowerCase():e.key];
if(s&&!e.repeat)send({type:"press",name:s});var n=nv(e);if(n&&!nH[n]){nH[n]=true;send({type:"hold",name:n,down:true});}
if(s||n||cd!==null)e.preventDefault();});
cv.addEventListener("keyup",function(e){if(e.key in PAN){delete pH[e.key];e.preventDefault();return;}
var n=nv(e);if(n&&nH[n]){delete nH[n];send({type:"hold",name:n,down:false});}});
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
if(f.gen!==curGen){curGen=f.gen;ATL=[];LAY={};HUD.unknown=0;}
if(f.cart!==assCart){assCart=f.cart;getA().catch(function(){});}
// Stage 9: the browser as a SECOND window manager -- when the frame carries per-WM-surface
// streams (f.surfaces: bar / app-content / player-viewport, each id-tagged), COMPOSITE them
// in order (bottom->top) reusing the same rep() interpreter + global ATL/LAY caches; the
// leading "_defs" surface ships the ship-once bitmaps/layers first. A flat frame (the device
// + web-view-off path) has no f.surfaces and replays f.cmds unchanged.
if(f.surfaces){for(var si=0;si<f.surfaces.length;si++)rep(f.surfaces[si].cmds||[]);}else{rep(f.cmds||[]);}
blit();
// A deflayer's imgref cache-MISS (racing the async /assets fetch) latched imgWant: re-fetch
// /assets (the server re-ships the deflayer on reset) until the paint image is cached (#63 F4).
if(imgWant&&!assLoading){imgWant=false;getA().catch(function(){});}
if(f.audio)playPCM(f.audio);
var t=(window.performance&&performance.now)?performance.now():Date.now();if(HUD.last){var inst=1000/Math.max(1,t-HUD.last);
HUD.fps=HUD.fps?HUD.fps+(inst-HUD.fps)*0.2:inst;}HUD.last=t;if(HUD.on)drawHud();
if(!ok){ok=true;sEl.textContent="live";sEl.style.color="#00e436";}}
// THE LIVE CHANNEL (#41): a persistent WebSocket, the ONLY transport now. Frames PUSH down
// (ws.onmessage -> df), input pushes up (pump() sends queued events per tick). BOTH the device
// and the host speak WS, so a closed/failed socket just reconnects with a small backoff -- no
// HTTP poll fallback. The onmessage byte-count feeds the perf log's bw/avg on both.
var ws=null,wsOpen=false,reconn=null;
function pump(){var v=pv();if(v[0]||v[1])send({type:"pan",dx:v[0],dy:v[1]});
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
function drawHud(){var n=0;for(var i=0;i<ATL.length;i++)if(ATL[i])n++;
var u=HUD.unknown?'<span class=w>'+HUD.unknown+'</span>':'0';
HUD.el.innerHTML="fps <b>"+HUD.fps.toFixed(1)+"</b>   "+HUD.kb.toFixed(2)+" KB/f<br>atlas <b>"+n+"</b>   unknown "+u;}
window.addEventListener("keydown",function(e){if(e.key==="`"||e.key==="~"){HUD.on=!HUD.on;
HUD.el.style.display=HUD.on?"block":"none";if(HUD.on)drawHud();e.preventDefault();}});
document.addEventListener("pointerdown",ensureAudio);
document.addEventListener("touchend",ensureAudio);  // legacy iOS only unlocks here
document.addEventListener("keydown",ensureAudio);
// Fetch /assets once over HTTP, then open the WebSocket live channel; pump queued input up on
// a timer.
getA().then(function(){connect();setInterval(pump,Math.round(1000/FPS));setInterval(plog,PERF_MS);}).catch(function(){
sEl.textContent="no assets";sEl.style.color="#ff004d";});cv.focus();
</script></body></html>"""
