/* Watch2Whoop watchOS mockups: drawing kit + every screen.
   Each screen is a pure function that returns SVG in the 46 mm coordinate system (208 x 248 pt). The 42 mm watch
   (187 x 223 pt) has the same aspect ratio, so it is the same drawing at 0.899; the smallest text (11 pt) is 9.9 pt there.
   Tokens are the 2.0.1 tokens (docs/design/2.0.1/kit.css on the design/ui-2.0.1 branch); the ice accent and the core
   are Jarvis only. Icons: Lucide 1.61.0 (lucide.js). Geometry only, no third-party artwork. All data is synthetic. */
var C={bg:'#000000',surface:'#121214',surface2:'#1D1D20',text:'#F4F4F6',text2:'#8D8D94',text3:'#55555B',
  good:'#72BF8E',mid:'#D3AE63',bad:'#DE7F69',sleep:'#8D93E4',train:'#5FB3BD',ice:'#8FDCFF',panel:'#0B0E12',
  track:'rgba(244,244,246,0.16)',todo:'rgba(244,244,246,0.26)',tick:'rgba(141,141,148,0.50)',tickM:'rgba(244,244,246,0.72)',
  bandLine:'rgba(141,141,148,0.62)',hair:'rgba(255,255,255,0.10)'};
var W46=[208,248],W42=[187,223];

function f(n){return (Math.round(n*100)/100).toString()}
function cl(x){return x<0?0:x>1?1:x}
function ez(x){x=cl(x);return 1-Math.pow(1-x,3)}
function io(x){x=cl(x);return x<.5?4*x*x*x:1-Math.pow(-2*x+2,3)/2}
function P(cx,cy,r,deg){var a=deg*Math.PI/180;return [cx+r*Math.sin(a),cy-r*Math.cos(a)]}
function arc(cx,cy,r,a0,a1){
  if(a1-a0>=359.9)return 'M'+f(cx)+' '+f(cy-r)+'A'+f(r)+' '+f(r)+' 0 1 1 '+f(cx-0.01)+' '+f(cy-r);
  var p=P(cx,cy,r,a0),b=P(cx,cy,r,a1);return 'M'+f(p[0])+' '+f(p[1])+'A'+f(r)+' '+f(r)+' 0 '+((a1-a0)>180?1:0)+' 1 '+f(b[0])+' '+f(b[1])}
function st(d,c,w,o){return '<path d="'+d+'" fill="none" stroke="'+c+'" stroke-width="'+w+'" stroke-linecap="round" stroke-linejoin="round" opacity="'+f(o==null?1:cl(o))+'"/>'}
function ln(x1,y1,x2,y2,c,w,o){return '<line x1="'+f(x1)+'" y1="'+f(y1)+'" x2="'+f(x2)+'" y2="'+f(y2)+'" stroke="'+c+'" stroke-width="'+(w||1)+'" stroke-linecap="round" opacity="'+f(o==null?1:cl(o))+'"/>'}
function rr(x,y,w,h,r,fill,more){return '<rect x="'+f(x)+'" y="'+f(y)+'" width="'+f(Math.max(0,w))+'" height="'+f(Math.max(0,h))+'" rx="'+f(r)+'" fill="'+fill+'" '+(more||'')+'/>'}
function ci(x,y,r,fill,more){return '<circle cx="'+f(x)+'" cy="'+f(y)+'" r="'+f(Math.max(0,r))+'" fill="'+fill+'" '+(more||'')+'/>'}
/* text: o = {c colour, w weight, a anchor, ls letter-spacing, o opacity}; s may hold <tspan>s (see sp) */
function tx(x,y,s,size,o){o=o||{};return '<text x="'+f(x)+'" y="'+f(y)+'" font-size="'+size+'" fill="'+(o.c||C.text)+'" font-weight="'+(o.w||500)+'" text-anchor="'+(o.a||'start')+'" letter-spacing="'+(o.ls||0)+'" opacity="'+f(o.o==null?1:cl(o.o))+'">'+s+'</text>'}
function sp(s,size,c,w,dx){return '<tspan font-size="'+size+'" fill="'+c+'" font-weight="'+(w||500)+'"'+(dx?' dx="'+dx+'"':'')+'>'+s+'</tspan>'}
function ico(n,x,y,size,c,o,sw){var ic=window.LUCIDE[n];if(!ic)return tx(x,y,'?'+n,8,{c:'red'});
  return '<g transform="translate('+f(x-size/2)+' '+f(y-size/2)+') scale('+f(size/24)+')" fill="'+(ic.fill?c:'none')+'" stroke="'+c+'" stroke-width="'+(sw||1.9)+'" stroke-linecap="round" stroke-linejoin="round" opacity="'+f(o==null?1:cl(o))+'">'+ic.d+'</g>'}
function tri(cx,cy,r){var d='';[60,180,300].forEach(function(g,i){var p=P(cx,cy,r,g);d+=(i?'L':'M')+f(p[0])+' '+f(p[1])});return d+'Z'}

/* ---------- system row: app title (or a sync mark) left, the clock right ---------- */
function status(title,o){o=o||{};return (title?tx(16,25,title,15,{c:o.c||C.text,w:600}):'')+tx(192,25,o.time||'9:41',15,{a:'end',w:600})}
/* sync mark: icon shape carries the state, the text is an age or a count, never a clock time (same grammar as the phone's capsule) */
var SYNC={ok:['circle-check',C.text2],busy:['refresh-cw',C.text],stale:['refresh-cw',C.mid],away:['unlink',C.mid],off:['wifi-off',C.mid],fail:['circle-alert',C.bad]};
function sync(kind,label,x,y,rot){var k=SYNC[kind],cx=(x==null?22:x),cy=(y==null?20:y);
  return '<g transform="rotate('+f(rot||0)+' '+cx+' '+cy+')">'+ico(k[0],cx,cy,12,k[1])+'</g>'+tx(cx+9,cy+4.5,label,12.5,{c:kind==='ok'||kind==='busy'?C.text2:k[1],w:500})}
