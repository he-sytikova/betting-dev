(() => {
  const $ = id => document.getElementById(id);
  const pct = n => Number.isFinite(n) ? (n*100).toFixed(1)+'%' : '—';
  const norm = s => String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase()
    .replace(/&/g,' and ').replace(/\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b/g,' ')
    .replace(/[^\p{L}\p{N}]+/gu,' ').trim().replace(/\s+/g,' ');
  const num = s => { const n=Number(String(s).replace(',','.')); return Number.isFinite(n)&&n>=0&&n<=100?n/100:null; };
  function parse1x2(s) {
    const m=[...String(s||'').matchAll(/(П1|X|П2)\s*(\d+(?:[.,]\d+)?)\s*%/gi)];
    if(m.length!==3)return null;
    const o={}; for(const x of m)o[x[1].toUpperCase()]=num(x[2]);
    if(!['П1','X','П2'].every(k=>Number.isFinite(o[k])))return null;
    const sum=o.П1+o.X+o.П2; if(sum<.85||sum>1.15)return null;
    return {home:o.П1/sum,draw:o.X/sum,away:o.П2/sum};
  }
  function parseLines(s, market) {
    const out={};
    const re=market==='corners'?/(?:ТБ\s*угл\.?\s*)?(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)\s*%/gi
      :/(ТБ|ТМ)\s*(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)\s*%/gi;
    for(const m of String(s||'').matchAll(re)){
      const line=Number(m[market==='corners'?1:2].replace(',','.'));
      const value=num(m[market==='corners'?2:3]);
      if(value===null)continue;
      const key=market==='corners'?String(line):m[1].toUpperCase()+' '+line;
      out[key]=value;
    }
    return out;
  }
  const avg = values => values.length?values.reduce((a,b)=>a+b,0)/values.length:null;
  function aggregate(items) {
    const map=new Map();
    for(const p of items){
      const time=Date.parse(p.time); if(!Number.isFinite(time)||time<=Date.now())continue;
      const home=norm(p.home),away=norm(p.away); if(!home||!away)continue;
      const key=[time,home,away].join('|');
      if(!map.has(key))map.set(key,{time,home:p.home,away:p.away,league:p.league||'—',rows:[]});
      map.get(key).rows.push(p);
    }
    return [...map.values()].map(f=>{
      const outcome=f.rows.map(p=>parse1x2(p.probabilityOutcome)).filter(Boolean);
      const home=avg(outcome.map(x=>x.home)),draw=avg(outcome.map(x=>x.draw)),away=avg(outcome.map(x=>x.away));
      const totals=new Map(),corners=new Map();
      for(const p of f.rows){
        for(const [k,v] of Object.entries({...parseLines(p.totalGoals,'goals'),...parseLines(p.underGoals,'goals')})){
          if(!totals.has(k))totals.set(k,[]); totals.get(k).push(v);
        }
        for(const [k,v] of Object.entries(parseLines(p.corners,'corners'))){
          if(!corners.has(k))corners.set(k,[]); corners.get(k).push(v);
        }
      }
      const markets={}; for(const [k,v] of totals)markets[k]=avg(v);
      const cornerMarkets={}; for(const [k,v] of corners)cornerMarkets[k]=avg(v);
      return {...f,sourceCount:f.rows.length,sourceNames:[...new Set(f.rows.map(p=>p.source))],
        outcome:outcome.length?{home,draw,away}:null,
        derived:outcome.length?{oneX:home+draw,xTwo:draw+away,twelve:home+away,dnbHome:home/(home+away),dnbAway:away/(home+away)}:null,
        totals:markets,corners:cornerMarkets};
    }).sort((a,b)=>a.time-b.time);
  }
  const fmtTime=t=>new Date(t).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
  const heat=p=>{const x=Math.max(0,Math.min(1,p)),a=[240,253,244],b=[20,83,45];return 'rgb('+a.map((v,i)=>Math.round(v+(b[i]-v)*x)).join(', ')+')';};
  const cell=(tr,value)=>{const td=document.createElement('td');td.textContent=value;tr.append(td);return td;};
  function stackedCell(tr,items){
    const td=document.createElement('td');td.className='probability-cell';
    if(!items.length){td.textContent='—';tr.append(td);return td;}
    for(const item of items){
      const line=document.createElement('div');line.className='probability-line';
      line.textContent=item.label+': '+pct(item.value);
      if(Number.isFinite(item.value)){line.style.backgroundColor=heat(item.value);line.title='Вероятность: '+pct(item.value);if(item.value>.65)line.classList.add('is-highlighted');}
      td.append(line);
    }
    tr.append(td);return td;
  }
  const sorted=o=>Object.entries(o).sort((a,b)=>a[0].localeCompare(b[0],undefined,{numeric:true})).map(([label,value])=>({label,value}));
  function render(fixtures,mode){
    const body=$('rows');body.replaceChildren();
    let visible=0;
    for(const f of fixtures){
      const selected=mode==='main'?(f.outcome||Object.keys(f.totals).length):(Object.keys(f.corners).length>0);
      if(!selected)continue;
      const tr=document.createElement('tr');
      cell(tr,fmtTime(f.time));cell(tr,f.league);cell(tr,f.home+' — '+f.away);
      if(mode==='main'){
        const o=f.outcome;
        stackedCell(tr,o?[{label:'П1',value:o.home},{label:'X',value:o.draw},{label:'П2',value:o.away}]:[]);
        const d=f.derived;
        stackedCell(tr,d?[{label:'1X',value:d.oneX},{label:'X2',value:d.xTwo},{label:'12',value:d.twelve},{label:'DNB П1',value:d.dnbHome},{label:'DNB П2',value:d.dnbAway}]:[]);
        stackedCell(tr,sorted(f.totals));
        cell(tr,f.sourceNames.join(', ')+' ('+f.sourceCount+')');
      } else {
        stackedCell(tr,sorted(f.corners).map(x=>({label:'ТБ угл. '+x.label,value:x.value})));
        cell(tr,f.sourceNames.join(', ')+' ('+f.sourceCount+')');
      }
      body.append(tr);visible++;
    }
    $('count').textContent='Предстоящих матчей: '+visible+'. Каждая вероятность показана отдельной строкой с градиентной подсветкой: чем выше вероятность, тем насыщеннее зелёный; выше 65% — жирным. Значения усредняются только по доступным данным и совпадающим линиям.';
    $('empty').hidden=visible>0;
  }
  async function load(){
    $('status').textContent='Загрузка…';
    try{
      const r=await fetch('./data.json?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error('data.json: HTTP '+r.status);
      const d=await r.json();const fixtures=aggregate(d.predictions||[]);
      render(fixtures,document.body.dataset.mode);
      $('status').textContent='Обновлено: '+new Date(d.generatedAt||Date.now()).toLocaleString('ru-RU');
    }catch(e){$('status').textContent='Ошибка загрузки: '+e.message;}
  }
  const api={norm,parse1x2,parseLines,aggregate,pct};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.ApiConsensus=api;
  if(typeof document!=='undefined'&&$('refresh')){$('refresh').addEventListener('click',load);load();}
})();