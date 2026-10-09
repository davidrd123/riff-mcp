"""Self-contained zoom viewer for portable comparison HTML."""
ZOOM_VIEWER = r'''
<style>
article img{cursor:zoom-in}article img:focus{outline:3px solid #9cd}
#zoom{border:1px solid #555;background:#111;color:#eee;padding:0;width:96vw;max-width:none;height:94vh;max-height:none}
#zoom::backdrop{background:#000c}#zoom header{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;padding:.8rem}
#zoom button,#zoom select{font:inherit;padding:.4rem;background:#333;color:white;border:1px solid #777;border-radius:5px}
#viewport{position:relative;overflow:hidden;touch-action:none;cursor:grab;background:#191919;height:calc(100% - 110px)}
#viewport:active{cursor:grabbing}#zoomImage{position:absolute;max-width:none;width:auto;height:auto;transform-origin:0 0;user-select:none;pointer-events:none}
#zoom p{margin:.4rem .8rem;font-size:14px}#zoomStatus{min-width:4rem}
</style>
<dialog id="zoom" aria-label="Full-resolution image viewer">
<header><select id="candidate" aria-label="Candidate"></select>
<button id="fit">Fit</button><button id="actual">100%</button>
<button id="minus" aria-label="Zoom out">−</button><button id="plus" aria-label="Zoom in">+</button>
<span id="zoomStatus" aria-live="polite"></span><button id="closeZoom">Close</button></header>
<p>Scroll to zoom · Drag to pan · Left/right arrows switch candidates at the same zoom and position · Esc closes</p>
<div id="viewport"><img id="zoomImage" alt="Enlarged candidate" draggable="false"></div></dialog>
<script>
(()=>{
const cards=[...document.querySelectorAll('article img')], dialog=document.getElementById('zoom'),
select=document.getElementById('candidate'), view=document.getElementById('viewport'),
img=document.getElementById('zoomImage'), status=document.getElementById('zoomStatus');
let scale=1,x=0,y=0,index=0,drag=null,opener=null;
function paint(){img.style.transform=`translate(${x}px,${y}px) scale(${scale})`;status.textContent=Math.round(scale*100)+'%';}
function fit(){scale=Math.min(view.clientWidth/img.naturalWidth,view.clientHeight/img.naturalHeight);x=(view.clientWidth-img.naturalWidth*scale)/2;y=(view.clientHeight-img.naturalHeight*scale)/2;paint();}
function zoom(next,cx=view.clientWidth/2,cy=view.clientHeight/2){next=Math.min(8,Math.max(.1,next));x=cx-(cx-x)*next/scale;y=cy-(cy-y)*next/scale;scale=next;paint();}
function show(i,reset=false){index=(i+cards.length)%cards.length;select.value=String(index);img.alt=select.options[index].text;img.onload=()=>reset?fit():paint();img.src=cards[index].src;}
cards.forEach((card,i)=>{const label=card.closest('article').querySelector('h2').textContent;
select.add(new Option(label,String(i)));card.tabIndex=0;card.setAttribute('role','button');card.setAttribute('aria-label','Zoom '+label);
function open(){opener=card;dialog.showModal();show(i,true);}card.onclick=open;card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}};});
select.onchange=()=>show(Number(select.value));
document.getElementById('fit').onclick=fit;document.getElementById('actual').onclick=()=>zoom(1);
document.getElementById('plus').onclick=()=>zoom(scale*1.5);document.getElementById('minus').onclick=()=>zoom(scale/1.5);
document.getElementById('closeZoom').onclick=()=>dialog.close();dialog.onclose=()=>opener?.focus();
view.onwheel=e=>{e.preventDefault();const r=view.getBoundingClientRect();zoom(scale*Math.exp(-e.deltaY*.002),e.clientX-r.left,e.clientY-r.top);};
view.onpointerdown=e=>{drag={px:e.clientX,py:e.clientY,x,y};view.setPointerCapture(e.pointerId);};
view.onpointermove=e=>{if(drag){x=drag.x+e.clientX-drag.px;y=drag.y+e.clientY-drag.py;paint();}};
view.onpointerup=view.onpointercancel=()=>{drag=null;};
dialog.addEventListener('keydown',e=>{if(e.target===select)return;if(e.key==='ArrowRight'){e.preventDefault();show(index+1);}if(e.key==='ArrowLeft'){e.preventDefault();show(index-1);}});
})();
</script>
'''
