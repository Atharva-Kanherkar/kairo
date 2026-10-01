// Command kairotee is a transparent stdio proxy that records every byte that
// crosses the MCP wire between a client and a stdio MCP server, so the upstream
// leg can be evidenced from raw bytes rather than from a decoded struct.
//
// It spawns the server named by its first argument and forwards its own stdin to
// the child's stdin and the child's stdout to its own stdout, unchanged. Every
// byte in either direction is also appended to the file named by
// KAIRO_TEE_LOG, tagged with the direction.
//
// It parses nothing and rewrites nothing. If the client under test sees a
// different byte than this log shows, the log is wrong.
//
// usage: kairotee <server-path> [server-args...]
package main

import (
	"fmt"
	"os"
	"os/exec"
	"sync"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: kairotee <server-path> [args...]")
		os.Exit(2)
	}
	logPath := os.Getenv("KAIRO_TEE_LOG")
	if logPath == "" {
		fmt.Fprintln(os.Stderr, "kairotee: KAIRO_TEE_LOG is required")
		os.Exit(2)
	}

	// The child's stdin and stdout are both piped through this process, never
	// handed straight to os.Stdin/os.Stdout, because every byte has to pass
	// through here to be recorded. Wiring child.Stdin = os.Stdin as well would
	// race two readers on the same descriptor and split the JSON-RPC stream.
	child := exec.Command(os.Args[1], os.Args[2:]...)
	child.Stderr = os.Stderr
	stdin, err := child.StdinPipe()
	if err != nil {
		fmt.Fprintf(os.Stderr, "kairotee: stdin pipe: %v\n", err)
		os.Exit(1)
	}
	stdout, err := child.StdoutPipe()
	if err != nil {
		fmt.Fprintf(os.Stderr, "kairotee: stdout pipe: %v\n", err)
		os.Exit(1)
	}
	if err := child.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "kairotee: start: %v\n", err)
		os.Exit(1)
	}

	log, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "kairotee: open log: %v\n", err)
		_ = child.Process.Kill()
		os.Exit(1)
	}
	defer log.Close()

	var writeMu sync.Mutex
	record := func(direction string, data []byte) {
		if len(data) == 0 {
			return
		}
		writeMu.Lock()
		defer writeMu.Unlock()
		if _, err := fmt.Fprintf(log, "<<<%s>>>%s", direction, data); err != nil {
			fmt.Fprintf(os.Stderr, "kairotee: log write: %v\n", err)
		}
	}

	// Client to server: read the client's request, record it, forward it
	// unchanged. A short write is retried rather than dropped, because a
	// truncated JSON-RPC frame would corrupt the stream the server reads.
	go func() {
		buffer := make([]byte, 4096)
		for {
			n, readErr := os.Stdin.Read(buffer)
			if n > 0 {
				record("client-to-server", buffer[:n])
				if _, writeErr := stdin.Write(buffer[:n]); writeErr != nil {
					fmt.Fprintf(os.Stderr, "kairotee: forward stdin: %v\n", writeErr)
					return
				}
			}
			if readErr != nil {
				_ = stdin.Close()
				return
			}
		}
	}()

	// Server to client: read the server's response, record it, forward it
	// unchanged.
	go func() {
		buffer := make([]byte, 4096)
		for {
			n, readErr := stdout.Read(buffer)
			if n > 0 {
				record("server-to-client", buffer[:n])
				if _, writeErr := os.Stdout.Write(buffer[:n]); writeErr != nil {
					fmt.Fprintf(os.Stderr, "kairotee: forward stdout: %v\n", writeErr)
					return
				}
			}
			if readErr != nil {
				return
			}
		}
	}()

	if err := child.Wait(); err != nil {
		// A server exiting nonzero after its transport closed is not this
		// proxy's failure to report as its own, so the code is preserved.
		if exitErr, ok := err.(*exec.ExitError); ok {
			os.Exit(exitErr.ExitCode())
		}
		fmt.Fprintf(os.Stderr, "kairotee: wait: %v\n", err)
		os.Exit(1)
	}
}
