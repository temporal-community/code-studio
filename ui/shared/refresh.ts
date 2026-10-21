/** Serialize refreshes and collapse bursts into one trailing catch-up. */
export function coalesceRefresh(task: () => Promise<void>, delay = 100) {
  let running: Promise<void> | null = null;
  let pending = false, stopped = false;
  async function drain() {
    do {
      pending = false;
      await new Promise(resolve => setTimeout(resolve, delay));
      if (stopped) return;
      // Requests received during the debounce are satisfied by this pass.
      pending = false;
      await task();
    } while (pending && !stopped);
  }
  const refresh = () => {
    if (stopped) return Promise.resolve();
    pending = true;
    if (!running) running = drain().finally(() => { running = null; });
    return running;
  };
  refresh.dispose = () => { stopped = true; pending = false; };
  return refresh;
}
