/* Jarvis cockpit, softer and roomier: one moving prototype for the built panel ("before") and three options.
   A copy of ../jarvis-cockpit.html (third pass, approved 2026-11-12) with the geometry of the panel and its keys
   made a parameter. The core key, the springs, the flows, the listening ring and the colours are unchanged.
     opt=before | a | b | c    before = what is built (CockpitGeometry on main); a = soft keycaps; b = satellites; c = ribbon
     size=pro (402 x 874) | se (375 x 667, the smallest phone)
     theme=dark | light        the page behind the wash
     lang=zh | en
     demo=1&t=<ms>             a frame of the scripted film (three scenes, 21 s); still=1 hides the finger and the caption
     rec=1                     no animation loop; window.frame(ms) advances the film (used by record.mjs)
   No demo / rec = interactive: tap the key, then the keys. Speech is simulated.
   Geometry only (circle, arc, rounded sector, inverted triangle); icons are Lucide (ISC). All data synthetic. */
var q=new URLSearchParams(location.search),OPT=q.get('opt')||window.OPT||'a',SIZE=q.get('size')||'pro',THEME=q.get('theme')||'dark',
  EN=q.get('lang')==='en',DEMO=!!q.get('demo'),REC=!!q.get('rec'),STILL=!!q.get('still');
var W=SIZE==='se'?375:402,H=SIZE==='se'?667:874,CX=W/2,CY=H-123,CW=Math.min(354,W-48);
var A='#8FDCFF',INK='#F4F4F6',DIM='#8D8D94',GOOD='#72BF8E',PANEL='#0B0E12',LIT='#04121A';
function L(zh,en){return EN?en:zh}

/* ---- geometry: the only thing that differs between the options (points; angles in degrees, 0 = 12 o'clock) ---- */
var GEO={
  before:{RM:108,RI:80,RO:136,HW:16,rc:0,tick:[143,146.5,150],sel:153,cardR:6,chamfer:18},
  a:{RM:108,RI:82,RO:134,HW:13,rc:13,tick:[146,149,152],sel:156,cardR:22,chamfer:0},
  b:{RM:116,KR:24,tick:[148,151,154],sel:158,cardR:22,chamfer:0},
  c:{RM:110,BW:28,tick:[0,0,0],sel:144,cardR:22,chamfer:0}
},G=GEO[OPT]||GEO.a,RM=G.RM;

var L1=[{a:-72,i:'soup',l:L('记一餐','Meal'),act:'sub:meal'},{a:-36,i:'dumbbell',l:L('记训练','Workout'),act:'sub:train'},{a:0,i:'mic',l:L('说话','Talk'),act:'voice:qa'},{a:36,i:'camera',l:L('拍照','Photo')},{a:72,i:'message-circle',l:L('提问','Ask')}];
var MEAL=[{a:-72,i:'camera',l:L('拍照','Photo')},{a:-36,i:'mic',l:L('说','Say'),act:'voice:meal'},{a:0,t:L('鸡胸饭','Chicken'),n:'570',s:'570 kcal',act:'log:meal'},{a:36,t:L('燕麦碗','Oats'),n:'420',s:'420 kcal',act:'log:meal'},{a:72,i:'ellipsis',l:L('更多','More')}];
var TRAIN=[{a:-36,i:'zap',l:L('开始','Start'),act:'nav'},{a:0,i:'clock',l:L('补记','Backfill')},{a:36,i:'calendar-days',l:L('计划','Plan')}];
var SAY={qa:{text:L('今天适合练腿吗','Legs today?'),dur:1500,kind:'qa',tk:1300},meal:{text:L('中午吃了鸡胸饭','Chicken rice for lunch'),dur:1500,kind:'meal',tk:1000}};
var CARDH={qa:214,meal:190};
var S={mode:'idle',T:0,since:0,items:L1,lv:1,fanT:0,fanSince:0,sel:-1,pend:null,say:SAY.qa,kind:'qa',tk:1300,nav:0,rotT:0,ra:0,rb:0,pulse:-9999,crumbI:'soup',taps:[],cap:'',ext:74};
var V={};
function spr(n,to,k,c,dt){var o=V[n]||(V[n]={x:0,v:0});o.v+=(-k*(o.x-to)-c*o.v)*dt/1000;o.x+=o.v*dt/1000;return o.x}
function val(n){return V[n]?V[n].x:0}
function cl(x){return x<0?0:x>1?1:x}
function e(x){x=cl(x);return 1-Math.pow(1-x,3)}
function f(n){return n.toFixed(2)}
function rad(d){return d*Math.PI/180}
function P(r,d){var a=rad(d);return [CX+r*Math.sin(a),CY-r*Math.cos(a)]}
function pt(p){return f(p[0])+' '+f(p[1])}
function arc(r,a0,a1){if(a1-a0>=359.9)return 'M'+f(CX-r)+' '+CY+'a'+f(r)+' '+f(r)+' 0 1 0 '+f(2*r)+' 0a'+f(r)+' '+f(r)+' 0 1 0 '+f(-2*r)+' 0';
  return 'M'+pt(P(r,a0))+'A'+f(r)+' '+f(r)+' 0 '+((a1-a0)>180?1:0)+' 1 '+pt(P(r,a1))}
