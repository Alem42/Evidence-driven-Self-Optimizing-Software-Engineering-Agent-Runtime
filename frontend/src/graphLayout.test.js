import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutGraph} from './graphLayout.js';

test('unordered diamond dependencies stay to the left of their consumers', () => {
  const nodes = [{id:'gate',dependencies:['a','b']}, {id:'b',dependencies:['root']},
    {id:'root',dependencies:[]}, {id:'a',dependencies:['root']}];
  const before = JSON.stringify(nodes);
  const {positions:p, height} = layoutGraph(nodes);
  assert.ok(p.root.x < p.a.x && p.a.x < p.gate.x);
  assert.equal(p.a.x, p.b.x);
  assert.notEqual(p.a.y, p.b.y);
  assert.ok(height > p.a.y + 94 && height > p.b.y + 94);
  assert.equal(JSON.stringify(nodes), before);
});

test('full verification has three roots feeding one gate without overlaps', () => {
  const nodes = ['test','vet','format'].map(id => ({id,dependencies:[]}));
  nodes.push({id:'gate',dependencies:['test','vet','format']});
  const {positions:p, height, width} = layoutGraph(nodes);
  assert.equal(new Set(nodes.slice(0,3).map(n => p[n.id].y)).size, 3);
  assert.ok(p.gate.x > p.test.x && height >= p.format.y + 94 && width >= p.gate.x + 210);
});
