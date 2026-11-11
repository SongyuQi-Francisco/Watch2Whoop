// The Jarvis key glyph approved on 2026-11-12 (first-pass cockpit core), as a static mark for the screen mockups:
// tick ring, three segments, an outlined inverted triangle with a lit inner triangle. Plain geometry; the moving version
// is jarvis-cockpit.html. Usage: <div data-core="58"></div> (size in pt).
(function(){
  var A='#8FDCFF';
  function P(r,deg){var a=deg*Math.PI/180;return [(29+r*Math.cos(a)).toFixed(2),(29+r*Math.sin(a)).toFixed(2)]}
  function tri(r){return 'M'+P(r,90).join(' ')+'L'+P(r,210).join(' ')+'L'+P(r,330).join(' ')+'Z'}
  function draw(el){
    var s=parseFloat(el.getAttribute('data-core'))||58,h='',i,a,b;
    for(i=0;i<36;i++){a=P(24.6,i*10-90);b=P(27.2,i*10-90);
      h+='<line x1="'+a[0]+'" y1="'+a[1]+'" x2="'+b[0]+'" y2="'+b[1]+'" stroke="var(--text2)" stroke-opacity=".7" stroke-width="1"/>'}
    for(i=0;i<3;i++){a=P(20.6,i*120-90+12);b=P(20.6,i*120-90+108);
      h+='<path d="M'+a[0]+' '+a[1]+'A20.6 20.6 0 0 1 '+b[0]+' '+b[1]+'" stroke="var(--text)" stroke-width="1.5" stroke-linecap="round"/>'}
    h+='<path d="'+tri(13.2)+'" stroke="var(--text)" stroke-width="1.6" stroke-linejoin="round"/>';
    h+='<path d="'+tri(5.6)+'" fill="'+A+'" stroke="'+A+'" stroke-width="1.2" stroke-linejoin="round" style="filter:drop-shadow(0 0 3px '+A+')"/>';
    el.style.display='inline-flex';el.style.width=s+'px';el.style.height=s+'px';
    el.innerHTML='<svg width="'+s+'" height="'+s+'" viewBox="0 0 58 58" fill="none" style="display:block;overflow:visible">'+h+'</svg>';
  }
  function run(){document.querySelectorAll('[data-core]').forEach(draw)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run);else run();
})();
