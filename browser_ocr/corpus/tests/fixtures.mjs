import { writeFile } from "node:fs/promises";

import { buildHistoricalDrugExposure } from "../drug_holdout.mjs";

export function testDrugCatalog(count = 240) {
  return Array.from({ length: count }, (_, index) => ({
    item_seq: String(800000000 + index),
    product_name: `테스트의약품${String(index).padStart(3, "0")}정`,
  }));
}

export async function createCanonicalDrugDb(path, count = 240) {
  const { DatabaseSync } = await import("node:sqlite");
  const database = new DatabaseSync(path);
  try {
    database.exec(`
      create table source_snapshots (
        dataset_key text primary key,
        source_family text not null,
        source_locator text not null,
        sha256 text not null
      );
      create table products (
        item_seq text primary key,
        product_name text,
        source_dataset_key text not null,
        permit_status text
      );
    `);
    database.prepare(
      "insert into source_snapshots(dataset_key, source_family, source_locator, sha256) values (?, ?, ?, ?)",
    ).run(
      "mfds_permit:products",
      "mfds_permit",
      "fixture://mfds-products",
      "a".repeat(64),
    );
    const insert = database.prepare(
      "insert into products(item_seq, product_name, source_dataset_key, permit_status) values (?, ?, ?, ?)",
    );
    for (const product of testDrugCatalog(count)) {
      insert.run(product.item_seq, product.product_name, "mfds_permit:products", "active");
    }
  } finally {
    database.close();
  }
}
export function testHistoricalDrugExposure(count = 240, exposedCount = Math.floor(count * 0.7)) {
  const products = testDrugCatalog(count);
  return buildHistoricalDrugExposure({
    productNames: products.slice(0, exposedCount).map((product) => product.product_name),
    checkpointSha256: "b".repeat(64),
    sourceDatasetId: "fixture-selected-recognizer-train",
    sourceDatasetFingerprint: "c".repeat(64),
    sourceTrainSplitSha256: "d".repeat(64),
    sourceTrainSampleCount: 76520,
  });
}

export async function writeTestHistoricalDrugExposure(path, count = 240) {
  const exposure = testHistoricalDrugExposure(count);
  await writeFile(path, `${JSON.stringify(exposure, null, 2)}\n`, "utf8");
  return exposure;
}
