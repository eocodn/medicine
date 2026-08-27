FROM rust:1.89-slim@sha256:8cffb8fe4e8a95cf0d6a2060375e5a28aff4c752155aa9f1f9193530769bdf66 AS rust-cli

WORKDIR /build
COPY rust/medicine_core/Cargo.toml rust/medicine_core/Cargo.lock ./
COPY rust/medicine_core/src ./src
RUN cargo build --locked --release --features agentctl --bin medicine-agentctl

FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

ENV HOME=/tmp
WORKDIR /app
COPY --from=rust-cli /build/target/release/medicine-agentctl /usr/local/bin/medicine-agentctl

ENTRYPOINT ["/usr/local/bin/medicine-agentctl"]
