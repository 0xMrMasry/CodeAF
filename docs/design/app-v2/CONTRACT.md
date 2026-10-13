# codeaf app contract publication

## Regenerate and publish the binary's contract

Run `go generate ./internal/appapi` in the binary checkout. Commit both
`docs/design/app-v2/schema/*.json` and `docs/design/app-v2/wire.ts` when their
registered Go types, route shapes or event payloads change. The generator has
no npm dependencies; it reads `ContractTypes`, `ContractRoutes` and
`ContractEvents`, so resource lanes keep their own registration as the one
source of truth. `make test-quick` checks route coverage, schema round-trips and
freshness of both published formats. It fails until generation is current.

## Import the contract into the app

Pin the copy to the binary commit bundled by the app. From the app checkout:

```sh
git -C <binary-checkout> show <binary-commit>:docs/design/app-v2/wire.ts > src/lib/engine/wire.ts
```

The published file is self-contained. Import its shapes with `import type`;
`APP_CONTRACT` is the only runtime export. `/app/v1` is Contract 1, independent
of the factory's `/v1` Contract 2. The launch hello and health expose the same
app contract number. A breaking wire change must change the binary's `Contract`
constant and regenerate the artifacts; adding a reserved shape alone does not
claim that its resource is implemented. API.md's status column records that.

```ts
import type { AppRequest, AppResponse, AppEvent } from "./wire";

type NewAgent = AppRequest<"POST /app/v1/agents">;
type Snapshot = AppResponse<"GET /app/v1/agents/{id}">;

function text(event: AppEvent): string | undefined {
  if (event.event === "turn.delta") return event.data.text;
}
```

## JSON and stream semantics

Names and fields use the binary's JSON spelling. Money is `{usd: string}`,
ids are strings and UTC timestamps remain strings. `omitempty` fields are
optional; false or zero pointers remain valid explicit updates. Required Go
slices and maps may encode `null`, so consumers must handle that alongside
arrays and objects. Embedded resource fields are flattened. Raw JSON and
extensible settings use `JsonValue`; field validation remains in the engine.
Enum tags become literal unions. Size, range and clock rules are enforced by
the engine and schemas, not by TypeScript types.

`AppRoutes` keys are method plus canonical path template, without query values.
`request: never` means no JSON body. Responses describe successful JSON;
failures use `AppError`, including guarded `NotBuiltResponse` reservations.
SSE modes are not JSON responses: main `/events`, process output `?follow=1`
and AI `stream:true` use `AppEvents` and the discriminated `AppEvent` union.
The event types describe parsed `event`/`data`, not raw SSE framing; ids and
reconnect cursors still belong to the stream transport. Request headers,
query validation and runtime decoding stay with the app client and engine.

## Run strict app compiler acceptance

Go builders require no Node installation. The app can supply its existing
TypeScript compiler to run the checked-in client fixture, which includes
valid requests and deliberately rejected money, enums, routes and payloads:

```sh
CODEAF_TYPESCRIPT_COMPILER=<app-checkout>/node_modules/typescript/bin/tsc \
  go test ./internal/appapi/schema -run TestPublishedTypescriptCompilesForTheApp -count=1 -v
```

Without that variable the test uses `tsc` on PATH, or skips compiler acceptance
when it is absent. Freshness and Go/schema contract laws never depend on that
optional compiler. The fixture enables strict checking and exact optional
property types, and emits no files.
