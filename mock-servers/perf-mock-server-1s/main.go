// perf-mock-server-1s: 1-second latency MCP server for gateway overhead measurement.
//
// 10 tools, each sleeps 1 second before returning a static response.
// proves that gateway overhead is constant (~5ms), not proportional to backend latency.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var addr = flag.String("addr", ":8080", "HTTP listen address")

const responseDelay = 1 * time.Second

type toolArgs struct {
	Input string `json:"input"`
}

func makeTool(name string) func(context.Context, *mcp.ServerSession, *mcp.CallToolParamsFor[toolArgs]) (*mcp.CallToolResultFor[struct{}], error) {
	return func(_ context.Context, _ *mcp.ServerSession, params *mcp.CallToolParamsFor[toolArgs]) (*mcp.CallToolResultFor[struct{}], error) {
		time.Sleep(responseDelay)
		input := params.Arguments.Input
		if input == "" {
			input = "default"
		}
		return &mcp.CallToolResultFor[struct{}]{
			Content: []mcp.Content{
				&mcp.TextContent{Text: fmt.Sprintf("%s: ok [input=%s, delay=1s]", name, input)},
			},
		}, nil
	}
}

var toolNames = []string{
	"alpha",
	"bravo",
	"charlie",
	"delta",
	"echo",
	"foxtrot",
	"golf",
	"hotel",
	"india",
	"juliet",
}

func main() {
	flag.Parse()

	server := mcp.NewServer(&mcp.Implementation{
		Name:    "perf-mock-server-1s",
		Version: "1.0.0",
	}, nil)

	for _, name := range toolNames {
		mcp.AddTool(server, &mcp.Tool{
			Name:        name,
			Description: fmt.Sprintf("mock tool %s (1s delay)", name),
		}, makeTool(name))
	}

	handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server {
		return server
	}, nil)

	port := *addr
	if envPort := os.Getenv("PORT"); envPort != "" {
		port = ":" + envPort
	}

	log.Printf("perf-mock-server-1s listening on %s (%d tools, 1s delay per call)", port, len(toolNames))
	srv := &http.Server{
		Addr:              port,
		Handler:           handler,
		ReadHeaderTimeout: 3 * time.Second,
	}
	if err := srv.ListenAndServe(); err != nil {
		log.Fatalf("server error: %v", err)
	}
}
