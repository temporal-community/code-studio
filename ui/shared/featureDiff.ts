/** Apply an asynchronous result only while the state that requested it is current. */
export async function applyIfCurrent<T>(
  request: Promise<T>,
  isCurrent: () => boolean,
  apply: (result: T) => void,
): Promise<boolean> {
  const result = await request;
  if (!isCurrent()) return false;
  apply(result);
  return true;
}


/** Detect a changed saved file without applying its contents over an editor draft. */
export async function detectRemoteChange(
  request: Promise<{ hash: string }>,
  expectedHash: string,
  isCurrent: () => boolean,
  onChanged: () => void,
): Promise<boolean> {
  const result = await request;
  if (!isCurrent() || result.hash === expectedHash) return false;
  onChanged();
  return true;
}
