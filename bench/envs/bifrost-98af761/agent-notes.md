Environment: Go 1.27 with a warm build cache and a Go workspace (go.work) over
core, framework, transports, and plugins, so edits anywhere in /work/repo are
picked up. Rebuild the gateway with `bifrost-build` (writes
/work/bin/bifrost-http, about 15 seconds). Run it with
`/work/bin/bifrost-http -app-dir <dir> -port 8080` where <dir>/config.json holds
the config; pricing data must come from file:// URLs because there is no
network. Unit tests: `cd core && go test ./providers/<name>/`.
