// Offline checks for configuration guards. No database or network access.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const nodes = JSON.parse(fs.readFileSync(path.join(__dirname, '../node-red/flows.json')));
const functions = nodes.filter(n => n.type === 'function');
for (const node of functions) new Function('msg', 'env', 'flow', 'node', node.func);
function run(name, msg, config = {}) {
  const source = nodes.find(n => n.name === name).func;
  return new Function('msg', 'env', source)(msg, {get: key => config[key]});
}
function checkin(key, config, payload = {usn: ' demo01 '}) {
  return run('Validate check-in device', {req: {headers: {'x-continio-key': key}}, payload}, config);
}
assert.equal(checkin('', {})[1].statusCode, 401);
assert.equal(checkin('wrong', {CONTINIO_DEVICE_KEY: 'test-key'})[1].statusCode, 401);
assert.equal(checkin('test-key', {CONTINIO_DEVICE_KEY: 'test-key'})[0].checkinIdentifier, 'DEMO01');
assert.equal(checkin('test-key', {CONTINIO_DEVICE_KEY: 'test-key'}, {usn: 'a', rfid_uid: 'b'})[1].statusCode, 400);
function register(code, config) {
  return run('Validate registration', {payload: {role: 'admin', name: 'Demo', email: 'demo@example.test', mobile: '0000000000', password: 'test-password', admin_code: code}}, config);
}
assert.equal(register('', {})[2].statusCode, 403);
assert.equal(register('wrong', {CONTINIO_ADMIN_CODE: 'test-code'})[2].statusCode, 403);
assert.equal(register('test-code', {CONTINIO_ADMIN_CODE: 'test-code'})[1].reg.role, 'admin');
console.log(`${functions.length} function nodes compile; device and admin guards passed.`);
