export type ClipboardWriter = (output: string) => Promise<void>;

export async function copyOutput(output: string, writer?: ClipboardWriter): Promise<boolean> {
  try {
    const write = writer ?? ((value: string) => {
      const clipboard = globalThis.navigator?.clipboard;
      if (!clipboard) throw new Error('Clipboard API unavailable');
      return clipboard.writeText(value);
    });
    await write(output);
    return true;
  } catch {
    return false;
  }
}