/* an arc around any point (for the lit rim of a round key) */
function arcAt(c,r,a0,a1){var p=[c[0]+r*Math.sin(rad(a0)),c[1]-r*Math.cos(rad(a0))],b=[c[0]+r*Math.sin(rad(a1)),c[1]-r*Math.cos(rad(a1))];
  return 'M'+pt(p)+'A'+f(r)+' '+f(r)+' 0 '+((a1-a0)>180?1:0)+' 1 '+pt(b)}
function sector(ri,ro,a0,a1){return 'M'+pt(P(ri,a0))+'L'+pt(P(ro,a0))+'A'+f(ro)+' '+f(ro)+' 0 0 1 '+pt(P(ro,a1))+'L'+pt(P(ri,a1))+'A'+f(ri)+' '+f(ri)+' 0 0 0 '+pt(P(ri,a0))+'Z'}
/* option A: the same ring sector with four round corners (in SwiftUI: Path.addArc(tangent1End:tangent2End:radius:)) */
function rsector(ri,ro,a0,a1,rc){var s=Math.sin(rad((a1-a0)/2*.9));rc=Math.max(.5,Math.min(rc,(ro-ri)/2-.3,ri*s/(1-s)));
  var to=Math.asin(rc/(ro-rc))*180/Math.PI,ti=Math.asin(rc/(ri+rc))*180/Math.PI,lo=Math.sqrt((ro-rc)*(ro-rc)-rc*rc),li=Math.sqrt((ri+rc)*(ri+rc)-rc*rc),c=' A'+f(rc)+' '+f(rc)+' 0 0 1 ';
  return 'M'+pt(P(li,a0))+'L'+pt(P(lo,a0))+c+pt(P(ro,a0+to))+'A'+f(ro)+' '+f(ro)+' 0 0 1 '+pt(P(ro,a1-to))+c+pt(P(lo,a1))+'L'+pt(P(li,a1))+c+pt(P(ri,a1-ti))+'A'+f(ri)+' '+f(ri)+' 0 0 0 '+pt(P(ri,a0+ti))+c+pt(P(li,a0))+'Z'}
/* option C: one band with round ends, centre line at r, half width hw, from -E to E */
function ribbon(r,hw,E){var lg=2*E>180?1:0;
  return 'M'+pt(P(r-hw,-E))+'A'+f(hw)+' '+f(hw)+' 0 0 1 '+pt(P(r+hw,-E))+'A'+f(r+hw)+' '+f(r+hw)+' 0 '+lg+' 1 '+pt(P(r+hw,E))+'A'+f(hw)+' '+f(hw)+' 0 0 1 '+pt(P(r-hw,E))+'A'+f(r-hw)+' '+f(r-hw)+' 0 '+lg+' 0 '+pt(P(r-hw,-E))+'Z'}
function path(d,c,w,o){return '<path d="'+d+'" fill="none" stroke="'+c+'" stroke-width="'+w+'" stroke-linecap="round" opacity="'+f(cl(o))+'"/>'}
function ln(r0,r1,d,c,w,o){var p=P(r0,d),b=P(r1,d);return '<line x1="'+f(p[0])+'" y1="'+f(p[1])+'" x2="'+f(b[0])+'" y2="'+f(b[1])+'" stroke="'+c+'" stroke-width="'+w+'" stroke-linecap="round" opacity="'+f(cl(o))+'"/>'}
function disc(c,r,fill,fo,stroke,so,w){return '<circle cx="'+f(c[0])+'" cy="'+f(c[1])+'" r="'+f(Math.max(0,r))+'" fill="'+fill+'" fill-opacity="'+f(cl(fo))+'" stroke="'+(stroke||'none')+'" stroke-opacity="'+f(cl(so||0))+'" stroke-width="'+(w||1)+'"/>'}
function tri(cx,cy,r){var d='';[60,180,300].forEach(function(g,i){var a=rad(g);d+=(i?'L':'M')+f(cx+r*Math.sin(a))+' '+f(cy-r*Math.cos(a))});return d+'Z'}
function tx(x,y,s,size,c,o,anchor,w,ls){return '<text x="'+f(x)+'" y="'+f(y)+'" font-size="'+size+'" fill="'+c+'" opacity="'+f(cl(o))+'" text-anchor="'+(anchor||'middle')+'" letter-spacing="'+(ls||0)+'" font-weight="'+(w||500)+'">'+s+'</text>'}
function ico(n,x,y,size,c,o,sw){return '<g transform="translate('+f(x-size/2)+' '+f(y-size/2)+') scale('+f(size/24)+')" fill="none" stroke="'+c+'" stroke-width="'+(sw||1.8)+'" stroke-linecap="round" stroke-linejoin="round" opacity="'+f(cl(o))+'">'+window.LUCIDE[n].d+'</g>'}
function wave(r0,amp,ph){var d='';for(var i=0;i<=120;i++){var th=i*3,x=rad(th),r=r0+amp*(Math.sin(3*x+ph)*Math.sin(ph*.7)+.6*Math.sin(5*x-ph*1.4)),p=P(r,th);d+=(i?'L':'M')+pt(p)}return d+'Z'}
function ticks(r0,r1,r2,F,span,rot,dim){var g='';for(var i=0;i<90;i++){var an=((i*4+rot)%360+540)%360-180,ab=Math.abs(an);if(ab>span)continue;var maj=i%9===0;
  g+=ln(r0,maj?r2:r1,an,A,1,(maj?.85:.34)*dim*F*cl((span-ab)/14))}return g}

