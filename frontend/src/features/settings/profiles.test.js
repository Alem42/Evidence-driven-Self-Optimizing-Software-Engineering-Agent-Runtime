import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeProfiles,parseProfile,profileReady,profileLabel} from './profiles.js';

test('old backend cannot crash API list rendering',()=>{
  assert.deepEqual(normalizeProfiles({name:'legacy'}).profiles,[]);
  assert.equal(normalizeProfiles({}).compatible,false);
  assert.equal(normalizeProfiles({profiles:[]}).compatible,true);
});
test('keyless local and keyed cloud models are usable, disabled ones are never ready',()=>{
  assert.equal(profileReady({model_type:'local',key_configured:false}),true);
  assert.equal(profileReady({model_type:'cloud',key_configured:false}),false);
  assert.equal(profileReady({model_type:'local',enabled:false,ready:true}),false);
  assert.equal(profileLabel({model:'local',model_type:'local',level:1}),'local · 本地 L1');
  assert.equal(parseProfile('{"base_url":"http://localhost:11434","model":"local","model_type":"local","level":1}').model_type,'local');
});
test('JSON import supports aliases and rejects unexpected structures',()=>{
  assert.equal(parseProfile('{"baseURL":"https://example.com/v1","model":"demo","apiKey":"synthetic"}').api_key,'synthetic');
  for(const s of ['[]','null','{}','{"base_url":3,"model":"x"}']) assert.throws(()=>parseProfile(s));
});