/* buttons: one primary per screen, full width, in the bottom corner radius */
function btn(label,o){o=o||{};var h=o.h||46,y=o.y==null?240-h:o.y,x=o.x==null?8:o.x,w=o.w||192,pr=cl(o.press||0),k=1-.04*pr;
  var fill=o.kind==='quiet'?C.surface2:o.kind==='bad'?'rgba(222,127,105,0.16)':o.kind==='ice'?'rgba(143,220,255,0.10)':C.text;
  var ink=o.kind==='quiet'?C.text:o.kind==='bad'?C.bad:o.kind==='ice'?C.ice:'#000';
  var cx=x+w/2,cy=y+h/2,s='<g transform="translate('+f(cx)+' '+f(cy)+') scale('+f(k)+') translate('+f(-cx)+' '+f(-cy)+')" opacity="'+f(o.o==null?1:o.o)+'">';
  s+=rr(x,y,w,h,h/2,fill,o.kind==='ice'?'stroke="'+C.ice+'" stroke-opacity=".8"':'')+(pr>0?rr(x,y,w,h,h/2,'#000','opacity="'+f(.18*pr)+'"'):'');
  if(o.icon){s+=ico(o.icon,cx-label.length*8.5-5,cy,15,ink,1,2.2)+tx(cx+8,cy+6,label,17,{c:ink,w:600,a:'middle'})}
  else s+=tx(cx,cy+6,label,17,{c:ink,w:600,a:'middle'});
  return s+'</g>'}
/* set progress of one exercise: n pills (done = train, current = ink, to do = track) */
function segs(x,y,w,n,done,cur){var s='',g=3,pw=(w-(n-1)*g)/n;for(var i=0;i<n;i++)s+=rr(x+i*(pw+g),y,pw,3,1.5,i<done?C.train:i===cur?C.text:C.todo);return s}
/* set ring of the whole session: one arc per planned set, a wider gap between exercises (the phone's set ring) */
var PLAN=[4,4,3,3,2,2];
function setRing(cx,cy,r,done,cur,sw,prog){var tot=0,i,j,s='';PLAN.forEach(function(n){tot+=n});
  var each=(360-(tot-PLAN.length)*3-PLAN.length*9)/tot,a=4.5,k=0;
  for(i=0;i<PLAN.length;i++){for(j=0;j<PLAN[i];j++){var on=k<done,c=on?C.train:k===cur?C.text:C.todo;
      s+=st(arc(cx,cy,r,a,a+each),c,sw||4,prog!=null&&on?cl(prog*tot-k+1):1);a+=each+3;k++}a+=6}
  return s}
/* vertical tick ruler beside the Digital Crown: shown only while the crown changes a value */
function crown(y,off,o){var s='',i;for(i=-4;i<=4;i++){var yy=y+i*7+(off||0)*7;if(Math.abs(yy-y)>30)continue;var m=Math.abs(yy-y)<3.5;
    s+=ln(m?194:198,yy,203,yy,m?C.train:C.tick,m?2:1,(o==null?1:o)*(1-Math.abs(yy-y)/34))}return s}
/* Jarvis core: tick ring, three segments, inverted triangle with a lit inner triangle (the approved key).
   live = the cockpit version in ice with a soft glow (only while Jarvis is working); else the quiet key in ink. */
function core(cx,cy,size,o){o=o||{};var k=size/58,A=C.ice,i,s='',live=!!o.live,ra=o.ra||0,rb=o.rb||0;
  s+=ci(cx,cy,29*k,'#08090B','stroke="'+(live?A:'rgba(255,255,255,0.22)')+'" stroke-width="1" stroke-opacity="'+(live?.9:1)+'"');
  for(i=0;i<24;i++){var a=P(cx,cy,22*k,ra+i*15),b=P(cx,cy,(i%6===0?26.5:25)*k,ra+i*15);s+=ln(a[0],a[1],b[0],b[1],live?A:C.text2,1,live?(i%6===0?.95:.5):(i%6===0?.9:.55))}
  for(i=0;i<3;i++)s+=st(arc(cx,cy,17.5*k,rb+i*120+12,rb+i*120+96),live?A:C.text,1.5*Math.max(.8,k),.9);
  s+='<path d="'+tri(cx,cy,11.5*k)+'" fill="none" stroke="'+C.text+'" stroke-width="'+f(1.6*Math.max(.8,k))+'" stroke-linejoin="round"/>';
  s+='<path d="'+tri(cx,cy,4.6*k)+'" fill="'+A+'" stroke="'+A+'" stroke-width="1" stroke-linejoin="round" opacity="'+f(o.dim?0.35:(o.lit==null?1:o.lit))+'"/>';
  return '<g style="filter:drop-shadow(0 0 '+(live?5:2.5)+'px rgba(143,220,255,'+(o.dim?0:live?.6:.35)+'))">'+s+'</g>'}
function wave(cx,cy,r0,amp,ph){var d='';for(var i=0;i<=120;i++){var th=i*3,x=th*Math.PI/180,r=r0+amp*(Math.sin(3*x+ph)*Math.sin(ph*.7)+.6*Math.sin(5*x-ph*1.4)),p=P(cx,cy,r,th);d+=(i?'L':'M')+f(p[0])+' '+f(p[1])}return d+'Z'}
function glow(s){return '<g style="filter:drop-shadow(0 0 5px rgba(143,220,255,.45))">'+s+'</g>'}
function tagW(s){var w=14;for(var i=0;i<s.length;i++)w+=s.charCodeAt(i)>255?9.6:s[i]===' '?2.6:6;return w}
function tag(x,y,s){var w=tagW(s);return rr(x,y-11,w,15,7.5,'none','stroke="'+C.text3+'"')+tx(x+w/2,y,s,9.5,{c:C.text2,a:'middle'})}

/* =========================================== screens =========================================== */
var SC={};

/* ---- training ---- */
SC.start=function(p){p=p||{};var s=p.noSync?status(''):sync(p.sync||'ok',p.syncLabel||'2分前')+status('');
  s+=setRing(104,106,56,0,-1,4);
  s+=tx(104,90,'上肢 A',12,{c:C.text2,a:'middle'})+tx(104,128,'18',42,{w:300,a:'middle',ls:-1})+tx(104,145,'组',11,{c:C.text2,a:'middle'});
  s+=tx(104,183,'6 动作',12,{c:C.text2,a:'middle',o:0});
  return s+btn('开始',{icon:'play',press:p.press})};

