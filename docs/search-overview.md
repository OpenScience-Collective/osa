# The search overview

A community that routes its turns between a quick and an analysis model
(`quick_model` and `analysis_model` in its `config.yaml`) also answers
`POST /{community_id}/overview`: a short, cited reading of the top results of a
dataset search, written by the quick model.
It exists for the Discover panel of nemar.org (the "AI overview" of board D2),
which already holds the search results and does not need a conversation.

This page is for the people who call the endpoint.
The implementation notes (cache internals, budget wiring) are in
`.context/api-structure.md`, section 5.

## Calling it

Send the reader's query, the filters they applied, and up to 10 results in the
search's ranking order.

```js
const response = await fetch('https://edge.osc.earth/osa/nemar/overview', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    query: 'face recognition',
    filters: { modality: ['eeg'] },
    total_count: 240,
    results: results.slice(0, 10).map((r) => ({
      id: r.dataset_id,
      title: r.name,
      description: r.description.slice(0, 500),
      modalities: r.modalities,
    })),
  }),
});
```

A browser page must be on one of the community's allowed origins
(`cors_origins`); a caller outside a browser sends its own key in the
`X-Anthropic-API-Key` or `X-OpenRouter-Key` header.
The Cloudflare worker rate-limits the route and does not ask for a Turnstile
token.
Its limits are per IP address: the per-minute limit it shares with the chat
routes, and an hourly limit of its own (100 in production), so searching in
Discover does not use up the 20 chat questions an hour.
An `Origin` header is enforced by browsers only, so a caller outside a browser
can send any one: the per-IP limits are the only enforced bound on the spend, not
the origin list.
The community's daily and monthly budget is monitored and opens an alert issue
when crossed; it does not block requests.

| Field | Limits |
|---|---|
| `query` | 1 to 300 characters, not blank |
| `filters` | at most 20 keys; values are strings, booleans, integers or lists of strings |
| `results` | 1 to 10 rows; `id` unique, up to 64 characters of letters, digits, `.`, `_`, `-`; `title` up to 200 characters; `description` up to 500; `modalities` an optional string |
| `total_count` | optional, the number of matches in all |

## What comes back

```json
{
  "summary": [
    {"text": "For event-related potentials to faces, "},
    {"dataset_id": "on002718"},
    {"text": " is the closest match: 18 participants viewed famous and scrambled faces."}
  ],
  "picks": [{"id": "on002718", "reason": "Face conditions, HED events."}],
  "cited_ids": ["on002718"],
  "shown": 10,
  "cached": false,
  "model": "claude-haiku-5-5",
  "generated_at": "2026-11-11T08:30:00.123456Z"
}
```

- `summary` is a list of segments: `{"text": ...}` is plain text to show as
  text, and `{"dataset_id": ...}` is a result to link, so the page never parses
  markdown or HTML.
  It is about 100 words; sentences after the first are dropped once it would pass
  120, and a first sentence that is long on its own is kept.
- `picks` are at most three of the given results, best first, each with a short
  reason in plain text.
- `cited_ids` are the ids the summary and the picks name, in order of first
  mention; every one is an `id` you sent.
- `shown` is how many results it was written from, and `cached` says whether
  this request did not call the model.

Every id in the response is one of the ids in the request: a sentence that names
any other id is dropped before the response is built, whatever the text of a
result asks the model to say.
A sentence that holds a link is dropped too: a scheme-prefixed one (`http://`,
`https://`), a `www.` host, a `javascript:`, `data:`, `vbscript:` or `mailto:`
scheme, or a bare host on a common suffix (`.com`, `.org`, `.net`, `.io`, `.edu`,
`.gov`, `.dev`, `.app`, `.info`, `.xyz`).
This is a filter, not a guarantee: a host on another suffix passes, so the page
must render text segments as plain text and never autolink them.
An id is checked inside `[[...]]` markers for every community; a bare id with no
brackets is also checked, but only in the shape NEMAR ids have (two letters and
six digits).
A reply whose summary names no result at all (a refusal, a reply that is not the
JSON asked for, text with no citation) is not an overview: it is a 502 and is not
cached.

| Status | Meaning |
|---|---|
| 403 | The origin is not allowed and no key was sent |
| 404 | The community has no quick model |
| 422 | A field is out of bounds (the body names which) |
| 500 | A bug or a missing configuration on the server, not the model provider; the body has an error id to give the maintainers |
| 502 | The model wrote no overview that could be shown, or the provider failed; try again later. Nothing is cached |

## Caching and cost

The overview is kept in memory for 24 hours, per community, up to 500 entries.
Two requests share an entry when their query (ignoring case, spacing and
Unicode form), their filters and their results are the same.
A different result list, in different content or order, is a new overview, so a
reader is only ever shown an overview written from the results the caller sent.
Identical requests that arrive together share one model call.
The cache is not shared between servers and is lost on a restart.

A model call runs the community's quick model at low reasoning effort and is
counted in the community's budget like any other request (`request_log`, tier
`quick`, endpoint `/<community>/overview`); a cache hit costs nothing.

## Deploying the route

The browser reaches OSA through the Cloudflare worker in `workers/osa-worker`,
which is deployed by hand (`wrangler deploy`, see its `README.md`).
The `/overview` route exists in the worker's code from the release that adds it
and answers only after a maintainer has deployed the worker.