/* reply cards (unchanged content; the frame is softer in the options) */
function cardBody(kind,x,y,h){var s='',R=x+CW-20,k=CW/354;function rv(ry){return cl((h-ry)/26)}
  if(kind==='qa'){
    s+='<path d="'+tri(x+25,y+24,6)+'" fill="none" stroke="'+A+'" stroke-width="1.4" stroke-linejoin="round" opacity="'+f(rv(28))+'"/>'+tx(x+40,y+28,SAY.qa.text,12,DIM,rv(28),'start');
    s+=tx(x+20,y+78,L('可以练','Good to go'),30,INK,rv(78),'start',600)+tx(x+20,y+102,L('恢复够 · 可上强度','Recovered · push hard'),13,DIM,rv(102),'start');
    var gx=R-28,gy=y+72,c=2*Math.PI*26;
    s+='<circle cx="'+gx+'" cy="'+gy+'" r="26" fill="none" stroke="#fff" stroke-opacity=".14" stroke-width="4"/><circle cx="'+gx+'" cy="'+gy+'" r="26" fill="none" stroke="'+GOOD+'" stroke-width="4" stroke-linecap="round" stroke-dasharray="'+f(c*.82*rv(110))+' '+f(c)+'" transform="rotate(-90 '+gx+' '+gy+')"/>'+tx(gx,gy+6,'82',17,INK,rv(80),'middle',600);
    s+='<line x1="'+(x+20)+'" x2="'+R+'" y1="'+(y+122)+'" y2="'+(y+122)+'" stroke="#fff" stroke-opacity="'+f(.1*rv(122))+'"/>';
    [['88',L('睡眠','sleep')],['2.8',L('负荷','strain')],[L('3 天','3 d'),L('上次练腿','since legs')]].forEach(function(r,i){var rx=x+20+i*104*k;s+=tx(rx,y+148,r[0],17,INK,rv(150),'start',600)+tx(rx+(r[0].length>2?38:r[0].length*11+8),y+148,r[1],10.5,DIM,rv(150),'start')});
    s+='<rect x="'+(x+20)+'" y="'+(y+166)+'" width="112" height="32" rx="16" fill="'+A+'" fill-opacity=".1" stroke="'+A+'" stroke-opacity="'+f(.8*rv(196))+'"/>'+tx(x+76,y+186.5,L('开始训练','Start'),13,A,rv(196),'middle',600)+tx(R,y+186.5,L('展开对话 ›','Open chat ›'),12,DIM,rv(196),'end')}
  else{
    s+=ico('circle-check',x+27,y+23,16,GOOD,rv(28))+tx(x+42,y+28,L('已记到午餐','Logged to lunch'),12,DIM,rv(28),'start');
    s+='<rect x="'+(R-66)+'" y="'+(y+10)+'" width="66" height="26" rx="13" fill="none" stroke="#fff" stroke-opacity="'+f(.25*rv(28))+'"/>'+ico('undo-2',R-50,y+23,12,INK,rv(28))+tx(R-26,y+27.5,L('撤销','Undo'),12,INK,rv(28));
    s+=tx(x+20,y+80,L('鸡胸饭','Chicken rice'),26,INK,rv(80),'start',600)+tx(R-32,y+80,'570',26,INK,rv(80),'end',600)+tx(R,y+80,'kcal',11,DIM,rv(80),'end');
    [[L('蛋白','Prot'),'45 g',.75],[L('碳水','Carb'),'66 g',.62],[L('脂肪','Fat'),'14 g',.4]].forEach(function(r,i){var rx=x+20+i*108*k,bw=92*k;
      s+=tx(rx,y+110,r[0],10.5,DIM,rv(112),'start')+tx(rx+28,y+110,r[1],13,INK,rv(112),'start',600)+'<rect x="'+f(rx)+'" y="'+(y+120)+'" width="'+f(bw)+'" height="3" rx="1.5" fill="#fff" fill-opacity=".12"/><rect x="'+f(rx)+'" y="'+(y+120)+'" width="'+f(bw*r[2]*rv(140))+'" height="3" rx="1.5" fill="'+INK+'"/>'});
    var bw=CW-40,a0=bw*420/2300,a1=bw*570/2300*rv(176);
    s+='<rect x="'+(x+20)+'" y="'+(y+150)+'" width="'+bw+'" height="4" rx="2" fill="#fff" fill-opacity=".12"/><rect x="'+(x+20)+'" y="'+(y+150)+'" width="'+f(a0)+'" height="4" rx="2" fill="#fff" fill-opacity=".55"/><rect x="'+f(x+20+a0+2)+'" y="'+(y+150)+'" width="'+f(a1)+'" height="4" rx="2" fill="'+A+'"/>';
    s+=tx(x+20,y+174,L('今天 990 / 2,300 kcal','Today 990 / 2,300 kcal'),11,DIM,rv(176),'start')}
  return s}

