import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeProfiles,parseProfile} from './apiProfiles.js';

test('old backend cannot crash API list rendering',()=>{
  assert.deepEqual(normalizeProfiles({name:'legacy'}).profiles,[]);
  assert.equal(normalizeProfiles({}).compatible,false);
  assert.equal(normalizeProfiles({profiles:[]}).compatible,true);
});
test('JSON import supports aliases and rejects unexpected structures',()=>{
  assert.equal(parseProfile('{"baseURL":"https://example.com/v1","model":"demo","apiKey":"synthetic"}').api_key,'synthetic');
  for(const s of ['[]','null','{}','{"base_url":3,"model":"x"}']) assert.throws(()=>parseProfile(s));
});