SC.set=function(p){p=p||{};var name=p.name||'卧推',n=p.n||4,i=p.i==null?1:p.i,w=p.w||'60',r=p.r||'8',foc=p.focus,s=status(name);
  s+=segs(16,38,132,n,i,i)+tx(192,43,(i+1)+'/'+n,12.5,{c:C.text2,a:'end'});
  var fw=w.length>3?32:44,dim=foc?C.text2:C.text;
  s+=tx(60,108,w,fw,{w:300,a:'middle',ls:-1,c:foc==='r'?dim:C.text})+tx(60,127,'kg',11.5,{c:C.text2,a:'middle'});
  s+=tx(106,102,'×',20,{c:C.text3,a:'middle',w:300});
  s+=tx(150,108,r,44,{w:300,a:'middle',ls:-1,c:foc==='w'?dim:C.text})+tx(150,127,'次',11.5,{c:C.text2,a:'middle'});
  if(foc)s+=rr((foc==='w'?60:150)-18,134,36,2,1,C.train)+crown(100,p.crownOff||0);
  if(p.dots!=null){for(var d=0;d<PLAN.length;d++)s+=ci(104+(d-(PLAN.length-1)/2)*9,181,d===p.dots?2.4:1.8,d<p.dots?C.train:d===p.dots?C.text:C.todo);
    s+=ico('chevron-left',7,100,12,C.text3)+ico('chevron-right',201,100,12,C.text3)}
  {s+=ico('heart',22,158,13,C.text2,1,2.1)+tx(33,163.5,sp(p.hr==null?'132':p.hr,19,C.text,500)+sp('bpm',10.5,C.text2,500,3),19);
    if(p.q)s+=ico('unlink',192-String(p.q).length*7-42,158,12,C.mid)+tx(192,162.5,'待传 '+p.q,12.5,{c:C.mid,a:'end'});
    else s+=tx(192,162.5,sp('上次 ',11,C.text2)+sp(p.last||'57.5×8',12.5,C.text2,500),12,{a:'end'})}
  return s+btn('完成',{icon:'check',h:50,press:p.press})};

SC.logged=function(p){p=p||{};var u=p.u==null?1:p.u,s=status(p.name||'卧推');
  s+=segs(16,38,132,p.n||4,(p.i==null?1:p.i)+1,-1)+tx(192,43,((p.i==null?1:p.i)+1)+'/'+(p.n||4),12.5,{c:C.text2,a:'end'});
  s+=ci(104,104,30,'none','stroke="'+C.track+'" stroke-width="1.5"')+st(arc(104,104,30,0,Math.max(1,360*ez(u*1.6))),C.train,4);
  s+='<g opacity="'+f(cl(u*3-.8))+'">'+ico('check',104,104,30,C.text,1,2.2)+'</g>';
  s+=tx(104,168,sp(p.w||'60',22,C.text,500)+sp('kg',11,C.text2,500,2)+sp('×',15,C.text3,400,5)+sp(p.r||'8',22,C.text,500,5),22,{a:'middle',o:cl(u*3-1)});
  s+=tx(104,196,sp('比上次',11.5,C.text2)+sp(p.delta||'+2.5 kg',15,C.text,600,6),15,{a:'middle',o:cl(u*3-1.4)});
  return s};

SC.rest=function(p){p=p||{};var left=p.left==null?72:p.left,total=p.total||90,i=p.i==null?2:p.i,s=status(p.name||'卧推'),k,cy=101;
  s+=segs(16,38,132,p.n||4,i,-1)+tx(192,43,i+'/'+(p.n||4),12.5,{c:C.text2,a:'end'});
  for(k=0;k<36;k++){var a=P(104,cy,49.5,k*10),b=P(104,cy,k%9===0?54:52,k*10);s+=ln(a[0],a[1],b[0],b[1],k%9===0?C.tickM:C.tick,1)}
  s+=ci(104,cy,45,'none','stroke="'+C.track+'" stroke-width="1.5"');
  if(left>0)s+=st(arc(104,cy,45,0,Math.max(1,360*left/total)),C.train,4);
  var mm=Math.floor(Math.max(0,left)/60),ss=Math.floor(Math.max(0,left)%60);
  s+=tx(104,cy-17,'休息',11,{c:C.text2,a:'middle'})+tx(104,cy+14,mm+':'+(ss<10?'0':'')+ss,34,{w:300,a:'middle',ls:-.5});
  s+=tx(104,cy+30,p.next||'60 kg × 8',11,{c:C.text2,a:'middle'});
  s+=ico('heart',22,172,13,C.text2,1,2.1)+tx(33,177.5,sp(p.hr==null?'118':p.hr,19,C.text,500)+sp('bpm',10.5,C.text2,500,3),19);
  s+=ico('arrow-down',192-String(p.drop==null?14:p.drop).length*11-9,172,13,C.text2,1,2.2)+tx(192,177.5,p.drop==null?'14':p.drop,19,{w:500,a:'end'});
  if(p.q)s+=ico('unlink',99,172,11,C.mid)+tx(107,176.5,p.q,12,{c:C.mid});
  return s+btn('跳过',{kind:'quiet',h:42,press:p.press})};

SC.rir=function(p){p=p||{};var v=p.v==null?2:p.v,s=status(p.name||'卧推');
  s+=segs(16,38,132,4,4,-1)+tx(192,43,'4/4',12.5,{c:C.text2,a:'end'});
  s+=tx(104,68,'还能做',12,{c:C.text2,a:'middle'});
  s+=tx(104,122,v<0?'—':(v>=4?'4+':v),54,{w:300,a:'middle',ls:-1,c:v<0?C.text3:C.text})+(v<0?'':tx(104+(v>=4?40:24),122,'次',12,{c:C.text2}));
  for(var i=0;i<=4;i++){var x=44+i*30,on=i===v;s+=ln(x,on?140:144,x,152,on?C.train:C.tickM,on?2.5:1.2)+tx(x,168,i===4?'4+':i,11,{c:on?C.text:C.text2,a:'middle'});
    if(i<4)for(var j=1;j<3;j++)s+=ln(x+j*10,148,x+j*10,152,C.tick,1)}
  return s+crown(100,p.crownOff||0,.9)+btn('继续',{press:p.press})};

SC.sumA=function(p){p=p||{};var u=p.u==null?1:p.u,s=status('上肢 A');
  s+=setRing(104,96,48,18,-1,4,u);
  s+=tx(104,104,sp(Math.round(18*cl(u)),36,C.text,300)+sp('/18',15,C.text2,400,2),36,{a:'middle',ls:-.5})+tx(104,120,'组',11,{c:C.text2,a:'middle'});
  [['52','分','用时',38],['6,480','kg','总量',104],['121','bpm','均心率',170]].forEach(function(r){
    s+=tx(r[3],182,sp(r[0],17,C.text,500)+sp(r[1],10,C.text2,500,2),17,{a:'middle'})+tx(r[3],197,r[2],10.5,{c:C.text2,a:'middle'})});
  s+=ico('chevron-down',104,226,14,C.text3)+rr(202,96,3,56,1.5,C.track)+rr(202,96,3,26,1.5,C.text2);
  return s};