/* ---- the keys, per option. Each returns {g: glowing shapes, t: icons and labels} ---- */
function keyText(it,c,ink,sub,co,dy){ // icon + label, or name + kcal, centred on c
  if(it.i)return ico(it.i,c[0],c[1]-7+(dy||0),22,ink,co)+tx(c[0],c[1]+19+(dy||0),it.l,10.5,ink,.9*co,'middle',500,.3);
  return tx(c[0],c[1]+1,it.t,12.5,ink,co,'middle',600)+tx(c[0],c[1]+16,it.s,9.5,sub,co)}

function keysSector(F){var g='',t='',rc=G.rc; // before and A
  S.items.forEach(function(it,k){var v=val('w'+k);if(v<.02)return;var vc=cl(v),hot=S.sel===k,sv=hot?val('sel'):0,od=(hot||S.sel<0)?1:1-.65*cl(val('sel'));
    var ang=it.a*v,ri=34+(G.RI-34)*v+7*sv,ro=ri+(G.RO-G.RI)*cl(v*1.15),hw=G.HW*(.25+.75*vc),o=cl(v*3)*od;
    var d=rc?rsector(ri,ro,ang-hw,ang+hw,rc):sector(ri,ro,ang-hw,ang+hw);
    g+='<path d="'+d+'" fill="'+PANEL+'" fill-opacity=".94" stroke="'+A+'" stroke-opacity="'+(rc?.3:.5)+'" stroke-width="1" stroke-linejoin="round" opacity="'+f(o)+'"/>';
    if(hot)g+='<path d="'+d+'" fill="'+A+'" opacity="'+f(cl(sv)*o)+'"/>';
    // the lit outer edge: the whole edge before; in A only the part between the round corners, thinner
    if(rc){var to=Math.asin(rc/(ro-rc))*180/Math.PI+.5;if(hw-to>1)g+=path(arc(ro,ang-hw+to,ang+hw-to),A,1.5,.9*o)}
    else g+=path(arc(ro,ang-hw+3.5,ang+hw-3.5),A,2,.95*o);
    var co=cl((v-.55)/.35)*od,c=P((ri+ro)/2,ang),lit=hot&&sv>.5;
    t+=keyText(it,c,lit?LIT:INK,lit?LIT:DIM,co)});
  return {g:g,t:t}}

function keysOrbit(F){var g='',t='',span=Math.min(100,S.ext+24)*e(F); // B: round keys on one hairline orbit, the label outside
  // the orbit: a hairline through the key centres (it shows only between the keys)
  var rr=34+(RM-34)*cl(F);if(F>.02)g+=path(arc(rr,-span,span),A,1,.26*F);
  S.items.forEach(function(it,k){var v=val('w'+k);if(v<.02)return;var vc=cl(v),hot=S.sel===k,sv=hot?val('sel'):0,od=(hot||S.sel<0)?1:1-.65*cl(val('sel'));
    var ang=it.a*v,r=34+(RM-34)*v+7*sv,kr=G.KR*(.35+.65*vc),o=cl(v*3)*od,c=P(r,ang),lit=hot&&sv>.5;
    g+='<g opacity="'+f(o)+'">'+disc(c,kr,PANEL,1,A,.42,1)+(hot?disc(c,kr,A,cl(sv)):'')+path(arcAt(c,kr,ang-42,ang+42),A,1.8,.95)+'</g>';
    var co=cl((v-.55)/.35)*od;
    if(it.i)t+=ico(it.i,c[0],c[1],22,lit?LIT:INK,co);
    else t+=tx(c[0],c[1]+3,it.n,15,lit?LIT:INK,co,'middle',600)+tx(c[0],c[1]+13.5,'kcal',7.5,lit?LIT:DIM,co);
    t+=tx(c[0],c[1]+kr+15,it.i?it.l:it.t,10.5,INK,.72*co,'middle',500,.3)});
  return {g:g,t:t}}

