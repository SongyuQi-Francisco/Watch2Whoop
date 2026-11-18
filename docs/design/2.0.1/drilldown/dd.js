// Drill-down study (2.1.0): shared parts and synthetic data for the option mockups. Load BEFORE ../kit.js and ../core.js
// (this script writes the markup, the kit then draws icons and charts into it).
//   ?size=se            375 x 667 (iPhone SE) instead of 402 x 874
//   ?opt=ladder,bare    replaces the page's <html data-opt="..."> (marks = a door chevron on every number, bare = only on
//                       the dial, the readings and the hero label, ladder = depth indicator)
//   ?theme=light        handled by kit.js
// Every number, date and nickname is synthetic (小林, 11月10日), the same day as screens 01-05.
(function(){
  var q=new URLSearchParams(location.search),H=document.documentElement;
  if(q.get('size'))H.setAttribute('data-size',q.get('size'));
  if(q.get('opt')!=null)H.setAttribute('data-opt',q.get('opt').split(',').join(' '));
  if(q.get('state'))H.setAttribute('data-state',q.get('state'));
  var SE=H.getAttribute('data-size')==='se';

  // ---------- device chrome ----------
  var SYS='<svg width="18" height="12" viewBox="0 0 18 12" fill="currentColor"><rect x="0" y="8" width="3" height="4" rx="1"/><rect x="5" y="5.5" width="3" height="6.5" rx="1"/><rect x="10" y="3" width="3" height="9" rx="1"/><rect x="15" y="0" width="3" height="12" rx="1"/></svg>'
    +'<svg width="17" height="12" viewBox="0 0 17 12" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M1.2 4.2a10.4 10.4 0 0 1 14.6 0"/><path d="M3.9 6.9a6.5 6.5 0 0 1 9.2 0"/><circle cx="8.5" cy="9.9" r="1.2" fill="currentColor" stroke="none"/></svg>'
    +'<svg width="27" height="13" viewBox="0 0 27 13" fill="none"><rect x="0.5" y="0.5" width="23" height="12" rx="3.6" stroke="currentColor" stroke-opacity="0.4"/><rect x="2" y="2" width="20" height="9" rx="2.2" fill="currentColor"/><path d="M25 4.5v4c.9-.3 1.5-1.1 1.5-2s-.6-1.7-1.5-2z" fill="currentColor" fill-opacity="0.45"/></svg>';
  function sb(){return '<div class="sb"><div class="sb-time">9:41</div><div class="sb-island"></div><div class="sb-sys">'+SYS+'</div></div>';}
  function chrome(on){
    var T=[['circle-gauge','今天'],['activity','身体'],['square-plus','记录'],['users','圈子'],['user','我']],o='<div class="jarvis" data-core="58"></div><nav class="tabbar">';
    T.forEach(function(t,i){o+='<div class="tab'+(i===+on?' on':'')+'"><i data-i="'+t[0]+'"></i>'+t[1]+'</div>';});
    return o+'</nav><div class="home-ind"></div>';}

  var MK='<i class="mk" data-i="chevron-right"></i>';
  function ladder(n,cls){var o='<span class="ladder'+(cls?' '+cls:'')+'">';for(var i=1;i<=3;i++)o+='<b'+(i<=n?' class="on"':'')+'></b>';return o+'</span>';}

  // ---------- first glance of 今天 (01-today) ----------
  function todayL0(){
    return '<header class="hdr"><div class="hdr-date">11月10日<i data-i="chevron-down"></i></div>'+ladder(1,'mid')
      +'<div class="sync is-ok"><i data-i="circle-check"></i>2分前</div></header>'
      +'<section class="hero"><div class="dial" data-dial="82" data-band="58 74" data-zones="34 67" data-tone="good"></div>'
      +'<div class="dial-core"><div class="hero-label">恢复'+MK+'</div>'
      +'<div class="hero-num"><span class="t-hero">82</span><span class="pct">%</span></div>'
      +'<div class="minibars" style="--tone:var(--good)"><b style="height:9.5px"></b><b style="height:12px"></b><b style="height:6.5px"></b><b style="height:11px"></b><b style="height:13px"></b><b style="height:11.4px"></b><b style="height:16px"></b></div>'
      +'<div class="verdict t-verdict">可上强度</div></div></section>'
      +'<section class="gauges">'
      +'<div class="gauge"><div class="gauge-hd"><span class="t-gauge">88</span><span class="t-label">睡眠</span>'+MK+'</div>'
      +'<div class="gauge-bar" data-gauge="88" data-max="100" data-band="72 84" data-tone="sleep" data-step="10"></div></div>'
      +'<div class="gauge"><div class="gauge-hd"><span class="t-gauge">2.8</span><span class="t-label">负荷</span>'+MK+'</div>'
      +'<div class="gauge-bar" data-gauge="2.8" data-max="21" data-target="12 16" data-plan="13.4" data-tone="train" data-step="2" data-majors="10 14 18"></div></div>'
      +'</section>'
      +'<section class="faces">'
      +'<div class="face"><div class="face-hd"><i data-i="heart-pulse"></i>体征'+MK+'</div><div class="face-num"><span class="t-face">5/5</span></div>'
      +'<div class="face-gfx"><svg class="vitals" width="100%" height="26" viewBox="0 0 91 26" preserveAspectRatio="xMidYMid meet">'
      +'<rect x="0" y="1" width="6.5" height="24" rx="3.25"/><circle cx="3.25" cy="10" r="2.5"/><rect x="21" y="1" width="6.5" height="24" rx="3.25"/><circle cx="24.25" cy="7" r="2.5"/>'
      +'<rect x="42" y="1" width="6.5" height="24" rx="3.25"/><circle cx="45.25" cy="14.5" r="2.5"/><rect x="63" y="1" width="6.5" height="24" rx="3.25"/><circle cx="66.25" cy="17" r="2.5"/>'
      +'<rect x="84.5" y="1" width="6.5" height="24" rx="3.25"/><circle cx="87.75" cy="11.5" r="2.5"/></svg></div></div>'
      +'<div class="face"><div class="face-hd"><i data-i="soup"></i>饮食'+MK+'</div><div class="face-num"><span class="t-face">420</span><span class="u">kcal</span></div>'
      +'<div class="face-gfx"><div data-gauge="420" data-max="2500" data-target="2100" data-tone="text" data-step="500" style="width:100%;height:22px"></div></div></div>'
      +'<div class="face"><div class="face-hd"><i data-i="users"></i>圈子'+MK+'</div><div class="face-num"><span class="t-face">3</span><span class="u">人练了</span></div>'
      +'<div class="face-gfx avs"><span class="av on">鹿</span><span class="av on">杰</span><span class="av">木</span><span class="av on">K</span>'+(SE?'':'<span class="av">安</span>')+'</div></div>'
      +'</section>';}

  // the second row of faces of 今天 (训练 · 今晚 · 提醒), as built in 2.1.0 (TodayMoreFaces)
  function moreFaces(){
    return '<section class="faces more">'
      +'<div class="face"><div class="face-hd"><i data-i="dumbbell"></i>训练</div><div class="face-num"><span class="t-face">5</span><span class="u">项计划</span></div><div class="face-gfx"></div></div>'
      +'<div class="face"><div class="face-hd"><i data-i="moon"></i>今晚</div><div class="face-num"><span class="t-face">23:40</span></div><div class="face-gfx"></div></div>'
      +'<div class="face attn"><div class="face-hd"><i data-i="bell"></i>提醒</div><div class="face-num"><span class="t-face">2</span></div><div class="face-gfx"></div></div>'
      +'</section>';}

  // ---------- first glance of 身体 (05-body) ----------
  var REC30='58 66 71 49 62 74 69 55 61 77 72 64 38 52 67 70 63 59 75 68 46 61 66 64 71 55 68 74 69 82';
  var HRV30='55 58 54 57 60 56 52 59 61 58 55 60 57 59 62 57 60 58 55 61 63 59 60 64 62 61 65 63 66 68';
  function bodyL0(){
    return '<header class="hdr"><div class="hdr-date">近 30 天<i data-i="chevron-down"></i></div>'+ladder(1,'mid')
      +'<div class="sync is-ok"><i data-i="circle-check"></i>2分前</div></header>'
      +'<section class="p-hero"><div class="p-hl">平均恢复'+MK+'</div>'
      +'<div class="p-mean"><div class="hero-num"><span class="t-hero">64</span><span class="pct">%</span></div>'
      +'<div class="p-delta"><span class="d">+3</span><span class="t-label">比上期</span></div></div>'
      +'<div class="p-bars" data-bars="'+REC30+'" data-band="58 74" data-max="100" data-tone="good"></div>'
      +'<div class="clabels"><span>10/12</span><span>10/26</span><span>11/10</span></div>'
      +'<div class="p-dots"><b class="on"></b><b></b><b></b></div></section>'
      +'<section class="faces">'
      +'<div class="face"><div class="face-hd"><i data-i="heart"></i>心肺'+MK+'</div><div class="face-num"><span class="t-face">68</span><span class="u">ms</span></div>'
      +'<div class="face-gfx"><div data-spark="'+HRV30+'" data-band="54 64" data-dot="text" data-bg="surface" style="width:100%;height:26px"></div></div></div>'
      +'<div class="face"><div class="face-hd"><i data-i="person-standing"></i>身体年龄'+MK+'</div><div class="face-num"><span class="t-face">29.4</span><span class="u">岁</span></div>'
      +'<div class="face-gfx"><svg width="100%" height="22" viewBox="0 0 91 22" fill="none" style="display:block;overflow:visible"><rect x="0" y="5.25" width="91" height="1.5" rx="0.75" fill="var(--track)"/><rect x="0" y="17" width="1" height="5" fill="var(--tick-major)"/><rect x="11.37" y="17" width="1" height="3" fill="var(--tick)"/><rect x="22.75" y="17" width="1" height="3" fill="var(--tick)"/><rect x="34.12" y="17" width="1" height="3" fill="var(--tick)"/><rect x="45.5" y="17" width="1" height="5" fill="var(--tick-major)"/><rect x="56.88" y="17" width="1" height="3" fill="var(--tick)"/><rect x="68.25" y="17" width="1" height="3" fill="var(--tick)"/><rect x="79.62" y="17" width="1" height="3" fill="var(--tick)"/><rect x="90" y="17" width="1" height="5" fill="var(--tick-major)"/><rect x="44.75" y="0" width="1.5" height="12" rx="0.75" fill="var(--tick-major)"/><rect x="27.3" y="4" width="16.7" height="4" rx="2" fill="var(--good)"/></svg></div></div>'
      +'<div class="face"><div class="face-hd"><i data-i="calendar-days"></i>周报'+MK+'</div><div class="face-num"><span class="t-face">4</span><span class="u">天训练</span></div>'
      +'<div class="face-gfx" style="align-items:center"><svg width="100%" height="10" viewBox="0 0 91 10" fill="none" style="display:block"><circle cx="4.5" cy="5" r="4.5" fill="var(--train)"/><circle cx="18.17" cy="5" r="4.5" fill="var(--train)"/><circle cx="31.83" cy="5" r="3.75" stroke="var(--track)" stroke-width="1.5"/><circle cx="45.5" cy="5" r="4.5" fill="var(--train)"/><circle cx="59.17" cy="5" r="3.75" stroke="var(--track)" stroke-width="1.5"/><circle cx="72.83" cy="5" r="4.5" fill="var(--train)"/><circle cx="86.5" cy="5" r="3.75" stroke="var(--track)" stroke-width="1.5"/></svg></div></div>'
      +'</section>';}

  // ---------- the day's metrics (the 31 rows of 2.0.0's 身体 → 指标 → 今天, same groups and order) ----------
  // key: [label, value, unit, delta vs usual, tone of the delta, usual low, usual high, today's value as a number]
  var M={
    recovery:['恢复','82','%','+18','good',58,74,82], hrv:['HRV','68','ms','+6','good',54,64,68], rhr:['静息心率','51','bpm','−2','good',52,55,51],
    resp:['呼吸频率','14.2','次/分','0.0','',13.9,14.6,14.2], spo2:['血氧','97.1','%','+0.3','',96.4,97.2,97.1], temp:['手腕温度','+0.12','°C','','',-0.2,0.2,0.12],
    illness:['生病风险','8','','−3','good',6,18,8], lowhr:['最低心率出现','38','%','−6','good',36,52,38],
    sleep:['睡眠','7:36','','+0:24','good',6.6,7.4,7.6], perf:['睡眠表现','92','%','+5','good',80,90,92], perfw:['综合表现','88','','+4','good',76,86,88],
    debt:['睡眠债','0.4','时','−0.6','good',0.6,1.6,0.4], rest:['恢复性睡眠','3.4','时','+0.3','good',2.8,3.3,3.4], eff:['睡眠效率','93','%','+1','',89,94,93],
    score:['睡眠分','88','','+6','good',72,84,88], wake:['夜间醒来','2','次','−1','good',2,4,2], light:['日照','46','分','+12','good',20,40,46],
    strain:['负荷','2.8','','','',8,14,2.8], tsb:['训练负荷','+4','TSB','','',-8,6,4], acwr:['急慢比','1.05','','','',0.9,1.2,1.05],
    strainc:['含肌肉负荷','3.1','','','',8,15,3.1], vol:['训练量','0','kg','','',2000,6000,0], steps:['步数','3,240','','','',6000,9500,3240],
    akcal:['活动消耗','186','kcal','','',380,620,186], tkcal:['总消耗','1,020','kcal','','',2300,2700,1020], hrr:['心率恢复','31','bpm','+3','good',24,30,31],
    stress:['压力','0.8','','−0.3','good',0.9,1.5,0.8], resil:['韧性','61','','+4','good',50,60,61],
    vo2:['VO₂max','46.2','','+0.4','good',45.2,46.0,46.2], weight:['体重','71.4','kg','−0.3','',71.2,72.4,71.4], age:['身体年龄','29.4','岁','−0.2','good',29.3,29.9,29.4]
  };
  var S={hrv:'57 60 58 55 61 63 59 60 64 62 61 65 63 68',rhr:'54 54 55 53 53 56 55 53 54 53 52 53 52 51',recovery:'61 77 72 64 38 52 67 70 63 59 75 68 74 82'};
  var G={recovery:['heart-pulse','恢复',['recovery','hrv','rhr','resp','spo2','temp','illness','lowhr']],
    sleep:['moon','睡眠',['sleep','perf','perfw','debt','rest','eff','score','wake','light']],
    strain:['flame','负荷',['strain','tsb','acwr','strainc','vol','steps','akcal','tkcal','hrr']],
    stress:['activity','压力',['stress','resil']],long:['chart-line','长期',['vo2','weight','age']]};
  function gen(key){            // 14 deterministic days around the usual range, ending at today's value
    var m=M[key],lo=m[5],hi=m[6],s=7,i,out=[];for(i=0;i<key.length;i++)s=(s*31+key.charCodeAt(i))>>>0;
    var v=(lo+hi)/2;for(i=0;i<13;i++){s=(s*1664525+1013904223)>>>0;v=v*0.55+(lo-(hi-lo)*0.2+(hi-lo)*1.4*(s/4294967296))*0.45;out.push(v);}
    out.push(m[7]);return out.map(function(x){return Math.round(x*100)/100;}).join(' ');}
  function row(key,bg){
    var m=M[key];
    return '<div class="row"><span class="row-l">'+m[0]+'</span>'
      +'<div class="spark" data-spark="'+(S[key]||gen(key))+'" data-band="'+m[5]+' '+m[6]+'" data-bg="'+(bg||'bg')+'"></div>'
      +'<span class="row-v"><span class="t-row">'+m[1]+'</span>'+(m[2]?'<span class="u">'+m[2]+'</span>':'')+'</span>'
      +'<span class="delta '+m[4]+'">'+m[3]+'</span><i class="chev" data-i="chevron-right"></i></div>';}
  function rows(keys,bg){return '<div class="rows wide">'+keys.map(function(k){return row(k,bg);}).join('')+'</div>';}
  function group(id,bg){var g=G[id];return '<div class="gh"><i data-i="'+g[0]+'"></i>'+g[1]+'<span class="n">'+g[2].length+'</span></div>'+rows(g[2],bg);}
  function jump(){
    var o='<div class="jump">',first=true;
    ['recovery','sleep','strain','stress','long'].forEach(function(id){o+='<span'+(first?' class="on"':'')+'><i data-i="'+G[id][0]+'"></i>'+G[id][2].length+'</span>';first=false;});
    return o+'<span><i data-i="circle-gauge"></i>9</span></div>';}       // the last one: 当前状态 (9 status cards)
  function trio(){
    function c(v,max,tone,label){return '<div><div class="rg" data-ring="'+v+'" data-max="'+max+'" data-w="3" data-tone="'+tone+'"></div>'
      +'<div class="tv"><span class="t-face">'+v+'</span><span class="t-unit">'+label+'</span></div></div>';}
    return '<div class="trio">'+c(82,100,'good','恢复')+c(88,100,'sleep','睡眠')+c(2.8,21,'train','负荷')+'</div>';}
  function chips(){
    return '<div class="chips"><span class="chip"><i data-i="dumbbell"></i>5<span class="u">项</span></span>'
      +'<span class="chip"><i data-i="moon"></i>23:40</span><span class="chip attn"><i data-i="bell"></i>2</span></div>';}
  // 今日详情: opts = "trio" and/or "chips"; bg = token the list sits on (bg | sheet)
  function day(opts,bg){
    return (opts.indexOf('chips')>=0?chips():'')+(opts.indexOf('trio')>=0?trio():'')+jump()
      +['recovery','sleep','strain','stress','long'].map(function(id){return group(id,bg);}).join('');}

  // ---------- one metric (HRV): today's value, range switch, chart with the usual band and the 7-day line, figures, related ----------
  function metric(bg,compact){
    return '<div class="mv"><span class="t-big">68</span><span class="u">ms</span><span class="delta good">+6</span><span class="t-label">比常态</span></div>'
      +'<div class="seg s full"><span>7 天</span><span class="on">30 天</span><span>90 天</span><span>1 年</span></div>'
      +'<div class="lg"><span class="legend"><s></s>常态 54–64</span><span class="legend"><b class="ln"></b>7 天均</span><span class="ro">10/29 · 58 ms</span></div>'
      +'<div class="line" data-line="'+HRV30+'" data-band="54 64" data-sel="17" data-bg="'+(bg||'bg')+'"'+(compact?' style="height:'+compact+'px"':'')+'></div>'
      +'<div class="clabels"><span>10/12</span><span>10/26</span><span>11/10</span></div>'
      +'<div class="stats"><div><span class="t-row">60<span class="u">ms</span></span><span class="t-unit">均值</span></div>'
      +'<div><span class="t-row">68</span><span class="t-unit">最高</span></div><div><span class="t-row">52</span><span class="t-unit">最低</span></div>'
      +'<div><span class="t-row" style="color:var(--good)">+3</span><span class="t-unit">比上期</span></div></div>'
      +'<div class="gh" style="margin-top:14px"><i data-i="link"></i>相关</div>'+rows(['recovery','rhr','resp'],bg);}

  // option B: the drawer's peeking edge, one bare trend line per metric (no labels: the hint is graphics only)
  function strip(keys){return '<div class="strip">'+keys.map(function(k){var m=M[k];
    return '<div data-spark="'+(S[k]||gen(k))+'" data-band="'+m[5]+' '+m[6]+'" data-bg="sheet"></div>';}).join('')+'</div>';}

  // ---------- write the parts ----------
  var P={sb:sb,'today-l0':todayL0,'body-l0':bodyL0,'more-faces':moreFaces,jump:jump,trio:trio,chips:chips};
  document.querySelectorAll('[data-part]').forEach(function(el){
    var p=el.getAttribute('data-part'),h;
    if(p==='chrome')h=chrome(el.getAttribute('data-on')||0);
    else if(p==='day')h=day(el.getAttribute('data-with')||'',el.getAttribute('data-bg'));
    else if(p==='metric')h=metric(el.getAttribute('data-bg'),el.getAttribute('data-h'));
    else if(p==='rows')h=rows((el.getAttribute('data-keys')||'').split(' '),el.getAttribute('data-bg'));
    else if(p==='ladder')h=ladder(+el.getAttribute('data-n'));
    else if(p==='strip')h=strip((el.getAttribute('data-keys')||'').split(' '));
    else h=P[p]();
    el.outerHTML=h;});

  // ---------- line chart (the metric page): daily line, usual band, 7-day mean as the trend line, one selected day ----------
  function f(n){return Math.round(n*100)/100;}
  function line(el){
    var r=el.getBoundingClientRect(),W=r.width,Hh=r.height,vs=el.dataset.line.trim().split(/\s+/).map(Number),band=(el.dataset.band||'').split(' ').map(Number);
    var lo=Math.min.apply(null,vs.concat(band)),hi=Math.max.apply(null,vs.concat(band)),pad=(hi-lo)*0.16;lo-=pad;hi+=pad;
    function x(i){return 3+i/(vs.length-1)*(W-6);}function y(v){return (Hh-1)*(1-(v-lo)/(hi-lo));}
    var o='<svg xmlns="http://www.w3.org/2000/svg" width="'+f(W)+'" height="'+f(Hh)+'" viewBox="0 0 '+f(W)+' '+f(Hh)+'" fill="none" style="display:block;overflow:visible">';
    o+='<rect x="0" y="'+f(y(band[1]))+'" width="'+f(W)+'" height="'+f(y(band[0])-y(band[1]))+'" fill="var(--band)"/>';
    band.forEach(function(b){o+='<line x1="0" x2="'+f(W)+'" y1="'+f(y(b))+'" y2="'+f(y(b))+'" stroke="var(--band-line)" stroke-width="1" stroke-dasharray="2 3"/>';});
    function path(a,from){return a.map(function(v,i){return (i?'L':'M')+f(x(i+from))+' '+f(y(v));}).join('');}
    o+='<path d="'+path(vs,0)+'" stroke="var(--spark)" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round" stroke-opacity="0.75"/>';
    var mean=[],i,j,s;for(i=6;i<vs.length;i++){s=0;for(j=i-6;j<=i;j++)s+=vs[j];mean.push(s/7);}
    o+='<path d="'+path(mean,6)+'" stroke="var(--text)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';
    var k=+el.dataset.sel;
    if(k){o+='<line x1="'+f(x(k))+'" x2="'+f(x(k))+'" y1="0" y2="'+f(Hh-1)+'" stroke="var(--hair-strong)" stroke-width="1"/>'
      +'<circle cx="'+f(x(k))+'" cy="'+f(y(vs[k]))+'" r="3.5" fill="var(--text)" stroke="var(--'+(el.dataset.bg||'bg')+')" stroke-width="1.5"/>';}
    var n=vs.length-1;o+='<circle cx="'+f(x(n))+'" cy="'+f(y(vs[n]))+'" r="3.5" fill="var(--good)" stroke="var(--'+(el.dataset.bg||'bg')+')" stroke-width="1.5"/>';
    o+='<rect x="0" y="'+f(Hh-1)+'" width="'+f(W)+'" height="1" fill="var(--hair-strong)"/>';
    el.innerHTML=o+'</svg>';}
  function run(){document.querySelectorAll('[data-line]').forEach(line);}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();
})();
