# OCR model evaluation corpus

The checked-in corpus is synthetic and contains no patient or prescription data. It exists to make model changes reproducible and reviewable.

Real photographed prescriptions may be evaluated with the same runner by passing a separate corpus manifest outside the repository. Do not add patient-identifiable images to this directory.

This corpus scores only the vision boundary: image -> recognized text, score, and polygon. Prescription semantics are evaluated separately.