function keysRibbon(F){var g='',t='',rb=val('rb'),rbc=cl(rb); // C: one band, the keys are marks on it
  if(rb<.02)return {g:g,t:t};
  var r=34+(RM-34)*rb,hw=G.BW*(.3+.7*rbc),E=Math.max(.5,val('ext')*e(rbc)),o=cl(rb*3);
  g+='<path d="'+ribbon(r,hw,E)+'" fill="'+PANEL+'" fill-opacity=".94" stroke="'+A+'" stroke-opacity=".3" stroke-width="1" opacity="'+f(o)+'"/>';
  // the scale lives on the band's outer edge; a long tick stands over every key, so nothing else has to divide them
  var rot=val('rot');for(var i=0;i<90;i++){var an=((i*4+rot)%360+540)%360-180,ab=Math.abs(an);if(ab>E-3)continue;var maj=i%9===0;
    g+=ln(r+hw-(maj?10:7),r+hw-4,an,A,1,(maj?.9:.3)*o*cl((E-3-ab)/10))}
  g+=path(arc(r+hw,-E,E),A,1.5,.9*o*cl(rb*2-1));
  S.items.forEach(function(it,k){var v=val('w'+k);if(v<.02)return;var hot=S.sel===k,sv=hot?val('sel'):0,od=(hot||S.sel<0)?1:1-.65*cl(val('sel'));
    var c=P(r-4+7*sv,it.a),co=cl((v-.45)/.4)*od*cl((E-Math.abs(it.a)+12)/10),lit=hot&&sv>.5;
    if(hot)g+=disc(c,23*cl(sv),A,cl(sv)); // the lit node
    t+=keyText(it,c,lit?LIT:INK,lit?LIT:DIM,co,it.i?-1:0)});
  return {g:g,t:t}}

