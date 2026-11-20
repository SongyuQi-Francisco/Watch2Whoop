// Boards (tap paths, comparison): places scaled phone frames (iframes of the screens), captions, tap markers and wires.
//   <div class="fr" id="t" data-src="a-today.html" data-x="64" data-y="160" data-s="0.62" data-cap="今天|首屏"
//        [data-w="375" data-h="667"] [data-cy="560" data-ch="314"]></div>      cy / ch = crop (points of the screen)
//   <i class="tapm" data-f="t" data-px="201" data-py="270">1</i>                  a marker at a point of a frame
//   <div class="bd" data-wires='[["t",201,270,"d",0,300], ...]'>                  wires between points of frames
//   <div class="dimline" data-f="z" data-py="704" data-x0="0" data-x1="402"></div>, <div class="dimlab" data-f="z" data-px data-py>
(function(){
  var FR={},bd=document.querySelector('.bd');
  document.querySelectorAll('.fr').forEach(function(el){
    var s=+el.dataset.s||0.62,w=+(el.dataset.w||402),h=+(el.dataset.h||874),x=+el.dataset.x,y=+el.dataset.y,cy=+(el.dataset.cy||0),ch=+(el.dataset.ch||h);
    el.style.left=x+'px';el.style.top=y+'px';el.style.width=(w*s)+'px';el.style.height=(ch*s)+'px';
    if(el.dataset.r)el.style.borderRadius=el.dataset.r+'px';
    var f=document.createElement('iframe');f.src=el.dataset.src;f.width=w;f.height=h;f.scrolling='no';
    f.style.width=w+'px';f.style.height=h+'px';f.style.transform='translateY('+(-cy*s)+'px) scale('+s+')';el.appendChild(f);
    FR[el.id]={x:x,y:y,s:s,cy:cy,w:w*s,h:ch*s};
    if(el.dataset.cap){var c=document.createElement('div'),p=el.dataset.cap.split('|');c.className='fcap';
      c.innerHTML=p[0]+(p[1]?'<small>'+p[1]+'</small>':'');c.style.left=(x-30)+'px';c.style.width=(w*s+60)+'px';c.style.top=(y+ch*s+14)+'px';bd.appendChild(c);}
  });
  function P(f,px,py){var r=FR[f];return [r.x+px*r.s,r.y+(py-r.cy)*r.s];}
  document.querySelectorAll('.tapm').forEach(function(el){var p=P(el.dataset.f,+el.dataset.px,+el.dataset.py);el.style.left=p[0]+'px';el.style.top=p[1]+'px';});
  document.querySelectorAll('.dimline').forEach(function(el){var a=P(el.dataset.f,+el.dataset.x0,+el.dataset.py),b=P(el.dataset.f,+el.dataset.x1,+el.dataset.py);
    el.style.left=a[0]+'px';el.style.top=a[1]+'px';el.style.width=(b[0]-a[0])+'px';});
  document.querySelectorAll('.dimlab').forEach(function(el){var p=P(el.dataset.f,+el.dataset.px,+el.dataset.py);el.style.left=p[0]+'px';el.style.top=p[1]+'px';});
  var W=bd.offsetWidth,Hh=bd.offsetHeight,wires=JSON.parse(bd.dataset.wires||'[]');
  var o='<svg class="wires" width="'+W+'" height="'+Hh+'" viewBox="0 0 '+W+' '+Hh+'" fill="none"><defs>'
    +'<marker id="ah" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M1 1.5 8.5 5 1 8.5" stroke="#8FDCFF" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></marker></defs>';
  wires.forEach(function(w){
    var a=P(w[0],w[1],w[2]),b=P(w[3],w[4],w[5]),dir=b[0]>=a[0]?1:-1,dx=Math.max(50,Math.abs(b[0]-a[0])*0.45)*dir,dash=w[6]==='dash';
    if(w[6]==='up'){   // a wire that leaves upwards and comes down on the target (for the way back)
      var top=Math.min(a[1],b[1])-(w[7]||70);
      o+='<path d="M'+a[0]+' '+a[1]+'C'+a[0]+' '+top+' '+b[0]+' '+top+' '+b[0]+' '+b[1]+'" stroke="#8FDCFF" stroke-width="1.5" stroke-dasharray="5 5" marker-end="url(#ah)"/>';
    } else {
      o+='<path d="M'+a[0]+' '+a[1]+'C'+(a[0]+dx)+' '+a[1]+' '+(b[0]-dx)+' '+b[1]+' '+b[0]+' '+b[1]+'" stroke="#8FDCFF" stroke-width="1.5"'+(dash?' stroke-dasharray="5 5"':'')+' marker-end="url(#ah)"/>';
    }});
  bd.insertAdjacentHTML('beforeend',o+'</svg>');
})();
