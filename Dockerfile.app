FROM rust:1.89-slim@sha256:8cffb8fe4e8a95cf0d6a2060375e5a28aff4c752155aa9f1f9193530769bdf66

ENV CARGO_TARGET_DIR=/tmp/medicine-target \
    HOME=/tmp \
    RUSTUP_TOOLCHAIN=1.89.0

COPY rust/medicine_core/Cargo.toml rust/medicine_core/Cargo.lock /opt/medicine-rust/
RUN mkdir -p /opt/medicine-rust/src/bin \
    && : > /opt/medicine-rust/src/lib.rs \
    && : > /opt/medicine-rust/src/bin/medicine_agentctl.rs \
    && : > /opt/medicine-rust/src/bin/medicine_core_web.rs \
    && cargo fetch --locked --manifest-path /opt/medicine-rust/Cargo.toml

WORKDIR /app

ENTRYPOINT ["sh", "/app/scripts/run_app.sh"]
