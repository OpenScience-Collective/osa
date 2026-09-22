"""Numeric caps on what a browser execution may send back.

These live in their own module, importing nothing but the standard library, because two
very different places need them and one of them must stay dependency-light.

`src.api.tool_results` enforces them on the way in. `src.core.config.community` bounds
`RuntimeLimits` by them, so a community cannot declare a limit the server is guaranteed
to reject: without that, a config could promise the browser 64 KB of stdout, the browser
would honor its own config, and every result it sent would be refused whole with a 422
by a cap it was never told about. The failure would look like the browser misbehaving
when it was the config lying.

The first of those modules imports langchain-core. The second must import cleanly
WITHOUT the `server` extra, which `tests/test_cli/test_validate.py` pins, because the
CLI validates community configs on machines that never install a model runtime. Having
the config module reach into the API module for these four numbers broke that contract;
a shared leaf module is what lets both sides read one definition.
"""

#: Longest captured stdout, in characters, per execution.
MAX_STDOUT_CHARS = 16_384

#: Longest captured stderr, in characters. Shorter than stdout because the useful part
#: of a traceback is its tail, which is preserved.
MAX_STDERR_CHARS = 8_192

#: Most images one execution may return.
MAX_IMAGES = 3

#: Largest single image, in decoded bytes. Applied to the decoded form rather than the
#: base64 string, which inflates by a third and would otherwise admit payloads a quarter
#: larger than intended.
MAX_IMAGE_BYTES = 2_000_000

#: Longest edge, in pixels, the server will accept on a returned image.
MAX_IMAGE_EDGE_PX = 8192

#: Longest deterministic summary, in characters. This is the part the model reasons over.
MAX_SUMMARY_CHARS = 8_192

#: Cap on the rendered TEXT of a persisted tool result, separate from the per-message
#: cap that applies to something a person typed. Machine output is not a message, and
#: the smaller cap forbids every realistic figure (issue #422).
MAX_TOOL_RESULT_LENGTH = 65_536
