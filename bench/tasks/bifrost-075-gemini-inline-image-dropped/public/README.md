# Reproduction kit

`reproduce.sh` rebuilds the Bifrost HTTP gateway from `/work/repo` and starts
it with the native `gemini` provider pointed at a deterministic local Gemini
API (`upstream.py`) that answers `generateContent` and
`streamGenerateContent?alt=sse` with a caption part plus an `inlineData` PNG
part. It sends the reported chat completion (unary and streaming) and prints
what the client received. Gateway log: `/tmp/bifrost.log`.
