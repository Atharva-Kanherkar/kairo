
## Reproduction

A reproduction kit is in /work/kairo (see /work/kairo/README.md). Run:

    bash /work/kairo/reproduce.sh

It starts the real entry point against a local deterministic upstream, sends the
reported request, and prints what the client received and what was forwarded.
It does not tell you whether the output is correct; that is your call.
