import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

import { acquireAdvisoryLock, releaseAdvisoryLock } from "../advisory_lock.mts";

const options = {
  busyMessage: "fixture lock is already held",
  label: "fixture lock",
};

test("advisory lock uses live kernel ownership instead of lock-file existence", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-corpus-advisory-lock-"));
  try {
    await mkdir(root, { recursive: true });
    const lockPath = join(root, ".fixture.lock");
    await writeFile(lockPath, "stale-owner-metadata\n");

    const first = await acquireAdvisoryLock(lockPath, options);
    try {
      await assert.rejects(acquireAdvisoryLock(lockPath, options), /already held/);
    } finally {
      await releaseAdvisoryLock(first);
    }

    const second = await acquireAdvisoryLock(lockPath, options);
    await releaseAdvisoryLock(second);
    assert.equal(await readFile(lockPath, "utf8"), "stale-owner-metadata\n");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});


test("advisory lock ownership survives loss of acquisition helper processes", async () => {
  const root = await mkdtemp(join(tmpdir(), "medicine-corpus-advisory-lock-owner-"));
  const lockPath = join(root, ".fixture.lock");
  const helperUrl = pathToFileURL(join(process.cwd(), "browser_ocr/corpus/advisory_lock.mts")).href;
  const ownerSource = `
    import { acquireAdvisoryLock } from ${JSON.stringify(helperUrl)};
    await acquireAdvisoryLock(process.env.LOCK_PATH, {
      busyMessage: "fixture lock is already held",
      label: "fixture lock owner",
    });
    process.stdout.write("locked\\n");
    setInterval(() => {}, 1000);
  `;
  const owner = spawn(process.execPath, ["--input-type=module", "-e", ownerSource], {
    env: { ...process.env, LOCK_PATH: lockPath },
    stdio: ["ignore", "pipe", "inherit"],
  });
  try {
    await new Promise((resolvePromise, reject) => {
      let stdout = "";
      const onData = (chunk) => {
        stdout += chunk.toString();
        if (!stdout.includes("locked\n")) return;
        owner.stdout.off("data", onData);
        owner.off("close", onClose);
        resolvePromise();
      };
      const onClose = (code, signal) => reject(new Error(`lock owner exited before readiness (${signal || code})`));
      owner.stdout.on("data", onData);
      owner.on("close", onClose);
    });

    const childrenPath = `/proc/${owner.pid}/task/${owner.pid}/children`;
    const childPids = (await readFile(childrenPath, "utf8"))
      .trim()
      .split(/\s+/u)
      .filter(Boolean)
      .map(Number);
    for (const pid of childPids) process.kill(pid, "SIGKILL");
    if (childPids.length > 0) {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
    }

    const concurrent = await acquireAdvisoryLock(lockPath, options).then(
      (lock) => ({ lock }),
      (error) => ({ error }),
    );
    if (concurrent.lock) {
      await releaseAdvisoryLock(concurrent.lock);
      assert.fail("live owner must retain lock after acquisition helpers exit");
    }
    assert.match(concurrent.error.message, /already held/);

    owner.kill("SIGKILL");
    await new Promise((resolvePromise) => owner.once("close", resolvePromise));
    const afterCrash = await acquireAdvisoryLock(lockPath, options);
    await releaseAdvisoryLock(afterCrash);
  } finally {
    if (owner.exitCode === null && owner.signalCode === null) {
      owner.kill("SIGKILL");
      await new Promise((resolvePromise) => owner.once("close", resolvePromise));
    }
    await rm(root, { recursive: true, force: true });
  }
});
