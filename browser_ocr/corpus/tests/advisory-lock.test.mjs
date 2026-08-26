import assert from "node:assert/strict";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { acquireAdvisoryLock, releaseAdvisoryLock } from "../advisory_lock.mjs";

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
