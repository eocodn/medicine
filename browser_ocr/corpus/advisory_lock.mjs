import { spawn } from "node:child_process";

const CONFLICT_EXIT_CODE = 75;
const HOLDER_SOURCE = "process.stdout.write('locked\\n'); process.stdin.resume();";

export function acquireAdvisoryLock(lockPath, { busyMessage, label = "advisory lock" } = {}) {
  if (!lockPath) throw new Error("advisory lock path is required");
  if (!busyMessage) throw new Error("advisory lock busy message is required");
  return new Promise((resolvePromise, reject) => {
    const child = spawn(
      "flock",
      ["--exclusive", "--nonblock", "--conflict-exit-code", String(CONFLICT_EXIT_CODE), lockPath, process.execPath, "-e", HOLDER_SOURCE],
      { cwd: process.cwd(), stdio: ["pipe", "pipe", "pipe"] },
    );
    let stdout = "";
    let stderr = "";
    let settled = false;
    const fail = (error) => {
      if (settled) return;
      settled = true;
      child.stdin.end();
      reject(error);
    };
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", (error) => fail(new Error(`${label} helper failed to start: ${error.message}`)));
    child.on("close", (code, signal) => {
      if (settled) return;
      if (code === CONFLICT_EXIT_CODE) {
        fail(new Error(busyMessage));
        return;
      }
      const suffix = signal ? `signal ${signal}` : `exit ${code}`;
      fail(new Error(`${label} helper exited before acquiring the lock (${suffix}): ${stderr.trim()}`));
    });
    child.stdout.on("data", (chunk) => {
      if (settled) return;
      stdout += chunk.toString();
      const newline = stdout.indexOf("\n");
      if (newline < 0) return;
      if (stdout.slice(0, newline) !== "locked") {
        fail(new Error(`${label} helper returned invalid readiness output`));
        return;
      }
      settled = true;
      resolvePromise({ child, label });
    });
  });
}

export function releaseAdvisoryLock(lock) {
  const { child, label } = lock;
  return new Promise((resolvePromise, reject) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      if (child.exitCode === 0) resolvePromise();
      else reject(new Error(`${label} helper exited before release`));
      return;
    }
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.once("error", reject);
    child.once("close", (code, signal) => {
      if (code === 0) resolvePromise();
      else {
        const suffix = signal ? `signal ${signal}` : `exit ${code}`;
        reject(new Error(`${label} helper release failed (${suffix}): ${stderr.trim()}`));
      }
    });
    child.stdin.end();
  });
}
