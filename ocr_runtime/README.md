# OCR runtime

This directory contains only the reproducible build inputs for the on-device OCR runtime shipped by the Android app. The packaged runtime itself lives at `android/app/src/main/assets/ocr-assets`.

The runtime performs text detection and Korean text recognition locally in the Android WebView. Medication identity is not inferred by a learned document parser: recognized OCR boxes are passed to the canonical catalog candidate search, and users select the product and enter regimen information themselves.

Rebuild the runtime with the repository-root Docker context and compare the resulting `/ocr-assets` tree with the packaged Android assets before changing the shipped runtime.
