'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Module = require('node:module');

test('editor bridge authenticates and selects only the requested terminal', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'maixy-editor-test-'));
  const previous = process.env.MAIXY_HOME;
  process.env.MAIXY_HOME = root;
  const selected = [];
  const mock = {
    window: {terminals: [10, 20].map(pid => ({
      processId: Promise.resolve(pid), name: `Terminal ${pid}`, show: focus => selected.push([pid, focus])
    }))},
    workspace: {name: 'Project'}, env: {appName: 'Visual Studio Code'}
  };
  const originalLoad = Module._load;
  let extension;
  try {
    Module._load = function(name, ...args) {
      return name === 'vscode' ? mock : originalLoad.call(this, name, ...args);
    };
    extension = require('../editors/vscode/extension');
  } finally {
    Module._load = originalLoad;
  }
  const context = {subscriptions: []};
  try {
    extension.activate(context);
    const directory = path.join(root, 'editor-bridges');
    for (let i = 0; i < 100 && fs.readdirSync(directory).length === 0; ++i) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    const descriptorPath = path.join(directory, fs.readdirSync(directory)[0]);
    const descriptor = JSON.parse(fs.readFileSync(descriptorPath));
    assert.equal(fs.statSync(descriptorPath).mode & 0o777, 0o600);
    const url = `http://127.0.0.1:${descriptor.port}`;
    assert.equal((await fetch(url + '/terminals')).status, 403);
    const headers = {Authorization: `Bearer ${descriptor.token}`};
    const terminals = await (await fetch(url + '/terminals', {headers})).json();
    assert.deepEqual(terminals.map(t => t.pid), [10, 20]);
    assert.equal((await fetch(url + '/focus', {method: 'POST', headers, body: JSON.stringify({pid: 20})})).status, 200);
    assert.deepEqual(selected, [[20, false]]);
    assert.equal((await fetch(url + '/focus', {method: 'POST', headers, body: JSON.stringify({pid: 99})})).status, 404);
    assert.deepEqual(selected, [[20, false]]);
  } finally {
    for (const subscription of context.subscriptions) subscription.dispose();
    if (previous === undefined) delete process.env.MAIXY_HOME;
    else process.env.MAIXY_HOME = previous;
    fs.rmSync(root, {recursive: true, force: true});
  }
});
