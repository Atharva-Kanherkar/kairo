package gateway_test

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	envoyroutev3 "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	"github.com/stretchr/testify/require"
	"k8s.io/apimachinery/pkg/types"

	apisettings "github.com/kgateway-dev/kgateway/v2/api/settings"
	translatortest "github.com/kgateway-dev/kgateway/v2/test/translator"
)

// TestKairo147DelegationChildMatchStaysInsideParent is the invariant checker for
// kairo issue 147. A delegated child HTTPRoute may narrow its parent's matcher,
// never escape it. The bug input must fail the invariant; the control, with the
// inherit-parent-matcher annotation removed, must pass.
func TestKairo147DelegationChildMatchStaysInsideParent(t *testing.T) {
	dir := os.Getenv("KAIRO_REPRO_DIR")
	require.NotEmpty(t, dir, "set KAIRO_REPRO_DIR to the transcript directory")

	for _, tc := range []struct {
		name  string
		input string
	}{
		{name: "bug-inherit-regex-escape", input: "input.yaml"},
		{name: "control-no-inherit-annotation", input: "control.yaml"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()

			scheme := translatortest.NewScheme(nil)
			files := []string{
				filepath.Join(dir, "common.yaml"),
				filepath.Join(dir, tc.input),
			}
			results, err := (translatortest.TestCase{InputFiles: files}).Run(
				t, ctx, scheme, translatortest.ExtraConfig{},
				func(s *apisettings.Settings) {
					s.EnableExperimentalGatewayAPIFeatures = true
					s.EnableAuthMetadata = true
				},
			)
			require.NoError(t, err)
			res, ok := results[types.NamespacedName{Namespace: "infra", Name: "example-gateway"}]
			require.True(t, ok)

			for _, rc := range res.Proxy.Routes {
				for _, vh := range rc.GetVirtualHosts() {
					for _, r := range vh.GetRoutes() {
						t.Logf("programmed route %q match=%v", r.GetName(), r.GetMatch())
					}
				}
			}
			if violations := childMatchViolations(res.Proxy.Routes); len(violations) > 0 {
				t.Fatalf("delegated child escaped parent prefix /team1: %v", violations)
			}
		})
	}
}

func childMatchViolations(routes []*envoyroutev3.RouteConfiguration) []string {
	const parentPrefix = "/team1"
	canaries := []string{"/admin", "/admin/", "/admin/secret"}
	var out []string

	for _, rc := range routes {
		for _, vh := range rc.GetVirtualHosts() {
			for _, r := range vh.GetRoutes() {
				if !strings.Contains(r.GetName(), "httproute-zz-child") {
					continue
				}
				m := r.GetMatch()
				switch {
				case m.GetSafeRegex() != nil:
					re := m.GetSafeRegex().GetRegex()
					compiled, err := regexp.Compile("^(?:" + re + ")$")
					if err != nil {
						out = append(out, fmt.Sprintf("child %s has invalid regex %q: %v", r.GetName(), re, err))
						continue
					}
					for _, path := range canaries {
						if compiled.MatchString(path) {
							out = append(out, fmt.Sprintf(
								"child %s regex %q matches %q outside parent prefix %q",
								r.GetName(), re, path, parentPrefix))
						}
					}
				case m.GetPathSeparatedPrefix() != "":
					p := m.GetPathSeparatedPrefix()
					if p != parentPrefix && !strings.HasPrefix(p, parentPrefix+"/") {
						out = append(out, fmt.Sprintf(
							"child %s path-separated prefix %q is outside parent prefix %q",
							r.GetName(), p, parentPrefix))
					}
				case m.GetPath() != "":
					p := m.GetPath()
					if p != parentPrefix && !strings.HasPrefix(p, parentPrefix+"/") {
						out = append(out, fmt.Sprintf(
							"child %s exact path %q is outside parent prefix %q",
							r.GetName(), p, parentPrefix))
					}
				}
			}
		}
	}
	return out
}