function draw(){var T=S.T,on=cl(val('on')),s='',g='',t2='',i;
  var b2=document.getElementById('bg2');if(b2)b2.style.opacity=f(cl(val('nav')));
  s+='<rect width="'+W+'" height="'+H+'" fill="#000" opacity="'+f(.86*on)+'"/>';
  var F=0;S.items.forEach(function(it,k){F=Math.max(F,cl(val('w'+k)))});
  // the tick ring outside the keys (not in C, where the scale is on the band); it turns one notch per level
  if(F>.02&&G.tick[0]){g+=ticks(G.tick[0],G.tick[1],G.tick[2],F,100*e(F),val('rot')+T*.004,OPT==='before'?1:.8)}
  if(F>.02&&S.sel>=0&&OPT!=='c'){var sa=S.items[S.sel].a;g+=path(arc(G.sel,sa-9,sa+9),A,2,cl(val('sel'))*F)}
  var K=OPT==='b'?keysOrbit(F):OPT==='c'?keysRibbon(F):keysSector(F);g+=K.g;t2+=K.t;
  // level 2: the chosen key docks above the core (tap it to go back)
  var cv=val('crumb');if(cv>.02){var cp=P(54,0);g+='<circle cx="'+cp[0]+'" cy="'+f(cp[1])+'" r="'+f(13*cv)+'" fill="'+PANEL+'" stroke="'+A+'" stroke-opacity=".8"/>';t2+=ico(S.crumbI,cp[0],cp[1],14*cl(cv),INK,cl(cv))}
  // listening: the approved soft ring that moves with the voice
  var l=cl(val('l'));if(l>.01){var en=.25+.75*cl(val('env'));g+=path(wave(40,5.5*l*en,T/260),A,1.6,.95*l)+path(wave(47,4*l*en,-T/340+2),A,1,.4*l)}
  var cf=cl(val('cf'));if(cf>.01){g+='<circle cx="'+CX+'" cy="'+CY+'" r="40" fill="none" stroke="'+A+'" stroke-width="1" opacity="'+f(.22*cf)+'"/>'+path(arc(40,0,S.mode==='confirm'?Math.max(.5,360*cl(S.since/1000)):360),A,2,.95*cf)}
  var k2=cl(val('k'));if(k2>.01){var a1=T*.36;g+='<circle cx="'+CX+'" cy="'+CY+'" r="40" fill="none" stroke="'+A+'" stroke-width="1" opacity="'+f(.22*k2)+'"/>'+path(arc(40,a1,a1+96),A,2,.95*k2)}
  // transcript, live
  var tr=cl(val('tr'));if(tr>.01){var sy=S.say,n=S.mode==='listen'?Math.floor(cl(S.since/sy.dur)*sy.text.length+.001):sy.text.length,live=S.mode==='listen'&&n<sy.text.length;
    var a=sy.text.slice(0,Math.max(0,n-(live?2:0))),b=sy.text.slice(a.length,n);
    t2+='<text x="'+CX+'" y="'+f(CY-145-10*(1-tr))+'" font-size="22" font-weight="600" text-anchor="middle" opacity="'+f(tr)+'" xml:space="preserve"><tspan fill="'+INK+'">'+a+'</tspan><tspan fill="'+A+'" fill-opacity=".75">'+b+'</tspan></text>';
    t2+=tx(CX,CY-117,S.mode==='listen'?L('说完停一下，自动发送','Pause when done; it sends itself'):L('点文字可修改','Tap the words to edit'),11,DIM,.9*tr,'middle',500,.3);
    g+='<circle cx="'+(CX-80)+'" cy="'+CY+'" r="17" fill="'+PANEL+'" stroke="#fff" stroke-opacity=".25" opacity="'+f(tr)+'"/>';t2+=ico('x',CX-80,CY,15,INK,tr)}
  // reply card: a line leaves the core, widens, unfolds upwards
  var cw=cl(val('cardw'));if(cw>.02){var HH=CARDH[S.kind],h=Math.max(0,HH*val('card')),w=CW*cw,x=CX-w/2,y1=CY-99,y0=y1-h,R=Math.min(G.cardR,h/2,w/2),ch=Math.min(G.chamfer,h/2);
    g+=ln(37,99,0,A,1,.7*cw)+'<circle cx="'+CX+'" cy="'+y1+'" r="2.2" fill="'+A+'" opacity="'+f(cw)+'"/>';
    if(ch)g+='<path d="M'+f(x)+' '+f(y0+6)+'Q'+f(x)+' '+f(y0)+' '+f(x+6)+' '+f(y0)+'H'+f(x+w-ch)+'L'+f(x+w)+' '+f(y0+ch)+'V'+f(y1-6)+'Q'+f(x+w)+' '+f(y1)+' '+f(x+w-6)+' '+f(y1)+'H'+f(x+6)+'Q'+f(x)+' '+f(y1)+' '+f(x)+' '+f(y1-6)+'Z" fill="'+PANEL+'" fill-opacity=".97" stroke="'+A+'" stroke-opacity=".5" stroke-width="1" stroke-linejoin="round"/>';
    else g+='<rect x="'+f(x)+'" y="'+f(y0)+'" width="'+f(w)+'" height="'+f(h)+'" rx="'+f(R)+'" fill="'+PANEL+'" fill-opacity=".97" stroke="'+A+'" stroke-opacity=".3" stroke-width="1"/>';
    var lx=x+(ch?12:R+2);g+='<line x1="'+f(lx)+'" x2="'+f(lx+44*cw)+'" y1="'+f(y0)+'" y2="'+f(y0)+'" stroke="'+A+'" stroke-width="'+(ch?2:1.5)+'" stroke-linecap="round"/>';
    var x0=CX-CW/2;if(h>8)t2+='<clipPath id="cc"><rect x="'+x0+'" y="'+f(y0)+'" width="'+CW+'" height="'+f(h)+'"/></clipPath><g clip-path="url(#cc)" opacity="'+f(cl(cw*3-2))+'">'+cardBody(S.kind,x0,y0,h)+'</g>'}
  // the key: tick ring, three segments, inverted triangle with a lit inner triangle (decided; unchanged)
  var c='',pu=(T-S.pulse)/520;if(pu>0&&pu<1)c+='<circle cx="'+CX+'" cy="'+CY+'" r="'+f(29+36*e(pu))+'" fill="none" stroke="'+A+'" stroke-width="1.3" opacity="'+f(.75*(1-pu)*(1-pu))+'"/>';
  var sc=1+.5*val('pr');
  c+='<g transform="translate('+CX+' '+CY+') scale('+f(sc)+') translate('+(-CX)+' '+(-CY)+')"><circle cx="'+CX+'" cy="'+CY+'" r="29" fill="#08090B" stroke="'+A+'" stroke-width="1" stroke-opacity="'+f(.5+.45*on)+'"/>';
  for(i=0;i<24;i++)c+=ln(22,i%6===0?26.5:25,S.ra+i*15,A,1,i%6===0?.95:.5);
  for(i=0;i<3;i++)c+=path(arc(17.5,S.rb+i*120,S.rb+i*120+84),A,1.5,.85);
  c+='<path d="'+tri(CX,CY,11.5)+'" fill="none" stroke="'+INK+'" stroke-width="1.6" stroke-linejoin="round"/><path d="'+tri(CX,CY,4.6)+'" fill="'+A+'" stroke="'+A+'" stroke-width="1" stroke-linejoin="round" opacity="'+f(.6+.4*Math.sin(T/700))+'"/></g>';
  s+='<g style="filter:drop-shadow(0 0 6px '+A+(OPT==='before'?'66':'4D')+')">'+g+'</g>'+t2+'<g style="filter:drop-shadow(0 0 5px '+A+'99) drop-shadow(0 0 14px '+A+'44)">'+c+'</g>';
  // film only: scene caption and the finger
  if(DEMO&&!STILL){if(S.cap)s+='<rect x="'+(CX-60)+'" y="100" width="120" height="26" rx="13" fill="#000" fill-opacity=".55" stroke="#fff" stroke-opacity=".14"/>'+tx(CX,117.5,S.cap,12,INK,.85,'middle',500,1);
    S.taps.forEach(function(p){var d=T-p[0];if(d<-300||d>320)return;var o=d<0?.42*e((d+300)/220):.42*(1-d/320),r=d<0?22-6*e((d+300)/300):16+10*e(d/320);
      s+='<circle cx="'+f(p[1])+'" cy="'+f(p[2])+'" r="'+f(r)+'" fill="#fff" fill-opacity="'+f(o*.5)+'" stroke="#fff" stroke-opacity="'+f(o)+'" stroke-width="1.2"/>'})}
  document.getElementById('hud').innerHTML=s}

