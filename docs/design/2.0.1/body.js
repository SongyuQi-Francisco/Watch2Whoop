// Muscle body + set ring helpers for the 2.0.1 mockups. Needs muscle-body-data.js loaded first
// (body_engine/src/body_engine/web/static/muscle-body-data.js: paths from @mjcdev/react-body-highlighter 0.0.4, MIT; see THIRD_PARTY_NOTICES.md).
//   <div data-body="front|back" data-lit="chest:train deltoids:train@.4 quadriceps:bad"></div>   tone[@opacity], tone = any kit token name
//   <div data-sets="4 4 4 3 3" data-done="5"></div>   one arc per planned set, a wider gap between exercises; done = train 5 pt, next = train thin
(function(){
  function f(n){return (Math.round(n*100)/100)+'';}
  function body(el){
    var B=window.BodyMuscleBodies[el.dataset.sex||'male'][el.dataset.body],lit={};
    (el.dataset.lit||'').split(/\s+/).filter(Boolean).forEach(function(p){var a=p.split(':');lit[a[0]]=a[1]||'train';});
    var o='<svg xmlns="http://www.w3.org/2000/svg" viewBox="'+B.vb+'" width="100%" height="100%" style="display:block">';
    B.base.forEach(function(d){o+='<path d="'+d+'" fill="var(--text2)" fill-opacity="0.2"/>';});
    B.muscles.forEach(function(m){var t=lit[m[0]],fill='var(--text2)',op=0.3;
      if(t){var a=t.split('@');fill='var(--'+a[0]+')';op=a[1]?+a[1]:1;}
      m[1].forEach(function(d){o+='<path d="'+d+'" fill="'+fill+'" fill-opacity="'+op+'"/>';});});
    el.innerHTML=o+'</svg>';}
  function sets(el){
    var S=el.getBoundingClientRect().width,c=S/2,r=S/2-3,g=nums(el.dataset.sets),done=+el.dataset.done||0,n=g.reduce(function(a,b){return a+b;},0);
    var gs=1.6,gx=5.2,span=(360-n*gs-g.length*gx)/n,a=gx/2,k=0,o='<svg xmlns="http://www.w3.org/2000/svg" width="'+S+'" height="'+S+'" viewBox="0 0 '+S+' '+S+'" fill="none" style="display:block">';
    function pt(deg){var q=(deg-90)*Math.PI/180;return f(c+r*Math.cos(q))+' '+f(c+r*Math.sin(q));}
    g.forEach(function(cnt){for(var i=0;i<cnt;i++,k++){var st=k<done?['var(--train)',5,1]:(k===done&&done>0?['var(--train)',2,0.6]:['var(--track)',2,1]);
        o+='<path d="M'+pt(a+gs/2)+'A'+r+' '+r+' 0 0 1 '+pt(a+gs/2+span)+'" stroke="'+st[0]+'" stroke-width="'+st[1]+'" stroke-opacity="'+st[2]+'" stroke-linecap="butt"/>';a+=span+gs;}
      a+=gx;});
    el.innerHTML=o+'</svg>';}
  function nums(s){return (s||'').trim().split(/\s+/).map(Number);}
  function run(){document.querySelectorAll('[data-body]').forEach(body);document.querySelectorAll('[data-sets]').forEach(sets);}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();
})();
