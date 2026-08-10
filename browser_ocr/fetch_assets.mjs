import { createHash } from "node:crypto";
import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

const outputDirectory = process.argv[2];
if (!outputDirectory) throw new Error("usage: node fetch_assets.mjs OUTPUT_DIRECTORY");

const assets = [
  {
    name: "PP-OCRv5_mobile_det_onnx_infer.tar",
    url: "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_det_onnx_infer.tar",
    sha256: "781056046c9ed77a15c94681605db6a0f62317c2e9cce6931c71da2478d4bc30",
  },
  {
    name: "korean_PP-OCRv5_mobile_rec_onnx_infer.tar",
    url: "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/korean_PP-OCRv5_mobile_rec_onnx_infer.tar",
    sha256: "568ed8b43a260adc9f484d92105e425ea8cddf8ce16940c177bc12864cfb0eb0",
  },
];

await mkdir(outputDirectory, { recursive: true });

for (const asset of assets) {
  const response = await fetch(asset.url);
  if (!response.ok || !response.body) throw new Error(`${asset.name}: HTTP ${response.status}`);
  const total = Number(response.headers.get("content-length")) || null;
  const chunks = [];
  let received = 0;
  let reported = -1;
  for await (const chunk of response.body) {
    chunks.push(chunk);
    received += chunk.byteLength;
    const percent = total ? Math.floor((received / total) * 10) * 10 : null;
    if (percent !== null && percent !== reported) {
      reported = percent;
      process.stderr.write(`${asset.name}: ${Math.min(percent, 100)}%\n`);
    }
  }
  if (total !== null && received !== total) throw new Error(`${asset.name}: truncated download`);
  const bytes = Buffer.concat(chunks);
  const digest = createHash("sha256").update(bytes).digest("hex");
  if (digest !== asset.sha256) throw new Error(`${asset.name}: sha256 mismatch`);
  const target = join(outputDirectory, asset.name);
  const temporary = `${target}.partial`;
  await rm(temporary, { force: true });
  await writeFile(temporary, bytes, { flag: "wx" });
  await rename(temporary, target);
  process.stderr.write(`${asset.name}: verified ${received} bytes\n`);
}