function set(m){S.mode=m;S.since=0}
function openMenu(items,lv){S.items=items;S.lv=lv;S.fanT=1;S.fanSince=0;S.sel=-1;V.sel={x:0,v:0};for(var i=0;i<5;i++)V['w'+i]={x:0,v:0};
  S.ext=Math.abs(items[0].a)+2;V.ext={x:S.ext,v:0};set('menu')}
function closeAll(){S.fanT=0;S.pend=null;set('idle')}
function act(it){var a=(it.act||'close').split(':');
  if(a[0]==='sub'){S.fanT=0;S.pend={at:S.T+300,fn:function(){S.rotT+=36;S.crumbI=it.i;openMenu(a[1]==='meal'?MEAL:TRAIN,2)}}}
  else if(a[0]==='voice'){S.fanT=0;S.say=SAY[a[1]];set('listen')}
  else if(a[0]==='log'){S.fanT=0;S.kind='meal';S.tk=600;set('think')}
  else if(a[0]==='nav'){S.fanT=0;S.nav=1;set('idle')}
  else closeAll()}
/* the touch area of a key is its whole 36 degree wedge from 72 pt to 164 pt (44 pt wide at its narrowest): wider than what is drawn, in every option */
function hit(x,y){var d=Math.hypot(x-CX,y-CY);if(d<72||d>164)return -1;var an=Math.atan2(x-CX,CY-y)*180/Math.PI;
  for(var i=0;i<S.items.length;i++)if(Math.abs(an-S.items[i].a)<=18)return i;return -1}
function tap(x,y){var m=S.mode,i;
  if(Math.hypot(x-CX,y-CY)<34){V.pr=V.pr||{x:0,v:0};V.pr.v=-3;S.pulse=S.T;
    if(m==='idle')openMenu(L1,1);else if(m==='listen')set('confirm');else if(m==='confirm'){S.kind=S.say.kind;S.tk=S.say.tk;set('think')}else closeAll();return}
  if((m==='listen'||m==='confirm')&&Math.hypot(x-CX+80,y-CY)<24){closeAll();return}
  if(m==='menu'){if(S.sel>=0)return;
    if(S.lv===2&&Math.hypot(x-CX,y-(CY-54))<22){S.fanT=0;S.pend={at:S.T+280,fn:function(){S.rotT-=36;openMenu(L1,1)}};return}
    i=hit(x,y);if(i>=0){S.sel=i;(function(it){S.pend={at:S.T+260,fn:function(){act(it)}}})(S.items[i]);return}
    closeAll();return}
  if(m!=='idle')closeAll()}
