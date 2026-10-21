import { test } from 'node:test';
import assert from 'node:assert/strict';

const { workspaceConnection } = await import(process.env.WORKSPACE_CONNECTION_MODULE);

function setup(t) {
  const sockets = [];
  class Socket {
    static OPEN = 1;
    readyState = 0;
    sent = [];
    closed = false;
    constructor() { sockets.push(this); }
    open() { this.readyState = 1; this.onopen?.(); }
    close() { this.closed = true; this.readyState = 3; this.onclose?.(); }
    send(value) { this.sent.push(JSON.parse(value)); }
  }
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const target = new EventTarget();
  const oldWindow = globalThis.window, oldSocket = globalThis.WebSocket;
  globalThis.window = target;
  globalThis.WebSocket = Socket;
  let received = [], opens = 0;
  const connection = workspaceConnection('ws://example/workspace/connect', {
    open: () => opens++, message: event => received.push(event.data), offline() {},
  });
  t.after(() => {
    connection.close();
    globalThis.window = oldWindow;
    globalThis.WebSocket = oldSocket;
  });
  return { sockets, target, connection, received, opens: () => opens };
}

test('pagehide releases presence and pageshow restores exactly one connection', t => {
  const { sockets, target, connection, opens } = setup(t);
  sockets[0].open();
  target.dispatchEvent(new Event('pageshow'));
  assert.equal(sockets.length, 1, 'initial pageshow must not duplicate mount');
  t.mock.timers.tick(15_000);
  assert.deepEqual(sockets[0].sent, [{ type: 'ping' }]);
  target.dispatchEvent(new Event('pagehide'));
  assert.equal(sockets[0].closed, true);
  t.mock.timers.tick(60_000);
  assert.equal(sockets.length, 1, 'a cached page must not reconnect');
  assert.equal(sockets[0].sent.length, 1, 'a cached page must stop heartbeats');
  target.dispatchEvent(new Event('pageshow'));
  target.dispatchEvent(new Event('pageshow'));
  assert.equal(sockets.length, 2);
  sockets[1].open();
  connection.send({ type: 'presence', file: 'planner_agent.py' });
  assert.equal(opens(), 2);
  assert.deepEqual(sockets[1].sent, [{ type: 'presence', file: 'planner_agent.py' }]);
});

test('navigation cancels queued reconnects, including while connecting', t => {
  const { sockets, target } = setup(t);
  sockets[0].close();
  target.dispatchEvent(new Event('pagehide'));
  t.mock.timers.tick(10_000);
  assert.equal(sockets.length, 1);
  target.dispatchEvent(new Event('pageshow'));
  assert.equal(sockets.length, 2);
  target.dispatchEvent(new Event('pagehide'));
  assert.equal(sockets[1].closed, true);
  t.mock.timers.tick(10_000);
  assert.equal(sockets.length, 2);
});

test('late events from an old connection cannot corrupt its replacement', t => {
  const { sockets, target, received, opens } = setup(t);
  const oldOpen = sockets[0].onopen, oldMessage = sockets[0].onmessage, oldClose = sockets[0].onclose;
  target.dispatchEvent(new Event('pagehide'));
  target.dispatchEvent(new Event('pageshow'));
  sockets[1].open();
  oldOpen(); oldMessage({ data: 'stale' }); oldClose();
  sockets[1].onmessage({ data: 'current' });
  t.mock.timers.tick(5000);
  assert.deepEqual(received, ['current']);
  assert.equal(opens(), 1);
  assert.equal(sockets.length, 2);
});

test('a network disconnect reconnects, but disposed components stay closed', t => {
  const { sockets, target, connection } = setup(t);
  sockets[0].open(); sockets[0].close();
  t.mock.timers.tick(500);
  assert.equal(sockets.length, 2);
  connection.close();
  target.dispatchEvent(new Event('pageshow'));
  t.mock.timers.tick(60_000);
  assert.equal(sockets.length, 2);
  assert.equal(sockets[1].closed, true);
});
