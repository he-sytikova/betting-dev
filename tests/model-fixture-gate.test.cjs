const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { filterModelFixtures, loadModels } = require('../scripts/lines/filter_model_fixtures.cjs');

const projectRoot = path.resolve(__dirname, '..');
const models = loadModels(projectRoot);
const now = Date.parse('2026-11-17T00:00:00Z');

test('model fixture gate accepts UEFA Nations League and rejects lookalike competitions', () => {
  const result = filterModelFixtures({ predictions: [
    { home: 'Poland', away: 'Sweden', league: 'UEFA Nations League', time: '2026-11-17T19:45:00Z' },
    { home: 'Poland', away: 'Sweden', league: 'CONCACAF Nations League', time: '2026-11-17T19:45:00Z' },
    { home: 'Hungary U19', away: 'Czech Republic U19', league: 'UEFA European U19 Championship Qualification', time: '2026-11-17T11:00:00Z' },
  ] }, models, now);
  assert.equal(result.acceptedFixtures, 1);
  assert.equal(result.fixtures[0].modelCoverage, 'nt');
  assert.equal(result.fixtures[0].home, 'Poland');
  assert.equal(result.rejected.unsupported_by_models, 2);
  assert.equal(result.fixtures[0].markets, undefined);
});

test('model fixture gate uses the actual club model including team history eligibility', () => {
  const minimum = models.goals.runtimePolicy.minimumTeamMatches || 0;
  let fixture = null;
  for (const [leagueCode, teams] of Object.entries(models.goals.teams)) {
    const eligible = Object.values(teams).filter(team => team.matchesBefore >= minimum);
    if (eligible.length >= 2) {
      fixture = {
        home: eligible[0].name,
        away: eligible[1].name,
        league: models.goals.leagueNames[leagueCode],
        time: '2026-11-18T19:45:00Z',
        eventId: 'preserve-me',
      };
      break;
    }
  }
  assert.ok(fixture);
  const result = filterModelFixtures([fixture], models, now);
  assert.equal(result.acceptedFixtures, 1);
  assert.equal(result.fixtures[0].modelCoverage, 'goals');
  assert.equal(result.fixtures[0].eventId, 'preserve-me');
});

test('model fixture gate accepts UEFA club cups and rejects lookalike competitions', () => {
  const result = filterModelFixtures({ predictions: [
    { home: 'Real Madrid CF', away: 'PSV Eindhoven', league: 'UEFA Champions League', time: '2026-11-17T19:45:00Z' },
    { home: 'Real Madrid CF', away: 'PSV Eindhoven', league: 'Champions League', time: '2026-11-17T19:45:00Z' },
    { home: 'Newcastle Jets', away: 'Kashiwa Reysol', league: 'AFC Champions League Elite', time: '2026-11-17T07:45:00Z' },
    { home: 'Real Madrid CF', away: 'PSV Eindhoven', league: 'UEFA Champions League. Team vs Player', time: '2026-11-17T19:45:00Z' },
    { home: 'Real Madrid Youth', away: 'PSV Youth', league: 'UEFA Youth League', time: '2026-11-17T15:00:00Z' },
  ] }, models, now);
  assert.equal(result.acceptedFixtures, 2);
  assert.ok(result.fixtures.every(fixture => fixture.modelCoverage === 'euro'));
  assert.equal(result.rejected.unsupported_by_models, 3);
});

test('model fixture gate rejects past and malformed fixtures before detail requests', () => {
  const result = filterModelFixtures({ fixtures: [
    { home: 'Poland', away: 'Sweden', league: 'UEFA Nations League', time: '2026-11-16T19:45:00Z' },
    { home: 'Poland', away: 'Sweden', league: 'UEFA Nations League', time: 'not-a-date' },
    { home: 'Poland', league: 'UEFA Nations League', time: '2026-11-17T19:45:00Z' },
  ] }, models, now);
  assert.deepEqual(result.rejected, {
    missing_fields: 1,
    invalid_time: 1,
    past: 1,
    unsupported_by_models: 0,
  });
  assert.equal(result.acceptedFixtures, 0);
});