SC.sumB=function(p){p=p||{};var s=status('上肢 A'),y=36;
  function row(lbl,val){s+=tx(16,y+22,lbl,12.5,{c:C.text2})+val;s+=rr(16,y+33.5,176,1,0,C.hair);y+=34}
  row('比上次',ico('trending-up',116,y+17,13,C.text)+tx(125,y+22,'4',17,{w:500})+ico('equal',146,y+17,13,C.text2)+tx(155,y+22,'1',17,{w:500,c:C.text2})+ico('trending-down',174,y+17,13,C.text2)+tx(183,y+22,'1',17,{w:500,c:C.text2}));
  row('组间回落',ico('arrow-down',137,y+17,12,C.text2,1,2.2)+tx(192,y+22,sp('16',17,C.text,500)+sp('bpm',10,C.text2,500,2),17,{a:'end'}));
  row('休息',tx(192,y+22,sp('计划 1:30',10.5,C.text2)+sp('1:35',17,C.text,500,6),17,{a:'end'}));
  row('末组余力',tx(192,y+22,sp('2',17,C.text,500)+sp('次',10,C.text2,500,2),17,{a:'end'}));
  s+=rr(202,96,3,56,1.5,C.track)+rr(202,126,3,26,1.5,C.text2);
  return s+btn('完成',{icon:'check',press:p.press})};

/* ---- voice: one key, one channel (a meal, a set or a question) ---- */
SC.listen=function(p){p=p||{};var T=p.T==null?900:p.T,en=p.env==null?.8:p.env,cx=p.cx==null?104:p.cx,cy=p.cy==null?116:p.cy,sz=p.size||64,k=sz/64,s='';
  s+=ico('mic',22,20,12,C.ice)+tx(31,24.5,p.sec||'0:02',12.5,{c:C.text2})+tx(192,25,'9:41',15,{a:'end',w:600});
  s+=glow(st(wave(cx,cy,45*k,(1.2+5*en)*k,T/260),C.ice,1.6,.95)+st(wave(cx,cy,53*k,(.8+3.6*en)*k,-T/340+2),C.ice,1,.4));
  s+=core(cx,cy,sz,{live:1,ra:T*.06,rb:-T*.1});
  s+=ico('chevron-up',104,40,13,C.text3)+tx(104,206,'松手发送',12.5,{c:C.text2,a:'middle',o:p.hint==null?1:p.hint})+tx(104,224,'上滑取消',11,{c:C.text3,a:'middle',o:p.hint==null?1:p.hint});
  return s};
SC.confirm=function(p){p=p||{};var u=p.u==null?.45:p.u,L=p.lines||['午饭一碗牛肉面','加一个蛋'],s=status(''),T=p.T||0;
  L.forEach(function(t,i){s+=tx(104,(L.length>1?76:90)+i*27,t,21,{w:600,a:'middle',o:p.to==null?1:p.to})});
  s+=glow(ci(104,180,33,'none','stroke="'+C.ice+'" stroke-opacity=".22"')+st(arc(104,180,33,0,Math.max(1,360*u)),C.ice,2,.95));
  s+=core(104,180,48,{live:1,ra:T*.03,rb:-T*.05});
  s+=ci(40,180,17,C.panel,'stroke="rgba(255,255,255,.25)"')+ico('x',40,180,15,C.text);
  return s};
SC.think=function(p){p=p||{};var T=p.T==null?700:p.T,a=T*.36,s=status('');
  if(p.q)s+=tx(104,52,p.q,13,{c:C.text2,a:'middle'});
  s+=glow(ci(104,124,45,'none','stroke="'+C.ice+'" stroke-opacity=".22"')+st(arc(104,124,45,a,a+96),C.ice,2,.95));
  return s+core(104,124,64,{live:1,ra:T*.14,rb:-T*.24})};
/* reply cards: a line leaves the core and unfolds; one graphic, a few numbers, one action */
function cardTop(){return rr(16,34,44,2,1,C.ice)}
SC.meal=function(p){p=p||{};var u=p.u==null?1:p.u,m=p.m||{t:'牛肉面 + 蛋',k:'620',pr:'34',a:560,b:620},s=status('');
  function rv(y){return cl((u*230-y)/26)}
  s+=cardTop()+ico('circle-check',23,53,13,C.good,rv(50))+tx(33,57.5,'已记到午餐',12.5,{c:C.text2,o:rv(50)});
  s+=tx(16,86,m.t,18,{w:600,o:rv(86)});
  s+=tx(16,134,sp(m.k,42,C.text,300)+sp('kcal',11.5,C.text2,500,5),42,{o:rv(130)});
  s+=tx(192,134,sp('蛋白',11,C.text2)+sp(m.pr,15,C.text,600,5)+sp('g',10.5,C.text2,500,2),15,{a:'end',o:rv(134)});
  var bw=176,a0=bw*m.a/2300,a1=bw*m.b/2300*rv(170);
  s+=rr(16,150,bw,4,2,C.track,'opacity="'+f(rv(150))+'"')+rr(16,150,a0,4,2,'rgba(244,244,246,.55)','opacity="'+f(rv(150))+'"')+rr(16+a0+2,150,a1,4,2,C.ice);
  s+=tx(16,172,sp('今天 ',10.5,C.text2)+sp((m.a+m.b).toLocaleString('en-US'),11.5,C.text,500)+sp(' / 2,300 kcal',10.5,C.text2),11,{o:rv(172)});
  return s+btn('撤销',{kind:'quiet',icon:'undo-2',h:42,o:rv(206),press:p.press})};
SC.undo=function(){var s=status('');
  s+=tx(104,70,'撤销这一餐？',16,{w:600,a:'middle'})+tx(104,92,sp('牛肉面 + 蛋 ',12,C.text2)+sp('620 kcal',12,C.text2),12,{a:'middle'});
  return s+btn('撤销',{kind:'bad',y:116,x:12,w:184,h:44})+btn('保留',{kind:'quiet',y:166,x:12,w:184,h:44})};