function step(dt){S.T+=dt;S.since+=dt;S.fanSince+=dt;var m=S.mode;
  if(S.pend&&S.T>=S.pend.at){var fn=S.pend.fn;S.pend=null;fn();m=S.mode}
  if(m==='listen'&&S.since>S.say.dur+500)set('confirm');
  else if(m==='confirm'&&S.since>1000){S.kind=S.say.kind;S.tk=S.say.tk;set('think')}
  else if(m==='think'&&S.since>S.tk)set('card');
  m=S.mode;
  spr('on',m==='idle'?0:1,110,21,dt);
  for(var i=0;i<5;i++){var it=S.items[i],tg=it&&S.fanT&&S.fanSince>=Math.abs(it.a)/36*60?1:0;spr('w'+i,tg,S.fanT?150:300,S.fanT?15.5:32,dt)}
  spr('rb',S.fanT,S.fanT?150:300,S.fanT?17:32,dt); // C: the band is one object on one spring
  spr('sel',S.sel>=0?1:0,420,26,dt);spr('crumb',m==='menu'&&S.lv===2&&S.fanT?1:0,220,21,dt);spr('rot',S.rotT,80,13,dt);
  spr('l',m==='listen'?1:0,90,19,dt);spr('env',m==='listen'&&S.since<S.say.dur?1:0,160,25,dt);spr('cf',m==='confirm'?1:0,140,24,dt);spr('k',m==='think'?1:0,90,19,dt);
  spr('tr',m==='listen'||m==='confirm'?1:0,120,22,dt);
  spr('cardw',m==='card'?1:0,m==='card'?260:120,m==='card'?32:22,dt);spr('card',m==='card'&&val('cardw')>.6?1:0,m==='card'?120:260,m==='card'?16.5:32,dt);
  spr('nav',S.nav,36,12,dt);spr('pr',0,320,18,dt);
  var sp=1+5*cl(val('k'))+1.5*cl(val('on'))+2*cl(val('l'));S.ra+=dt*.02*sp;S.rb-=dt*.035*sp}

(function boot(){
  var st=document.createElement('style');st.textContent='*{box-sizing:border-box;margin:0;padding:0}html,body{width:'+W+'px;height:'+H+'px;overflow:hidden;background:#000;font-family:system-ui;-webkit-font-smoothing:antialiased}'+
    'img.bg{position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px;object-fit:cover;object-position:top}#hud{position:absolute;left:0;top:0;width:'+W+'px;height:'+H+'px;touch-action:manipulation;-webkit-tap-highlight-color:transparent}text{font-family:system-ui;user-select:none}';
  document.head.appendChild(st);
  document.body.innerHTML='<img class="bg" src="../01-today-'+(THEME==='light'?'light':'dark')+'.png" alt=""><img class="bg" id="bg2" src="../11-training-dark.png" alt="" style="opacity:0"><svg id="hud" viewBox="0 0 '+W+' '+H+'" xmlns="http://www.w3.org/2000/svg"></svg>';
  var w=function(a){return P(RM,a)},out=[CX,250];
  // the film: 1 voice question -> reply card; 2 记一餐 -> second ring -> a saved meal -> logged card; 3 记训练 -> second ring -> the training screen
  var ev=[[500,CX,CY],[1800].concat(w(0)),[8600].concat(out),[9500,CX,CY],[10600].concat(w(-72)),[12400].concat(w(0)),[15400].concat(out),[16300,CX,CY],[17400].concat(w(-36)),[19200].concat(w(-36))],k=0,now=0,dt=1000/120;
  function to(t){for(;now<t;now+=dt){while(k<ev.length&&now>=ev[k][0]){tap(ev[k][1],ev[k][2]);k++}step(dt)}
    S.cap=t<9100?L('1 · 说话','1 · Talk'):t<15900?L('2 · 记一餐','2 · Meal'):L('3 · 记训练','3 · Workout');draw()}
  if(DEMO||REC){S.taps=ev;DEMO=true;window.frame=to;to(+q.get('t')||0)}
  else{var last=performance.now();(function loop(n){var d=Math.min(50,n-last);last=n;while(d>0){step(Math.min(8,d));d-=8}draw();requestAnimationFrame(loop)})(last);
    var fit=function(){var kk=Math.min(1,innerHeight/H,innerWidth/W),h=document.documentElement.style,b=document.body.style;
      h.width='100%';h.height='100%';b.position='absolute';b.transformOrigin='0 0';b.transform='scale('+kk+')';
      b.left=Math.max(0,(innerWidth-W*kk)/2)+'px';b.top=Math.max(0,(innerHeight-H*kk)/2)+'px'};
    fit();addEventListener('resize',fit);
    var hud=document.getElementById('hud');
    document.addEventListener('pointerdown',function(evn){var r=hud.getBoundingClientRect();S.nav=0;tap((evn.clientX-r.left)*W/r.width,(evn.clientY-r.top)*H/r.height)})}
})();
