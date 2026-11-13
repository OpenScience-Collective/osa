# The search overview

A community that routes its turns between a quick and an analysis model
(`quick_model` and `analysis_model` in its `config.yaml`) also answers
`POST /{community_id}/overview`: a short, cited reading of the top results of a
dataset search, written by the quick model.
It reads the way a colleague would talk the results through:
a lead-in on what the search seems to focus on,
a list of the results that look most promising and how each relates to it,
and a suggestion of where to start (#748).
It exists for the Discover panel of nemar.org (the "AI overview" of board D2),
which already holds the search results and does not need a conversation.

This page is for the people who call the endpoint.
The implementation notes (cache internals, budget wiring) are in
`.context/api-structure.md`, section 5.

## Calling it

Send the reader's query, the filters they applied, and up to 20 results in the
search's ranking order.

```js
const response = await fetch('https://widget.osc.earth/osa/nemar/overview', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    query: 'face recognition in EEG',
    filters: { modality: ['eeg'] },
    total_count: 240,
    results: results.slice(0, 20).map((r) => ({
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
| `query` | 1 to 300 characters, and at least 3 words: whitespace-separated tokens that hold a letter or digit, so `P300` and `α-band` are words and a lone `-` is not |
| `filters` | at most 20 keys; values are strings, booleans, integers or lists of strings |
| `results` | 1 to 20 rows; `id` unique, up to 64 characters of letters, digits, `.`, `_`, `-`; `title` up to 200 characters; `description` up to 500; `modalities` an optional string |
| `total_count` | optional, the number of matches in all |

An overview needs a search of at least three words;
a shorter search is refused with a 422 before any model call.
(A one-word search, "EEG", had come back as a 502; #728.)
nemar.org's Discover panel counts words the same way and shows a note instead of calling.

## What comes back

```json
{
  "summary": [
    {"text": "Your search seems to focus on how the brain responds to faces. I had a look at the top 20 matches, and these two look especially promising."}
  ],
  "picks": [
    {"id": "on002718", "reason": "18 participants viewed famous and scrambled faces, with HED events."},
    {"id": "on003645", "reason": "Faces against houses in MEG, if you can work with another modality."}
  ],
  "suggestion": [
    {"text": "I'd start with "},
    {"dataset_id": "on002718"},
    {"text": ", since it is the only one built around famous faces."}
  ],
  "cited_ids": ["on002718", "on003645"],
  "shown": 20,
  "cached": false,
  "model": "claude-haiku-5-5",
  "generated_at": "2026-11-11T08:30:00.123456Z"
}
```

- `summary` is the lead-in: what the search seems to focus on and how many
  results look promising.
  It is a list of segments: `{"text": ...}` is plain text to show as text, and
  `{"dataset_id": ...}` is a result to link, so the page never parses markdown or
  HTML.
  It is asked for in about 45 words; sentences after the first are dropped once it
  would pass 60, and a first sentence that is long on its own is kept.
  It always has text and may name no result.
- `picks` are at most ten of the given results, best first, each with a short
  reason in plain text saying how it relates to the search.
  Show them right after the lead-in.
- `suggestion` is where to start: one or two sentences, segments like the summary,
  held to the same 60-word rule, to show after the picks.
  It is an empty list when the model wrote none that survives the filter.
- `cited_ids` are the ids the summary, the picks and then the suggestion name, in
  order of first mention; every one is an `id` you sent.
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
A reply whose summary has no words, or that names no result anywhere (in the
summary, the picks or the suggestion: a refusal, a reply that is not the JSON
asked for, text with no citation) is not an overview: it is a 502 and is not
cached.

A caller written for the earlier contract (up to 10 results, up to 3 picks, no
`suggestion`) keeps working: it reads the first picks and ignores the new field.

| Status | Meaning |
|---|---|
| 400 | The body is not JSON (the worker in front of the server answers this one) |
| 403 | The origin is not allowed and no key was sent |
| 404 | The community has no quick model |
| 422 | A field is out of bounds (the body names which), including a `query` under 3 words ("an overview needs a search of at least 3 words") |
| 429 | Too many requests from one address (the worker answers this one): more than 10 a minute, or more than 100 overviews an hour in production (500 in development); the body's `details` says which |
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