SC.quick=function(p){p=p||{};var s=status('快记'),T=[['soup','燕麦碗','420 kcal'],['soup','鸡胸饭','570 kcal'],['soup','酸奶','150 kcal'],['droplet','水','+250 ml']];
  T.forEach(function(t,i){var x=8+(i%2)*98,y=36+Math.floor(i/2)*102,hot=p.hot===i,u=hot?cl(p.hu==null?1:p.hu):0,r=i<2?22:22;
    s+='<path d="M'+f(x+22)+' '+y+'H'+f(x+72)+'Q'+f(x+94)+' '+y+' '+f(x+94)+' '+f(y+22)+'V'+f(y+(i<2?76:54))+'Q'+f(x+94)+' '+f(y+98)+' '+f(x+(i===3?50:72))+' '+f(y+98)+'H'+f(x+(i===2?44:22))+'Q'+x+' '+f(y+98)+' '+x+' '+f(y+(i<2?76:54))+'V'+f(y+22)+'Q'+x+' '+y+' '+f(x+22)+' '+y+'Z" fill="'+C.surface+'"/>';
    if(hot)s+=rr(x,y,94,98,22,C.text,'opacity="'+f(.14*u)+'"');
    s+=ico(hot&&u>.5?'check':t[0],x+20,y+24,15,hot&&u>.5?C.good:C.text2)+tx(x+13,y+62,t[1],16,{w:600})+tx(x+13,y+80,t[2],11,{c:C.text2})});
  return s};
SC.answer=function(p){p=p||{};var u=p.u==null?1:p.u,s=status('');function rv(y){return cl((u*230-y)/26)}
  s+=cardTop()+'<path d="'+tri(21,52,5)+'" fill="none" stroke="'+C.ice+'" stroke-width="1.3" stroke-linejoin="round" opacity="'+f(rv(50))+'"/>'+tx(31,56.5,'今天适合练腿吗',11.5,{c:C.text2,o:rv(50)});
  s+=tx(16,94,'可以练',27,{w:600,o:rv(90)})+tx(16,113,'恢复够 · 可上强度',12,{c:C.text2,o:rv(110)});
  var c=2*Math.PI*22;s+=ci(166,86,22,'none','stroke="'+C.track+'" stroke-width="3.5"')+'<circle cx="166" cy="86" r="22" fill="none" stroke="'+C.good+'" stroke-width="3.5" stroke-linecap="round" stroke-dasharray="'+f(c*.82*rv(110))+' '+f(c)+'" transform="rotate(-90 166 86)"/>'+tx(166,91.5,'82',16,{w:600,a:'middle',o:rv(90)});
  s+=rr(16,126,176,1,0,C.hair);
  [['88','睡眠',16],['2.8','负荷',76],['3天','上次练腿',132]].forEach(function(r){s+=tx(r[2],149,r[0],16,{w:600,o:rv(146)})+tx(r[2],163,r[1],10.5,{c:C.text2,o:rv(160)})});
  s+='<g opacity="'+f(rv(184))+'">'+tag(16,185,'AI 生成')+tag(16+tagW('AI 生成')+6,185,'非诊断')+'</g>';
  return s+btn('开始训练',{kind:'ice',h:40,x:11,w:186,y:198,o:rv(206)})};
SC.more=function(){var s=status('');
  s+=cardTop()+'<path d="'+tri(21,52,5)+'" fill="none" stroke="'+C.ice+'" stroke-width="1.3" stroke-linejoin="round"/>'+tx(31,56.5,'这周练得怎么样',11.5,{c:C.text2});
  s+=tx(16,94,'4 / 5 天',27,{w:600});for(var i=0;i<7;i++)s+=rr(16+i*26,108,20,4,2,i<4?C.train:i===4?C.todo:C.track);
  s+=tx(16,134,'负荷比平常 +6',12,{c:C.text2});
  s+=rr(16,148,176,1,0,C.hair)+'<g>'+tag(16,170,'AI 生成')+'</g>';
  return s+btn('在手机上继续',{kind:'quiet',h:42,icon:'smartphone'})};

/* ---- today at a glance ---- */
SC.today=function(p){p=p||{};var stale=p.stale,v=p.v==null?82:p.v,s=sync(p.sync||'ok',p.syncLabel||'2分前',null,null,p.rot)+status(''),i;
  var cx=104,cy=94,R=57;
  for(i=0;i<50;i++){var z=i===0||i===17||i===33,a=P(cx,cy,R-(z?11:8.5),i*7.2),b=P(cx,cy,R-(z?4:5.5),i*7.2);s+=ln(a[0],a[1],b[0],b[1],z?C.tickM:C.tick,z?1.5:1)}
  s+=ci(cx,cy,R,'none','stroke="'+C.track+'" stroke-width="1.5"')+st(arc(cx,cy,R-3.5,58*3.6,74*3.6),C.bandLine,2);
  if(v!=null&&v!=='')s+=st(arc(cx,cy,R,0,Math.max(1,3.6*v*(p.u==null?1:p.u))),stale?C.text2:(v<34?C.bad:v<67?C.mid:C.good),4,stale?.6:1);
  s+=tx(cx,cy-21,'恢复',10.5,{c:C.text2,a:'middle',ls:2})+tx(cx,cy+15,v===''?'—':v,42,{w:300,a:'middle',ls:-1.5,c:stale?C.text2:C.text})+(v===''?'':tx(cx+25,cy+13,'%',12,{c:C.text2}));
  s+=tx(cx,cy+32,p.verdict||'可上强度',11.5,{a:'middle',c:stale?C.text2:C.text});
  [['睡眠','88',88/100,C.sleep,16],['负荷','2.8',2.8/21,C.train,112]].forEach(function(r){
    s+=tx(r[4],174,r[0],10.5,{c:C.text2})+tx(r[4]+80,177,r[1],19,{w:500,a:'end',c:stale?C.text2:C.text})+rr(r[4],184,80,1.5,.75,C.track)+rr(r[4],182.75,Math.max(4,80*r[2]),4,2,stale?C.text2:r[3])});
  if(p.rest){s+=rr(12,202,134,36,18,C.surface)+ico('soup',31,220,15,C.text2)+tx(44,225,'记一餐',14,{w:600})}
  else s+=rr(12,202,134,36,18,C.surface)+ico('dumbbell',31,220,15,C.train)+tx(44,225,'上肢 A',14,{w:600})+ico('play',132,220,11,C.text2,1,2.2);
  s+=core(174,220,40,{dim:p.noAI,lit:p.lit});
  if(p.noAI)s+=ci(188,206,8,'#000')+ico('wifi-off',188,206,10,C.mid,1,2.2);
  return s};

