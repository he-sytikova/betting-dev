#!/usr/bin/env node
'use strict';

// Reuse every active candidate runtime as an eligibility gate. No probabilities
// are recalculated or exported: acquisition receives only fixtures that at
// least one homepage candidate model can actually score.
const fs = require('node:fs');
const path = require('node:path');
const goals = require('../../goals-internal-model.js');
const corners = require('../../internal-model.js');
const stats = require('../../stats-internal-model.js');
const nt = require('../../nt-internal-model.js');

function fixtureRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === 'object') {
    for (const key of ['predictions', 'fixtures', 'matches']) {
      if (Array.isArray(payload[key])) return payload[key];
    }
  }
  throw new Error('Fixture input must be a JSON object or array');
}

function coverage(fixture, models) {
  const args = [fixture.league, fixture.home, fixture.away];
  for (const [label, api, model] of [
    ['goals', goals, models.goals],
    ['cornersCards', corners, models.cornersCards],
    ['stats', stats, models.stats],
    ['nt', nt, models.nt],
  ]) {
    if (model && api.predict(model, ...args)) return label;
  }
  return null;
}

function filterModelFixtures(payload, models, now = Date.now()) {
  const rows = fixtureRows(payload);
  const rejected = { missing_fields: 0, invalid_time: 0, past: 0, unsupported_by_models: 0 };
  const fixtures = [];
  for (const fixture of rows) {
    if (!fixture || typeof fixture !== 'object' || !fixture.home || !fixture.away || !(fixture.time || fixture.start_time)) {
      rejected.missing_fields += 1;
      continue;
    }
    const timestamp = Date.parse(fixture.time || fixture.start_time);
    if (!Number.isFinite(timestamp)) {
      rejected.invalid_time += 1;
      continue;
    }
    if (timestamp <= now) {
      rejected.past += 1;
      continue;
    }
    const modelCoverage = coverage(fixture, models);
    if (!modelCoverage) {
      rejected.unsupported_by_models += 1;
      continue;
    }
    fixtures.push({
      ...fixture,
      time: new Date(timestamp).toISOString(),
      modelCoverage,
    });
  }
  return {
    generatedAt: new Date(now).toISOString(),
    sourceFixtures: rows.length,
    acceptedFixtures: fixtures.length,
    rejected,
    fixtures,
  };
}

function loadModels(projectRoot) {
  return {
    goals: JSON.parse(fs.readFileSync(path.join(projectRoot, 'data/goals_markets_models.json'), 'utf8')),
    cornersCards: JSON.parse(fs.readFileSync(path.join(projectRoot, 'data/corners_cards_models.json'), 'utf8')),
    stats: JSON.parse(fs.readFileSync(path.join(projectRoot, 'data/stats_markets_models.json'), 'utf8')),
    nt: JSON.parse(fs.readFileSync(path.join(projectRoot, 'data/nt_model.json'), 'utf8')),
  };
}

function main(argv) {
  const [input, output] = argv;
  if (!input || !output) throw new Error('Usage: filter_model_fixtures.cjs <input.json> <output.json>');
  const projectRoot = path.resolve(__dirname, '../..');
  const payload = JSON.parse(fs.readFileSync(input, 'utf8'));
  const result = filterModelFixtures(payload, loadModels(projectRoot));
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n');
}

if (require.main === module) main(process.argv.slice(2));

module.exports = { fixtureRows, coverage, filterModelFixtures, loadModels };
