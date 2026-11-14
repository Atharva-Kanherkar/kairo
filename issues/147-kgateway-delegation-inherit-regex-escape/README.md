# 147, a delegated child HTTPRoute with inherit-parent-matcher escapes the parent prefix and shadows a protected route

## Claim

A delegated child HTTPRoute may narrow its parent's matcher, never escape it.
kgateway's `delegation.kgateway.dev/inherit-parent-matcher` path merges the
parent and child paths with `path.Join` and never checks that the result stays
inside the parent. A child `RegularExpression` of `../admin(?:/.*)?` under a
parent `PathPrefix /team1` becomes `/admin(?:/.*)?`. That route is emitted
before a separate platform route for `/admin`, so it captures the admin
subtree. The child inherits the delegating parent's policies, not the admin
route's, so a route-level RBAC policy on the admin route is bypassed.

- **Upstream project**: [kgateway-dev/kgateway](https://github.com/kgateway-dev/kgateway).
- **Cited upstream issue**: [#14869](https://github.com/kgateway-dev/kgateway/issues/14869), open.
  Classification `duplicate-open` as of 2026-11-14. The issue's Additional
  Context already names the inherit branch and `path.Join`. The open fix,
  [#14949](https://github.com/kgateway-dev/kgateway/pull/14949), changes only
  the non-inherit path check; it adds helper functions and tests but does not
  touch the inherit branch, and applying it leaves this reproduction unchanged.
- **Target type**: open source, run locally.
- **Tested commit**: `74f2e098d85f1b3d6825b3a12bbacab82e606cb3` (main, 2026-11-13).
- **Client dialect**: Gateway API HTTPRoute.
- **Backend dialect**: kgateway's HTTPRoute to Envoy xDS translation. Data-plane
  check with `envoyproxy/envoy:v1.39.1`, digest
  `sha256:57e14a549d7bd43c8d3f6d03e8cfa653e037d4b38e133acd9b54f38c524401b4`.
- **Model, if relevant**: not applicable.
- **Relevant configuration**: the delegation parent uses `PathPrefix /team1`;
  the child names `delegation.kgateway.dev/inherit-parent-matcher: "true"` and a
  `RegularExpression` path; a separate platform route uses `PathPrefix /admin`
  with a route-level RBAC TrafficPolicy. The invariant test sets
  `EnableAuthMetadata=true` as the standard translator harness does; it does not
  affect delegation or RBAC translation. No other non-default settings.

## What breaks

The parent route grants the tenant child the `/team1` subtree. The child's
`RegularExpression` value `../admin(?:/.*)?` is a valid RE2 expression. The
inherit branch calls `path.Join("/team1", "../admin(?:/.*)?")`, which cleans the
`..` segment to `/admin(?:/.*)?`. The child route is programmed with
`safeRegex: /admin(?:/.*)?`.

The listener sorts a `RegularExpression` match above a `PathSeparatedPrefix`
match, so the child route is first. A request for `/admin`, `/admin/`, or
`/admin/secret` matches the child and reaches the tenant's backend. The
platform's `/admin` route is never selected. Because the child inherits the
delegating parent's policies only, the admin route's route-level RBAC per-filter
config is absent from the child, so a request that would be denied on the admin
route is allowed on the child.

Who it hurts: a platform that uses delegation to grant each team a path prefix
on a shared Gateway, and protects an admin path with a route-level TrafficPolicy.
The tenant that owns a delegated child can leave its prefix, shadow the admin
route, and receive the admin traffic and credentials without the admin policy.

Conditions: a delegated child HTTPRoute in a namespace that owns it, the
`inherit-parent-matcher` annotation, and a regex child path. The parent does not
need to use a wildcard delegation; the tenant can set the annotation and the
regex on a route the parent already delegates to.

Measured versus inferred: the translator invariant is **measured** 5 of 5. The
merged match, the route order, the child's missing RBAC config, and the Envoy
routing are shown in one xDS snapshot and one minimal Envoy run. The exact
credential exposure depends on what the admin route carries and is **inferred**
from the routing and policy attachment.

## Evidence

`transcripts/147-kgateway-delegation-inherit-regex/`:

- `common.yaml`, the shared Gateway and Services used by the translator.
- `input.yaml`, the bug case: parent `/team1`, admin `/admin` with RBAC, child
  with the inherit annotation and `../admin(?:/.*)?`.
- `control.yaml`, the same input with only the child annotation removed.
- `invariant_test.go`, the executable checker. It runs the real translator
  through `translatortest.TestCase.Run`, finds the programmed route for the
  delegated child, and fails if its match can match `/admin`, `/admin/`, or
  `/admin/secret`, all outside `/team1`.
- `run-bug.txt`, the bug input fails 5 of 5.
- `run-control.txt`, the control passes 5 of 5. Removing only the annotation
  makes the child fall out because the non-inherit prefix check rejects the
  `../admin` value.
- `output.yaml`, the xDS snapshot for the bug.
- `control-output.yaml`, the xDS snapshot for the control.
- `envoy.yaml`, `envoy-check.sh`, `envoy-check.txt`, the data-plane check.
- `environment.txt`, the pinned commit, toolchain, and Envoy digest.

### Translation

The two relevant routes in `output.yaml`, in order:

```yaml
- match:
    safeRegex:
      regex: /admin(?:/.*)?
  name: listener~80~example_com-route-0-httproute-zz-child-a-0-0-matcher-0
  route:
    cluster: kube_a_svc-a_8080
- match:
    pathSeparatedPrefix: /admin
  metadata:
    filterMetadata:
      merge.TrafficPolicy.gateway.kgateway.dev:
        rbac:
        - gateway.kgateway.dev/TrafficPolicy/infra/zz-admin-policy
  name: listener~80~example_com-route-2-httproute-zz-admin-infra-0-0-matcher-0
  route:
    cluster: kube_infra_example-svc_80
  typedPerFilterConfig:
    envoy.filters.http.rbac:
      '@type': type.googleapis.com/envoy.extensions.filters.http.rbac.v3.RBACPerRoute
      rbac: ...
```

The child route has no `typedPerFilterConfig`. The admin route has the RBAC
per-filter config. `control-output.yaml` has no child route; the parent's
`/team1` route and the admin's `/admin` route remain. The parent route is a 500
fallback because its only backend is the now-rejected child, which is the
expected result of removing the annotation.

### Data plane

`envoy.yaml` loads the two emitted matches in the same order, with each route
returning its backend name directly. `envoy-check.txt`:

```text
/admin         -> tenant status=200
/admin/        -> tenant status=200
/admin/secret  -> tenant status=200
/team1/x       ->  status=404
```

The child route is selected for the whole admin subtree. The admin route is
never reached.

## Root cause

`pkg/kgateway/utils/delegation/delegation.go:210`:

```go
joined := path.Join(pathMatchValue(parent.Path), *child.Path.Value)
```

The inherit branch in `FilterDelegatedRuleMatches`
(`pkg/kgateway/utils/delegation/delegation.go:166-171`) skips
`IsDelegatedRouteMatch`, and `MergeParentChildRouteMatch` does not constrain the
result. The `RegularExpression` type bypasses the Gateway API path rules that
reject `/../` and `/..` because those CEL rules apply only to `Exact` and
`PathPrefix`. The route sorter puts a regex match ahead of a prefix match, so
the child shadows the admin route.

`pkg/kgateway/translator/httproute/delegation_helpers.go:55,63` reads the
annotation and passes it into the inherit branch.
`pkg/kgateway/translator/irtranslator/route.go:855-869` attaches the child's
own and delegating-parent policies, not the admin route's.

## Test

Run from the kairo checkout, then the pinned kgateway checkout:

```text
# in the kairo checkout
export KAIRO147="$PWD/transcripts/147-kgateway-delegation-inherit-regex"

# in a clean kgateway checkout at 74f2e098d85f1b3d6825b3a12bbacab82e606cb3
cp "$KAIRO147/invariant_test.go" pkg/kgateway/translator/gateway/zz_kairo147_invariant_test.go
KAIRO_REPRO_DIR="$KAIRO147" \
  go test ./pkg/kgateway/translator/gateway/ \
  -run 'TestKairo147DelegationChildMatchStaysInsideParent/bug-inherit-regex-escape' \
  -count=5 -v   # fails 5 of 5
KAIRO_REPRO_DIR="$KAIRO147" \
  go test ./pkg/kgateway/translator/gateway/ \
  -run 'TestKairo147DelegationChildMatchStaysInsideParent/control-no-inherit-annotation' \
  -count=5 -v   # passes 5 of 5
```

The checker is a targeted regression test over the programmed route for
`a/zz-child` and three admin canaries, not a general regex-containment proof.
It asserts the protocol invariant for this input: the delegated child must not
match `/admin`, `/admin/`, or `/admin/secret`, all outside the parent's `/team1`
prefix. The control changes only the annotation, so the failure is attributed
to the inherit branch rather than to the regex input or the harness.

No Rust harness checker is added. The kairo harness models LLM wire transcripts,
not Envoy xDS; the Go test is the executable invariant and runs against the
pinned kgateway source.

## Upstream status

Searched on 2026-11-14: `inherit-parent-matcher`, `MergeParentChildRouteMatch`,
`path.Join`, `delegated RegularExpression`, `route delegation prefix`,
`/admin` delegation, and the exact route names. [#14869](https://github.com/kgateway-dev/kgateway/issues/14869)
is open and its Additional Context already describes this branch and the
`/x|/admin.*` variant. [#14949](https://github.com/kgateway-dev/kgateway/pull/14949)
is open and does not cover the inherit branch. [#13792](https://github.com/kgateway-dev/kgateway/issues/13792)
and closed PR [#14329](https://github.com/kgateway-dev/kgateway/pull/14329) cover
a regex parent under matcher inheritance, which is a different case. This is a
duplicate with independent evidence and a new payload (`../admin(?:/.*)?`), not
a novel finding.

## Why this is a bug, not a hardening note

- EP-10943 states the goal: "Allowing teams to make changes to their routing
  configuration without accessing or impacting routing owned by other teams."
  The child leaves its granted prefix and impacts the admin route.
- The trigger is the documented delegation feature and its documented
  `inherit-parent-matcher` annotation, not a misconfiguration.
- A real boundary is crossed: the platform's admin route traffic reaches the
  tenant's backend and the admin route's route-level RBAC is not applied.
- The fix is a code change, not a doc note: validate the merged matcher against
  the parent prefix with the regex-aware containment logic from #14949, and add
  the `../admin(?:/.*)?` and `/team1/.*|/admin` cases. Rejecting every
  `RegularExpression` child under inheritance would break the legitimate
  relative regex fixture in `relative_paths.yaml`.

## Not verified

- The translator harness does not validate HTTPRoute against the Gateway API
  CRD. The CRD itself allows `RegularExpression` values; its `/../` and `/..`
  rules are conditional on `Exact` and `PathPrefix`. A full API server
  admission, controller, and generated-data-plane Envoy run was not performed.
- The Envoy check uses the two emitted matches in a minimal config, not the full
  generated data plane with the RBAC filter chain. The RBAC bypass is shown in
  the xDS output, where the child route has no RBAC config, and follows from the
  route order; it was not exercised against a live RBAC filter in the full
  generated config.
- The full admin request path and its credentials were not exercised; the
  consequence is inferred from the route selection and policy attachment.
