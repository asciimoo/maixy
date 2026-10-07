'use strict';
const vscode = require('vscode');
const http = require('node:http');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function stateHome() {
  const override = process.env.MAIXY_HOME || process.env.LOGIAI_HOME;
  if (override) return override.replace(/^~(?=\/|$)/, os.homedir());
  const legacy = path.join(os.homedir(), '.local/share/logiai');
  return fs.existsSync(path.join(legacy, 'state.sqlite3')) ? legacy : path.join(os.homedir(), '.local/share/maixy');
}

async function terminals() {
  return (await Promise.all(vscode.window.terminals.map(async terminal => ({
    pid: await terminal.processId,
    name: terminal.name,
    workspace: vscode.workspace.name || ''
  })))).filter(t => Number.isInteger(t.pid));
}

function activate(context) {
  // Remote terminal PIDs belong to another machine's process namespace.
  if (vscode.env.remoteName) return;
  const token = crypto.randomBytes(32).toString('hex');
  const directory = path.join(stateHome(), 'editor-bridges');
  fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  const descriptor = path.join(directory, `${process.pid}-${crypto.randomUUID()}.json`);
  const server = http.createServer(async (request, response) => {
    if (request.headers.authorization !== `Bearer ${token}`) {
      response.writeHead(403).end();
      return;
    }
    try {
      if (request.method === 'GET' && request.url === '/terminals') {
        response.writeHead(200, {'Content-Type': 'application/json'}).end(JSON.stringify(await terminals()));
        return;
      }
      if (request.method === 'POST' && request.url === '/focus') {
        let data = '';
        for await (const chunk of request) {
          data += chunk;
          if (data.length > 4096) {
            response.writeHead(413).end();
            return;
          }
        }
        const pid = JSON.parse(data).pid;
        if (!Number.isInteger(pid)) {
          response.writeHead(400).end();
          return;
        }
        for (const terminal of vscode.window.terminals) {
          if (await terminal.processId === pid) {
            terminal.show(false);
            response.writeHead(200).end('{}');
            return;
          }
        }
        response.writeHead(404).end();
        return;
      }
      response.writeHead(404).end();
    } catch {
      if (!response.headersSent) response.writeHead(500);
      response.end();
    }
  });
  server.on('error', () => { try { fs.unlinkSync(descriptor); } catch {} });
  server.listen(0, '127.0.0.1', () => {
    fs.writeFileSync(descriptor, JSON.stringify({
      port: server.address().port, token, pid: process.pid, application: vscode.env.appName
    }), {mode: 0o600});
  });
  context.subscriptions.push({dispose() {
    server.close();
    try { fs.unlinkSync(descriptor); } catch {}
  }});
}

module.exports = {activate};