/* ---- states that are mostly words: kept to one icon + one short label ---- */
SC.signin=function(){return status('')+ico('smartphone',104,96,44,C.text2,1,1.4)+tx(104,150,'在手机上登录',15,{w:600,a:'middle'})+tx(104,170,'Watch2Whoop',11.5,{c:C.text2,a:'middle'})};
SC.noplan=function(){return sync('stale','昨天')+status('')+ci(104,100,48,'none','stroke="'+C.track+'" stroke-width="1.5" stroke-dasharray="2 5"')+ico('smartphone',104,100,30,C.text2,1,1.5)+tx(104,176,'计划没同步',15,{w:600,a:'middle'})+tx(104,196,'在手机上打开一次',11.5,{c:C.text2,a:'middle'})};

/* ---- watch face with complications, Smart Stack ---- */
function compCircle(cx,cy,r,kind){var s=ci(cx,cy,r,C.surface);
  if(kind==='rec'){s+=ci(cx,cy,r-4,'none','stroke="'+C.track+'" stroke-width="3"')+st(arc(cx,cy,r-4,0,295),C.good,3)+tx(cx,cy+5.5,'82',r*.68,{w:600,a:'middle'})}
  else if(kind==='strain'){s+=st(arc(cx,cy,r-4,-130,130),C.track,3)+st(arc(cx,cy,r-4,-130,-130+260*2.8/21),C.train,3)+tx(cx,cy+4,'2.8',r*.6,{w:600,a:'middle'})+tx(cx,cy+r-5,'负荷',r*.3,{c:C.text2,a:'middle'})}
  else s+=ico(kind,cx,cy,r*.95,kind==='dumbbell'?C.train:C.text,1,1.8);
  return s}
function compRect(x,y,w,h,mode,o){var s='<g opacity="'+f(o==null?1:o)+'">'+rr(x,y,w,h,20,C.surface),m=y+h/2;
  if(mode==='today'){s+=ci(x+36,m,23,'none','stroke="'+C.track+'" stroke-width="3"')+st(arc(x+36,m,23,0,295),C.good,3)+tx(x+36,m+6,'82',18,{w:600,a:'middle'});
    [['睡眠','88',C.sleep,.88],['负荷','2.8',C.train,.133]].forEach(function(r,i){var yy=m-12+i*26;s+=tx(x+72,yy+4,r[0],10.5,{c:C.text2})+rr(x+100,yy-1,w-152,3,1.5,C.track)+rr(x+100,yy-1.5,(w-152)*r[3],4,2,r[2])+tx(x+w-14,yy+5,r[1],15,{w:600,a:'end'})})}
  else if(mode==='plan'){s+=ico('dumbbell',x+28,m,22,C.train,1,1.7)+tx(x+52,m-2,'上肢 A',16,{w:600})+tx(x+52,m+15,'18 组 · 6 动作',11,{c:C.text2})+ci(x+w-32,m,18,C.text)+ico('play',x+w-31,m,14,'#000',1,2.4)}
  else if(mode==='live'){s+=ci(x+36,m,23,'none','stroke="'+C.track+'" stroke-width="3"')+st(arc(x+36,m,23,0,288),C.train,3)+tx(x+36,m+5,'1:12',13,{w:600,a:'middle'});
    s+=tx(x+72,m-2,'卧推',16,{w:600})+tx(x+w-14,m-2,'2/4',15,{w:500,a:'end',c:C.text2})+segs(x+72,m+9,w-86,4,2,-1)+tx(x+72,m+27,sp('下一组 ',10,C.text2)+sp('60 kg × 8',11,C.text,500),11)}
  return s+'</g>'}
SC.face=function(){var s='';
  s+=tx(104,27,sp('恢复 ',12,C.text2)+sp('82%',12,C.good,600)+sp(' · 负荷 ',12,C.text2)+sp('2.8',12,C.train,600),12,{a:'middle'});
  s+=compCircle(40,68,23,'rec')+tx(194,84,'10:09',47,{w:300,a:'end',ls:-1.5});
  s+=compRect(8,100,192,72,'today');
  s+=compCircle(44,208,23,'dumbbell')+compCircle(104,208,23,'mic')+compCircle(164,208,23,'strain');
  return s};
function compCorner(q,kind){var cx=q%2?158:50,cy=q<2?50:198,a0=[285,15,195,105][q],s='',lx=q%2?176:32,ly=q<2?40:214;
  if(kind==='rec'||kind==='strain'){var v=kind==='rec'?.82:2.8/21,c=kind==='rec'?C.good:C.train;
    s+=st(arc(cx,cy,43,a0,a0+60),C.track,3.5)+st(arc(cx,cy,43,a0,a0+60*v),c,3.5)+tx(lx,ly+5,kind==='rec'?'82':'2.8',15,{w:600,a:'middle'})}
  else s+=st(arc(cx,cy,43,a0,a0+60),C.track,3.5)+ico(kind,lx,ly,17,kind==='dumbbell'?C.train:C.text,1,1.9);
  return s}
SC.face2=function(){var s=compCorner(0,'rec')+compCorner(1,'strain')+compCorner(2,'dumbbell')+compCorner(3,'mic');
  s+=tx(104,112,'10:09',56,{w:200,a:'middle',ls:-2})+tx(104,138,'周六 14日',13,{c:C.text2,a:'middle',w:600});
  s+=tx(104,178,sp('睡眠 ',11,C.text2)+sp('88',13,C.sleep,600),12,{a:'middle'});
  return s};
function stack(mode,mode2){var s=tx(16,27,'周六 14日',14,{c:C.text2,w:600})+tx(192,27,'9:41',15,{a:'end',w:600});
  s+=compRect(8,44,192,80,mode)+compRect(8,130,192,80,mode2,.55)+rr(14,216,180,40,20,C.surface,'opacity=".3"');
  return s+rr(202,70,3,44,1.5,C.track)+rr(202,70,3,16,1.5,C.text2)}
SC.stackAm=function(){return stack('today','plan')};
SC.stackPlan=function(){return stack('plan','today')};
SC.stackLive=function(){return stack('live','today')};

