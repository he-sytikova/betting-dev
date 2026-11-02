const test=require('node:test');
const assert=require('node:assert/strict');
const {parse1x2,parseLines,aggregate}=require('../api-consensus.js');

test('normalizes 1X2 source probabilities before deriving outcomes',()=>{
 const p=parse1x2('П1 50.8% / X 23.9% / П2 25.3%');
 assert.ok(p);assert.ok(Math.abs(p.home+p.draw+p.away-1)<1e-12);
});
test('rejects incomplete and malformed 1X2 probabilities',()=>{
 assert.equal(parse1x2('П1 50% / X 25%'),null);
 assert.equal(parse1x2('П1 80% / X 30% / П2 20%'),null);
});
test('keeps totals separated by direction and exact line',()=>{
 const p=parseLines('ТБ 1.5: 87.8% / ТБ 2.5: 71.6% / ТБ 3.5: 49.6%','goals');
 const u=parseLines('ТМ 1.5: 12.2% / ТМ 2.5: 28.4%','goals');
 assert.deepEqual(Object.keys(p),['ТБ 1.5','ТБ 2.5','ТБ 3.5']);
 assert.equal(u['ТМ 1.5'],.122);
});
test('parses corner lines with abbreviated prefix',()=>{
 const p=parseLines('ТБ угл. 8.5: 59.7% / 9.5: 47.9% / 10.5: 36.2%','corners');
 assert.deepEqual(p,{'8.5':.597,'9.5':.479,'10.5':.362});
});
test('aggregates exact fixture from multiple providers and derives double chance',()=>{
 const future='2099-01-01T12:00:00Z';
 const rows=aggregate([
  {source:'A',time:future,home:'Alpha FC',away:'Beta',probabilityOutcome:'П1 50% / X 25% / П2 25%',totalGoals:'ТБ 2.5: 60%'},
  {source:'B',time:future,home:'Alpha',away:'Beta',probabilityOutcome:'П1 40% / X 30% / П2 30%',totalGoals:'ТБ 2.5: 80%'}
 ]);
 assert.equal(rows.length,1);assert.equal(rows[0].sourceCount,2);
 assert.equal(rows[0].derived.oneX,.725);
 assert.equal(rows[0].totals['ТБ 2.5'],.7);
});
test('does not invent corners or cards when providers omit them',()=>{
 const rows=aggregate([{source:'A',time:'2099-01-01T12:00:00Z',home:'Alpha',away:'Beta',probabilityOutcome:'П1 50% / X 25% / П2 25%'}]);
 assert.deepEqual(rows[0].corners,{});
});
