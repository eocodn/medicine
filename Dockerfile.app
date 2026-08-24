FROM rust:1.89-slim@sha256:8cffb8fe4e8a95cf0d6a2060375e5a28aff4c752155aa9f1f9193530769bdf66 AS rust-cli

WORKDIR /build
COPY rust/medicine_core/Cargo.toml rust/medicine_core/Cargo.lock ./
COPY rust/medicine_core/src ./src
RUN cargo build --locked --release --bin medicine-core

FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ENV HOME=/tmp
WORKDIR /app
COPY medicine_app/__init__.py medicine_app/cli.py ./medicine_app/
COPY --from=rust-cli /build/target/release/medicine-core /usr/local/bin/medicine-core

ENTRYPOINT ["python", "-m", "medicine_app.cli"]