/* =========================================== device frame =========================================== */
/* A generic watch outline (rounded body, crown, side button): plain geometry, no vendor artwork. scale = CSS px per pt. */
function frame(inner,size,scale,o){o=o||{};var W=size[0],H=size[1],k=W/208,B=11,ox=B,oy=B,tw=W+2*B+9,th=H+2*B,r=50*k,id='c'+Math.random().toString(36).slice(2,8);
  var s='<svg xmlns="http://www.w3.org/2000/svg" width="'+f(tw*scale)+'" height="'+f(th*scale)+'" viewBox="0 0 '+f(tw)+' '+f(th)+'" style="display:block;font-family:system-ui;font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased">';
  var cy=oy+H*.3,hot=cl(o.crown||0);
  s+=rr(W+2*B-1,cy-17*k,8,34*k,3.5,hot>0?'#3a4a4e':'#2A2A2E')+rr(W+2*B-1,cy+34*k,4.5,44*k,2.2,'#232326');
  for(var i=0;i<7;i++)s+=ln(W+2*B+1.5,cy-13*k+i*4.4*k+((o.crownRot||0)%1)*4.4*k,W+2*B+6,cy-13*k+i*4.4*k+((o.crownRot||0)%1)*4.4*k,hot>0?C.train:'#4a4a50',1,.9);
  s+=rr(0,0,W+2*B,H+2*B,r+B-1,'#17171A','stroke="#2E2E33" stroke-width="1"')+rr(ox-3,oy-3,W+6,H+6,r+3,'#050506');
  s+='<clipPath id="'+id+'"><rect x="'+ox+'" y="'+oy+'" width="'+W+'" height="'+H+'" rx="'+f(r)+'"/></clipPath>';
  s+='<g clip-path="url(#'+id+')"><rect x="'+ox+'" y="'+oy+'" width="'+W+'" height="'+H+'" fill="#000"/><g transform="translate('+ox+' '+oy+') scale('+f(k)+')">'+inner+'</g></g>';
  return s+'</svg>'}

