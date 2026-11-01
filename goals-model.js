'use strict';

const $ = (id) => document.getElementById(id);
let rows = [];
let modelConfig = null;
let report = null;

function normalize(value) {
  return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('ru').replace(/\b(fc|afc|cf|sc|sv|fk|sk|club|the)\b/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
}
const TEAM_ALIASES = {
  'ac milan': 'milan',
  'internazionale milano': 'inter',
  'deportivo alaves': 'alaves',
  'cambuur leeuwarden': 'cambuur',
  'az': 'az alkmaar',
  'pec zwolle': 'zwolle',
  'ado den haag': 'den haag',
  'como 1907': 'como',
  'unione venezia': 'venezia',
  'twente 65': 'twente',
  'telstar 1963': 'telstar',
  'sheffield wednesday': 'sheffield weds',
  'rc strasbourg': 'strasbourg',
  'rc strasbourg alsace': 'strasbourg',
  'aj auxerre': 'auxerre',
  'cagliari calcio': 'cagliari',
  'frosinone calcio': 'frosinone',
  'ss lazio': 'lazio',
  '1 union berlin': 'union berlin',
  '1 koln': 'koln',
  'spvgg greuther furth': 'greuther furth',
  'hannover 96': 'hannover',
  '1 nurnberg': 'nurnberg',
  'royal antwerp': 'antwerp',
  'sint truidense vv': 'st truiden',
  'sbv excelsior': 'excelsior',
  'us avellino 1912': 'avellino',
  'hellas verona': 'verona',
  'citta di palermo': 'palermo',
  'vicenza calcio': 'vicenza',
  'racing de santander': 'santander',
  'real racing': 'santander',
  'athletic': 'ath bilbao',
  'athletic club': 'ath bilbao',
  'real sociedad': 'sociedad',
  'nacional da madeira': 'nacional',
  'eintracht frankfurt': 'ein frankfurt',
  'hamburger sv': 'hamburg',
  'hamburger': 'hamburg',
  'angers sco': 'angers',
  'olympique lyonnais': 'lyon',
  'olympique de marseille': 'marseille',
  'as monaco': 'monaco',
  'as monaco fc': 'monaco',
  'genoa cfc': 'genoa',
  'ssc napoli': 'napoli',
  'us lecce': 'lecce',
  'juventus fc': 'juventus',
  'cd eldense': 'eldense',
  'ad ceuta': 'ceuta',
  'cd castellon': 'castellon',
  'cd tenerife': 'tenerife',
  'burgos de futbol': 'burgos',
  'holstein': 'holstein kiel',
  'lr vicenza': 'vicenza',
  'ascoli calcio 1898': 'ascoli',
  'avellino 1912': 'avellino',
  'carrarese calcio': 'carrarese',
  'levadiakos': 'levadeiakos',
  'apo levadiakos': 'levadeiakos',
  'aris thessaloniki': 'aris',
  'gfs panetolikos': 'panetolikos',
  'asteras aktor': 'asteras tripolis',
  'aek athens': 'aek',
  'panathinaikos fc': 'panathinaikos',
  'sint truidense': 'st truiden',
  'r charleroi': 'charleroi',
  'cd nacional': 'nacional',
  'cs maritimo': 'maritimo',
  'sporting braga': 'sp braga',
  'athletic bilbao': 'ath bilbao'
};
const LEAGUE_ALIASES = {
  'germany bundesliga':'D1','germany 2 bundesliga':'D2',
  'italy serie a':'I1','italy serie b':'I2',
  'spain la liga':'SP1','spain segunda':'SP2',
  'france ligue 1':'F1','france ligue 2':'F2',
  'netherlands eredivisie':'N1','england premier league':'E0',
  'england championship':'E1','england league one':'E2','england league two':'E3',
  'belgium jupiler league':'B1','greece superleague':'G1',
  'greece super league':'G1','stoiximan super league':'G1',
  'liga portugal betclic':'P1',
  'spain segunda division':'SP2',
  'turkey superliga':'T1','turkey super league':'T1',
  'trendyol super lig':'T1','turkish super league':'T1'
};
function resolveLeague(name, config) {
  const n = normalize(name);
  const aliases = new Map(Object.entries(config.leagueAliases || {}).map(([k,v]) => [normalize(k),v]));
  for (const [code, title] of Object.entries(config.leagueNames || {})) aliases.set(normalize(title), code);
  for (const [alias, code] of Object.entries(LEAGUE_ALIASES)) if (!aliases.has(normalize(alias))) aliases.set(normalize(alias),code);
  return aliases.get(n) || null;
}
function findTeam(leagueTeams, name) {
  if (!leagueTeams || !name) return null;
  const n = normalize(name);
  const target = TEAM_ALIASES[n] || n;
  const matches = Object.entries(leagueTeams).filter(([key,team]) =>
    normalize(key) === target || normalize(team?.name) === target
  );
  return matches.length === 1 ? matches[0][1] : null;
}
function predictMean(model, features) {
  if (!model || model.type !== 'poisson') throw new Error('Неподдерживаемый тип модели.');
  const z = model.features.map((name, i) => {
    const raw = features[name];
    const value = Number.isFinite(raw) ? raw : Number(model.imputerMedian[i]);
    return (value - model.scalerMean[i]) / (model.scalerScale[i] || 1);
  });
  const eta = model.intercept + z.reduce((sum,value,i) => sum + value * model.coefficients[i],0);
  return Math.max(0.01, Math.min(12, Math.exp(Math.max(-5,Math.min(5,eta)))));
}
function poissonPmf(mean) {
  const values = [Math.exp(-mean)];
  let cumulative = values[0];
  for (let k=1; k<=100; k++) {
    values[k] = values[k-1] * mean / k;
    cumulative += values[k];
    if (k > mean && 1-cumulative < 1e-12) break;
  }
  const total = values.reduce((a,b)=>a+b,0);
  return values.map(v=>v/total);
}
function scoreDistribution(homeMean, awayMean) {
  const hp=poissonPmf(homeMean), ap=poissonPmf(awayMean);
  const matrix=[];
  let home=0, draw=0, away=0, btts=0;
  for(let h=0;h<hp.length;h++){
    matrix[h]=[];
    for(let a=0;a<ap.length;a++){
      const p=hp[h]*ap[a]; matrix[h][a]=p;
      if(h>a) home+=p; else if(h===a) draw+=p; else away+=p;
      if(h>0 && a>0) btts+=p;
    }
  }
  return {hp,ap,matrix,home,draw,away,btts};
}
function totalOver(pmf,line) {
  let under=0;
  for(let k=0;k<pmf.length;k++) if(k<line) under+=pmf[k];
  return 1-under;
}
function jointTotalOver(dist,line) {
  let over=0;
  for(let h=0;h<dist.hp.length;h++) for(let a=0;a<dist.ap.length;a++)
    if(h+a>line) over+=dist.matrix[h][a];
  return over;
}
function asianOutcome(dist, handicap) {
  let win=0,push=0,lose=0;
  for(let h=0;h<dist.hp.length;h++) for(let a=0;a<dist.ap.length;a++){
    const p=dist.matrix[h][a], result=h-a+handicap;
    if(Math.abs(result)<1e-9) push+=p; else if(result>0) win+=p; else lose+=p;
  }
  return {win,push,lose};
}
function pct(v){return (v*100).toFixed(1)+'%';}
function buildFeatures(home,away,league) {
  const features={home_advantage:1};
  for(const [side,team] of [['home',home],['away',away]]) {
    for(const [key,value] of Object.entries(team.stats || {})) {
      if (value !== null && Number.isFinite(Number(value))) features[side+'_'+key]=Number(value);
    }
  }
  for(const [key,value] of Object.entries(league || {})) {
    if(value !== null && Number.isFinite(Number(value))) features[key]=Number(value);
  }
  return features;
}
function predictFixture(fixture,config) {
  const leagueCode=resolveLeague(fixture.league,config);
  if(!leagueCode) return {skip:'Лига не найдена в исторической базе'};
  const teams=config.teams?.[leagueCode];
  const home=findTeam(teams,fixture.home), away=findTeam(teams,fixture.away);
  if(!home || !away) return {skip:'Одна или обе команды не сопоставлены'};
  const league=config.leagueStats?.[leagueCode] || config.leagueStats?.[config.leagueNames?.[leagueCode]];
  if(!league) return {skip:'Нет статистики лиги'};
  const features=buildFeatures(home,away,league);
  const homeMean=predictMean(config.models.homeGoals,features);
  const awayMean=predictMean(config.models.awayGoals,features);
  const dist=scoreDistribution(homeMean,awayMean);
  const overLines=[0.5,1.5,2.5,3.5,4.5];
  const teamLines=[0.5,1.5,2.5,3.5];
  const ahLines=[-1.5,-1,-0.5,0,0.5,1,1.5];
  const totals=overLines.map(line=>({line,over:jointTotalOver(dist,line)}));
  const homeTotals=teamLines.map(line=>({line,over:totalOver(dist.hp,line)}));
  const awayTotals=teamLines.map(line=>({line,over:totalOver(dist.ap,line)}));
  const ah=ahLines.map(line=>({line,...asianOutcome(dist,line)}));
  const awayAh=ahLines.map(line=>({line,...asianOutcome(dist,-line)}));
  return {
    fixture,leagueCode,homeMean,awayMean,
    outcome:{home:dist.home,draw:dist.draw,away:dist.away},
    doubleChance:{homeDraw:dist.home+dist.draw,drawAway:dist.draw+dist.away,homeAway:dist.home+dist.away},
    dnb:{home:dist.home/(dist.home+dist.away),away:dist.away/(dist.home+dist.away),refund:dist.draw},
    totals,homeTotals,awayTotals,btts:{yes:dist.btts,no:1-dist.btts},ah,awayAh
  };
}
function formatLines(items, label) {
  return items.map(x=>label(x)).join('\n');
}
const MIN_DISPLAY_PROBABILITY = 80;

function heatmapColor(probability) {
  const ratio = Math.max(0, Math.min(1, (probability - MIN_DISPLAY_PROBABILITY) / 20));
  const pale = [220, 252, 231];
  const dark = [20, 83, 45];
  const rgb = pale.map((start, index) => Math.round(start + (dark[index] - start) * ratio));
  return 'rgb(' + rgb.join(', ') + ')';
}

function highlightProbabilityCells(row, threshold = MIN_DISPLAY_PROBABILITY) {
  for (const cell of row.cells) {
    cell.style.backgroundColor = '#fff';
    cell.style.color = '#000';
    cell.removeAttribute('title');
    if (!cell.classList.contains('probability-cell')) continue;
    const probabilities = Array.from(cell.textContent.matchAll(/([0-9]+(?:[.,][0-9]+)?)\s*%/g))
      .map(match => Number.parseFloat(match[1].replace(',', '.')))
      .filter(Number.isFinite);
    if (!probabilities.length) continue;
    const highest = Math.max(...probabilities);
    if (highest > threshold) {
      cell.style.backgroundColor = heatmapColor(highest);
      cell.title = 'Максимальная вероятность: ' + highest.toFixed(1) + '%';
    }
  }
}

function render() {
  const query=$('market-search').value.trim().toLocaleLowerCase('ru');
  const visible=rows.filter(r=>[r.fixture.league,r.fixture.home,r.fixture.away].join(' ').toLocaleLowerCase('ru').includes(query));
  const body=$('markets-body'); body.replaceChildren();
  for(const r of visible) {
    const tr=document.createElement('tr');
    const cells=[
      new Date(r.fixture.time).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}),
      r.fixture.league,r.fixture.home+' — '+r.fixture.away,
       'П1: '+pct(r.outcome.home)+'\nX: '+pct(r.outcome.draw)+'\nП2: '+pct(r.outcome.away),
      '1X: '+pct(r.doubleChance.homeDraw)+'\nX2: '+pct(r.doubleChance.drawAway)+'\n12: '+pct(r.doubleChance.homeAway),
      'П1: '+pct(r.dnb.home)+'\nП2: '+pct(r.dnb.away)+'\nВозврат X: '+pct(r.dnb.refund)',
      formatLines(r.totals,x=>'ТБ '+x.line+': '+pct(x.over)+' / ТМ '+pct(1-x.over)),
      formatLines(r.homeTotals,x=>'ТБ '+x.line+': '+pct(x.over)),
      formatLines(r.awayTotals,x=>'ТБ '+x.line+': '+pct(x.over)),
      'Обе забьют — Да: '+pct(r.btts.yes)+'\nОбе забьют — Нет: '+pct(r.btts.no),
      formatLines(r.ah,x=>(x.line>0?'+':'')+x.line+'\nВыигрыш '+pct(x.win)+' · Возврат '+pct(x.push)+'\nПроигрыш '+pct(x.lose)),
      formatLines(r.awayAh,x=>(x.line>0?'+':'')+x.line+'\nВыигрыш '+pct(x.win)+' · Возврат '+pct(x.push)+'\nПроигрыш '+pct(x.lose))
    ];
    // Asian handicap probabilities remain calculated in r.ah/r.awayAh, but are hidden from this table.
    cells.length = 10;
    for(let i=0;i<cells.length;i++){
      const td=document.createElement('td'); td.textContent=cells[i]; if(i>=3) td.className='probability-cell'; tr.appendChild(td);
    }
    highlightProbabilityCells(tr);
    body.appendChild(tr);
  }
  $('markets-count').textContent='Матчей с расчётом: '+visible.length+' из '+rows.length;
  $('markets-empty').classList.toggle('hidden',visible.length>0);
}
async function load() {
  $('markets-error').classList.add('hidden');
  try {
    const [dataResponse,modelResponse,reportResponse]=await Promise.all([
      fetch('./data.json?t='+Date.now(),{cache:'no-store'}),
      fetch('./data/goals_markets_models.json?t='+Date.now(),{cache:'no-store'}),
      fetch('./data/goals_markets_report.json?t='+Date.now(),{cache:'no-store'})
    ]);
    if(!dataResponse.ok) throw new Error('Не удалось загрузить data.json: HTTP '+dataResponse.status);
    if(!modelResponse.ok) throw new Error('Не удалось загрузить файл модели: HTTP '+modelResponse.status);
    const data=await dataResponse.json(); modelConfig=await modelResponse.json();
    report=reportResponse.ok?await reportResponse.json():null;
    if(!modelConfig.models?.homeGoals || !modelConfig.models?.awayGoals || !modelConfig.teams || !modelConfig.leagueAliases)
      throw new Error('Структура файла модели не соответствует ожидаемой схеме.');
    const now=Date.now(), rawFixtures=(Array.isArray(data.predictions)?data.predictions:[])
      .filter(x=>Number.isFinite(Date.parse(x.time)) && Date.parse(x.time)>now)
      .sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
    const fixtureMap=new Map();
    for(const fixture of rawFixtures){
      const leagueKey=resolveLeague(fixture.league,modelConfig)||normalize(fixture.league);
      const homeKey=TEAM_ALIASES[normalize(fixture.home)]||normalize(fixture.home);
      const awayKey=TEAM_ALIASES[normalize(fixture.away)]||normalize(fixture.away);
      const key=[Date.parse(fixture.time),leagueKey,homeKey,awayKey].join('|');
      if(!fixtureMap.has(key)) fixtureMap.set(key,fixture);
    }
    const fixtures=Array.from(fixtureMap.values());
    let unmatched=0;
    rows=fixtures.map(f=>predictFixture(f,modelConfig)).filter(r=>{if(r.skip){unmatched++;return false;}return true;});
    $('markets-date').textContent='Расписание: '+(data.date||'—')+' · модель: '+(modelConfig.generatedAt||'—')+' · обучение по '+(modelConfig.trainingThrough||'—');
    $('markets-count').textContent='Сопоставлено '+rows.length+' из '+fixtures.length+' уникальных будущих матчей; пропущено без полной истории: '+unmatched;
    $('model-status').textContent='Статус: '+modelConfig.status+' · '+modelConfig.trainingRows+' исторических матчей';
    if(report?.markets){
      const h=report.markets.homeGoals,a=report.markets.awayGoals;
      const p=report.probabilityMarkets;
      const point='MAE голов — хозяева '+h.mae+' (baseline '+h.baselineMae+'), гости '+a.mae+' (baseline '+a.baselineMae+').';
      if(p?.['1X2']){
        const x=p['1X2'], t=p.matchTotals?.['2.5'], b=p.bothTeamsToScore;
        $('model-metrics').textContent=point+' Вероятности на holdout: 1X2 Brier '+x.brier+' / baseline '+x.baselineBrier+', LogLoss '+x.logLoss+' / baseline '+x.baselineLogLoss+
          (t?'; ТБ 2.5 Brier '+t.brier+' / baseline '+t.baselineBrier:'')+
          (b?'; обе забьют Brier '+b.brier+' / baseline '+b.baselineBrier:'')+'.';
      } else {
        $('model-metrics').textContent=point+' Метрики вероятностей рынков ещё не рассчитаны.';
      }
    }
    render();
  } catch(e) {
    $('markets-error').textContent=e.message||'Ошибка загрузки.';
    $('markets-error').classList.remove('hidden');
    $('markets-body').replaceChildren(); rows=[];
  }
}
if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded',()=>{
  $('market-search').addEventListener('input',render);
  $('refresh-btn').addEventListener('click',load);
  load();
});
if(typeof module!=='undefined' && module.exports) module.exports={normalize,resolveLeague,findTeam,predictMean,poissonPmf,scoreDistribution,jointTotalOver,totalOver,asianOutcome,buildFeatures,predictFixture};
