// Сниппет для выполнения во вкладке smart-tables.ru (через InAppBrowser evaluate).
// Забирает список матчей дня и их прогнозы (corners/yellowcards) под сессией владельца.
// Режим: 1–2 запуска в день, пауза между запросами — условия реестра источников (SMART_TABLES).
// Использование: const out = await stFetchDay('2026-11-24'); затем сохранить JSON в scratch.
async function stFetchDay(matchDate, pauseMs = 400) {
  const listResp = await fetch('/api/v1/match-center?matchDate=' + matchDate + '&relatedEntities=home_team,away_team,competition');
  const listJson = await listResp.json();
  const items = (listJson.data && listJson.data.items) || [];
  const result = { date: matchDate, matches: [] };
  for (const m of items) {
    const entry = {
      stMatchId: m.id,
      competition: m.competition && (m.competition.title || m.competition.common_title),
      competitionSlug: m.competition && m.competition.slug,
      home: m.home_team && m.home_team.title,
      away: m.away_team && m.away_team.title,
      beginAt: m.begin_at,
      status: m.status,
      predictions: null,
    };
    try {
      const r = await fetch('/match-center/' + m.id + '/predictions', { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      const t = await r.text();
      const mm = t.match(/<script type="application\/json"[^>]*>([\s\S]*?)<\/script>/);
      if (mm) {
        const j = JSON.parse(mm[1]);
        const pick = {};
        for (const [cat, arr] of Object.entries(j.predictions || {})) {
          pick[cat] = arr.map(p => ({ title: p.short_title, odd: p.odd }));
        }
        entry.predictions = pick;
      }
    } catch (e) {
      entry.error = String(e && e.message || e);
    }
    result.matches.push(entry);
    await new Promise(res => setTimeout(res, pauseMs));
  }
  return result;
}
