import { cp, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const output = fileURLToPath(new URL("../public/ocr/", import.meta.url));
await mkdir(output, { recursive: true });
const packageRoot = (name) => dirname(require.resolve(`${name}/package.json`));
await cp(
  join(packageRoot("tesseract.js"), "dist/worker.min.js"),
  join(output, "worker.min.js"),
);
await cp(
  join(packageRoot("tesseract.js"), "LICENSE.md"),
  join(output, "tesseract-LICENSE.md"),
);
for (const suffix of ["lstm", "simd-lstm", "relaxedsimd-lstm"])
  await cp(
    join(packageRoot("tesseract.js-core"), `tesseract-core-${suffix}.wasm.js`),
    join(output, `tesseract-core-${suffix}.wasm.js`),
  );
await cp(
  join(packageRoot("tesseract.js-core"), "LICENSE"),
  join(output, "core-LICENSE"),
);
for (const lang of ["kor", "eng"])
  await cp(
    join(
      packageRoot(`@tesseract.js-data/${lang}`),
      "4.0.0_best_int",
      `${lang}.traineddata.gz`,
    ),
    join(output, `${lang}.traineddata.gz`),
  );
console.log(
  "Self-hosted OCR worker, core and Korean/English language assets prepared.",
);
