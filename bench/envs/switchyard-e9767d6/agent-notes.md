Environment: Rust 1.96.1, offline cargo with all dependencies vendored in the
registry cache and a warm dev-profile target dir. Rebuild the server with
`switchyard-build` (prints the binary path). Run it with
`switchyard-server --config <file.toml> --port 8080`. Tests:
`cargo test --locked -p switchyard-translation` (or -p switchyard-llm-client).
