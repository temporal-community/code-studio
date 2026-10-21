interface Handlers {
  open?: () => void;
  message: (event: MessageEvent) => void;
  offline: () => void;
}

// Component teardown does not run when a browser caches a whole page. Release
// presence on pagehide as well, and reconnect when that same page is restored.
export function workspaceConnection(url: URL | string, handlers: Handlers) {
  let socket: WebSocket | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let suspended = false, disposed = false, attempt = 0;

  function clearTimers() {
    clearTimeout(retry); retry = undefined;
    clearInterval(heartbeat); heartbeat = undefined;
  }

  function disconnect() {
    clearTimers();
    const previous = socket;
    socket = undefined;
    if (previous) {
      previous.onopen = previous.onmessage = previous.onclose = previous.onerror = null;
      previous.close();
      handlers.offline();
    }
  }

  function send(data: unknown) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
  }

  function connect() {
    if (disposed || suspended || socket) return;
    clearTimeout(retry); retry = undefined;
    const current = new WebSocket(url);
    socket = current;
    current.onopen = () => {
      if (socket !== current) return;
      attempt = 0;
      heartbeat = setInterval(() => send({ type: 'ping' }), 15_000);
      handlers.open?.();
    };
    current.onmessage = event => {
      if (socket === current) handlers.message(event);
    };
    current.onclose = () => {
      if (socket !== current) return;
      socket = undefined;
      clearTimers();
      handlers.offline();
      if (!disposed && !suspended) retry = setTimeout(connect, Math.min(5000, 500 * 2 ** attempt++));
    };
    current.onerror = () => { if (socket === current) current.close(); };
  }

  function hide() { suspended = true; disconnect(); }
  function show() { suspended = false; connect(); }
  window.addEventListener('pagehide', hide);
  window.addEventListener('pageshow', show);
  connect();

  return {
    send,
    close() {
      disposed = true;
      window.removeEventListener('pagehide', hide);
      window.removeEventListener('pageshow', show);
      disconnect();
    },
  };
}
