Environment: Go 1.27 with a warm build cache; the module cache is offline.
Rebuild the gateway with `gomodel-build` (writes /work/bin/gomodel). GoModel
reads config.yaml from its working directory. Unit tests:
`go test ./internal/anthropicapi/`.
