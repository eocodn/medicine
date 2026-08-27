import { spawn } from "node:child_process";
import { open, type FileHandle } from "node:fs/promises";

const CONFLICT_EXIT_CODE = 75;
const CHILD_LOCK_FD = 3;

export interface AdvisoryLockOptions {
  busyMessage?: string;
  label?: string;
}

export interface AdvisoryLock {
  fileHandle: FileHandle;
  label: string;
}

function acquisitionError(label: string, code: number | null, signal: NodeJS.Signals | null, stderr: string): Error {
  const suffix = signal ? `signal ${signal}` : `exit ${code}`;
  const detail = stderr.trim();
  return new Error(`${label} acquisition helper failed (${suffix})${detail ? `: ${detail}` : ""}`);
}

function acquireKernelLock(
  fileHandle: FileHandle,
  busyMessage: string,
  label: string,
): Promise<void> {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(
      "flock",
      [
        "--exclusive",
        "--nonblock",
        "--conflict-exit-code",
        String(CONFLICT_EXIT_CODE),
        String(CHILD_LOCK_FD),
      ],
      { stdio: ["ignore", "ignore", "pipe", fileHandle.fd] },
    );
    let stderr = "";
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      if (error) reject(error);
      else resolvePromise();
    };
    child.stderr?.on("data", (chunk) => { stderr += chunk.toString(); });
    child.once("error", (error) => {
      finish(new Error(`${label} acquisition helper failed to start: ${error.message}`));
    });
    child.once("close", (code, signal) => {
      if (code === 0) {
        finish();
        return;
      }
      if (code === CONFLICT_EXIT_CODE) {
        finish(new Error(busyMessage));
        return;
      }
      finish(acquisitionError(label, code, signal, stderr));
    });
  });
}

export async function acquireAdvisoryLock(
  lockPath: string,
  { busyMessage, label = "advisory lock" }: AdvisoryLockOptions = {},
): Promise<AdvisoryLock> {
  if (!lockPath) throw new Error("advisory lock path is required");
  if (!busyMessage) throw new Error("advisory lock busy message is required");

  const fileHandle = await open(lockPath, "a+");
  try {
    await acquireKernelLock(fileHandle, busyMessage, label);
    return { fileHandle, label };
  } catch (error) {
    await fileHandle.close().catch(() => {});
    throw error;
  }
}

export async function releaseAdvisoryLock(lock: AdvisoryLock): Promise<void> {
  try {
    await lock.fileHandle.close();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`${lock.label} release failed: ${detail}`);
  }
}
