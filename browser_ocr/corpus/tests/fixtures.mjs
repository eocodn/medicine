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