/* =========================================== catalogue =========================================== */
/* sub = the one subject; tap / dt (Double Tap) / cr (Digital Crown) / hap (haptic) / note. '—' = does nothing here. */
var FLOWS=[['train','训练中记组'],['meal','语音记一餐'],['ask','一句话问 Jarvis'],['today','今天一眼'],['face','表盘和 Smart Stack'],['state','同步 / 离线状态']];
var LIST=[
 {id:'t1',flow:'train',t:'开始训练',svg:function(){return SC.start()},sub:'今天的组数（每段弧 = 一组）',tap:'「开始」= 开训练会话',dt:'= 开始',cr:'—',hap:'开始时一次'},
 {id:'t2',flow:'train',t:'记组主屏',svg:function(){return SC.set()},sub:'这一组：重量 × 次数',tap:'「完成」记一组；点数字 = 选中它',dt:'= 完成（不碰屏幕）',cr:'直接调次数（默认选中次数）',hap:'—',note:'左右滑 = 换动作；右下 = 上次这一组'},
 {id:'t3',flow:'train',t:'表冠调次数',svg:function(){return SC.set({focus:'r',r:'9'})},sub:'被选中的数（下划线 + 右侧刻度）',tap:'点重量 = 改调重量',dt:'= 完成',cr:'次数 ±1',hap:'每格一下'},
 {id:'t4',flow:'train',t:'表冠调重量',svg:function(){return SC.set({focus:'w',w:'62.5',r:'8'})},sub:'被选中的数',tap:'点次数 = 改回调次数',dt:'= 完成',cr:'重量 ±2.5 kg（按动作的步长）',hap:'每格一下'},
 {id:'t5',flow:'train',t:'记好了（1.2 秒）',svg:function(){return SC.logged({r:'9',delta:'+1 次'})},sub:'刚记的这一组 + 比上次',tap:'—（自动进休息）',dt:'—',cr:'—',hap:'成功震动'},
 {id:'t6',flow:'train',t:'组间休息',svg:function(){return SC.rest()},sub:'倒计时环',tap:'「跳过」= 回到下一组；点环 = 改刚才那组',dt:'= 跳过',cr:'休息 ±15 秒',hap:'到点提醒震动',note:'右下 ↓14 = 心率比这组结束时降了 14；环里小字 = 下一组'},
 {id:'t7',flow:'train',t:'换动作',svg:function(){return SC.set({name:'肩推',n:3,i:0,w:'25',r:'10',dots:1})},sub:'下一个动作的第一组',tap:'「完成」记一组',dt:'= 完成',cr:'—',hap:'换页一下',note:'左右滑换动作；圆点 = 6 个动作，第 2 个'},
 {id:'t8',flow:'train',t:'最后一组：还能做几个',svg:function(){return SC.rir()},sub:'RIR（0–4+），可不填',tap:'「继续」= 记下并进休息',dt:'= 继续',cr:'选 0–4+；不转 = 不填',hap:'每格一下'},
 {id:'t9',flow:'train',t:'结束小结（第 1 屏）',svg:function(){return SC.sumA()},sub:'完成组数',tap:'—',dt:'—',cr:'往下滚到事实',hap:'结束时一次'},
 {id:'t10',flow:'train',t:'结束小结（第 2 屏）',svg:function(){return SC.sumB()},sub:'训练质量的事实（没有评分）',tap:'「完成」= 结束会话，交给手机',dt:'= 完成',cr:'滚动',hap:'—',note:'比上次：4 个动作进步、1 个持平、1 个退步'},

 {id:'m1',flow:'meal',t:'按住说',svg:function(){return SC.listen()},sub:'Jarvis 核心 + 随声音动的环',tap:'按住 = 录音；松手 = 发送；上滑 = 取消',dt:'—',cr:'—',hap:'开始 / 结束各一下'},
 {id:'m2',flow:'meal',t:'转写 + 1.5 秒可取消',svg:function(){return SC.confirm()},sub:'听到的这句话',tap:'✕ = 取消；点核心 = 立刻发',dt:'= 立刻发',cr:'—',hap:'—',note:'环 1.5 秒走完自动发送'},
 {id:'m3',flow:'meal',t:'已记录回执',svg:function(){return SC.meal()},sub:'这一餐的热量',tap:'「撤销」→ 确认屏',dt:'—（撤销不给手势）',cr:'—',hap:'成功震动',note:'浅蓝段 = 这一餐加上去的部分'},
 {id:'m4',flow:'meal',t:'撤销要确认',svg:function(){return SC.undo()},sub:'一个问题，两个键',tap:'「撤销」/「保留」',dt:'= 保留',cr:'—',hap:'撤销后一下'},
 {id:'m5',flow:'meal',t:'4 个快捷模板',svg:function(){return SC.quick()},sub:'四格，点一下就记',tap:'点一格 = 记一次（不用 AI，可离线）',dt:'—',cr:'—',hap:'成功震动',note:'模板在手机上建和排序'},

 {id:'j1',flow:'ask',t:'听',svg:function(){return SC.listen({sec:'0:01',T:1500})},sub:'Jarvis 核心 + 聆听环',tap:'按住说，松手发',dt:'—',cr:'—',hap:'开始 / 结束各一下'},
 {id:'j2',flow:'ask',t:'想',svg:function(){return SC.think({q:'今天适合练腿吗'})},sub:'核心 + 一段转动的弧',tap:'点核心 = 取消',dt:'—',cr:'—',hap:'—'},
 {id:'j3',flow:'ask',t:'回答卡',svg:function(){return SC.answer()},sub:'结论（最多 4 个字）',tap:'「开始训练」= 卡片给的一个动作',dt:'= 开始训练',cr:'滚动（超出一屏时）',hap:'回答到达一下'},
 {id:'j4',flow:'ask',t:'回答太长',svg:function(){return SC.more()},sub:'一个数 + 一条图',tap:'「在手机上继续」= 把这轮对话交给手机',dt:'= 在手机上继续',cr:'—',hap:'—'},

 {id:'d1',flow:'today',t:'今天一眼',svg:function(){return SC.today()},sub:'恢复表盘',tap:'「上肢 A」= 开始训练；核心：点 = 快记，按住 = 说话；左上 = 立即同步',dt:'= 开始训练',cr:'—',hap:'—',note:'表盘不展开第二层，原因在手机上看'},
 {id:'d2',flow:'today',t:'今天一眼（休息日）',svg:function(){return SC.today({v:54,verdict:'正常练',rest:1})},sub:'恢复表盘',tap:'「记一餐」= 4 个快捷模板',dt:'= 记一餐',cr:'—',hap:'—'},

 {id:'c1',flow:'face',t:'表盘上的小组件',svg:function(){return SC.face()},sub:'恢复（圆形）',tap:'圆形恢复 / 负荷、矩形、行内 → 今天一眼；哑铃 → 开始训练；话筒 → 按住说',dt:'（系统）',cr:'（系统）',hap:'—',note:'行内、圆形 ×4、矩形；单色表盘上由系统染成一种颜色'},
 {id:'c1b',flow:'face',t:'表盘四角的小组件',svg:function(){return SC.face2()},sub:'四角：恢复、负荷、开始训练、按住说',tap:'同上',dt:'（系统）',cr:'（系统）',hap:'—',note:'角落款：一段弧 + 一个数或一个图标'},
 {id:'c2',flow:'face',t:'Smart Stack：早上',svg:function(){return SC.stackAm()},sub:'今天的三个分数',tap:'→ 今天一眼',dt:'（系统：打开 Smart Stack）',cr:'（系统：翻卡片）',hap:'—'},
 {id:'c3',flow:'face',t:'Smart Stack：训练时间',svg:function(){return SC.stackPlan()},sub:'今天的计划',tap:'▶ = 直接开始训练',dt:'（系统）',cr:'（系统）',hap:'—'},
 {id:'c4',flow:'face',t:'Smart Stack：训练中',svg:function(){return SC.stackLive()},sub:'休息倒计时 + 第几组',tap:'→ 回到当前这一组',dt:'（系统）',cr:'（系统）',hap:'—'},

 {id:'s1',flow:'state',t:'已连接',svg:function(){return SC.today()},sub:'左上：对勾 + 多久前',tap:'点左上 = 立即同步',dt:'',cr:'',hap:''},
 {id:'s2',flow:'state',t:'同步中',svg:function(){return SC.today({sync:'busy',syncLabel:'同步中',rot:40})},sub:'左上：转动的箭头',tap:'—',dt:'',cr:'',hap:''},
 {id:'s3',flow:'state',t:'数据旧了',svg:function(){return SC.today({sync:'stale',syncLabel:'6时前',stale:1})},sub:'左上变黄；数字变灰，不冒充最新',tap:'点左上 = 让手机同步',dt:'',cr:'',hap:''},
 {id:'s4',flow:'state',t:'手机不在身边，照常记',svg:function(){return SC.set({q:6,hr:128})},sub:'「待传 6」：6 组存在手表里',tap:'照常「完成」',dt:'',cr:'',hap:''},
 {id:'s5',flow:'state',t:'回到手机旁，补交中',svg:function(){return SC.start({sync:'busy',syncLabel:'补交 4/6'})},sub:'左上：补交进度',tap:'—',dt:'',cr:'',hap:'交完一下'},
 {id:'s6',flow:'state',t:'送达失败',svg:function(){return SC.start({sync:'fail',syncLabel:'重试'})},sub:'左上变红 +「重试」',tap:'点左上 = 重发',dt:'',cr:'',hap:'失败震动'},
 {id:'s7',flow:'state',t:'没联网：语音不可用',svg:function(){return SC.today({sync:'off',syncLabel:'离线',noAI:1})},sub:'核心变暗 + 断网标记',tap:'核心：点 = 快记（仍可用，联网后送达）',dt:'',cr:'',hap:''},
 {id:'s8',flow:'state',t:'还没登录',svg:function(){return SC.signin()},sub:'一个图标 + 一句短标签',tap:'—（在手机上登录后自动连上）',dt:'',cr:'',hap:''},
 {id:'s9',flow:'state',t:'今天的计划没同步',svg:function(){return SC.noplan()},sub:'同上',tap:'—（手机打开一次就带过来）',dt:'',cr:'',hap:''}
];
function byId(id){for(var i=0;i<LIST.length;i++)if(LIST[i].id===id)return LIST[i]}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function notes(it,full){var rows=[['主体',it.sub],['点按',it.tap],['Double Tap',it.dt],['表冠',it.cr]];if(full){rows.push(['触感',it.hap]);if(it.note)rows.push(['说明',it.note])}
  return rows.filter(function(r){return r[1]}).map(function(r){return '<div class="an"><b>'+r[0]+'</b><span>'+esc(r[1])+'</span></div>'}).join('')}
