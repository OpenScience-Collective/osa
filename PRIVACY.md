# Privacy

This page describes what the Open Science Assistant (OSA) does with what you type,
as it runs at osc.earth for the communities that embed it (for example NEMAR, HED, BIDS and EEGLAB).
The Open Science Collective (OSC) operates that deployment.
If you run OSA yourself, your own configuration decides the model provider, logging and tracing.

## In short

- Your questions and the assistant's answers are not written to disk.
  The server holds a conversation in memory for up to 24 hours after its last message so you can continue it,
  and a restart clears it.
- The models run under zero data retention:
  the provider does not keep your prompts or the replies after answering, and does not train on them.
- Usage metrics record how each request was served (model, token counts, cost, timing, tool names),
  never what was asked or answered.
- Your chat history, figures, code and settings stay in your browser.
- No tracing service, analytics script or advertising receives your conversations.

## What is sent when you ask a question

| What | When | Why |
| --- | --- | --- |
| Your message | Always | To answer it. |
| A conversation id | Always | To continue the same conversation. |
| The page's address and title | When "Share page URL to help answer questions" is on (the default) | So the assistant knows where you are; it may also read that public page to answer. |
| The dataset the page shows, and the community's instructions for that page | On pages that set them (for example a NEMAR dataset page) | The question is usually about that dataset. Sent whether or not you share the page's address. |
| The text of earlier turns in this chat | When the server may have lost the conversation (after a reload, or after more than 24 hours) | To restore the conversation. Text only: never code runs, results or figures. |
| The results of code you allow to run in your browser | When the assistant runs code | Printed output, a short summary and up to three figures, so the assistant can continue the analysis. Code you edit and run yourself is never sent. |
| Your memory budget setting | When code can run | So the assistant sizes its reads. |
| Your search words, its filters and the top results | When you turn on AI Mode on a community's search page | To write a short overview of the results. The overview is kept in the server's memory for 24 hours, so the same search is answered at once. |
| Your own application programming interface (API) key | Only if you enter one in Settings | To call the provider with your key. It is stored in this browser. |

## Where it goes

1. **A Cloudflare Worker** (`widget.osc.earth`) receives the request and passes it to the OSA server.
   It uses your Internet Protocol (IP) address to limit how many requests one address can make;
   those counters expire within two hours, and your IP address is not passed on.
   Cloudflare keeps logs of the Worker's requests (request metadata such as the path, status and errors), not the text you send.
2. **The OSA server** (`api.osc.earth`) builds the prompt, calls the model, and runs the community's tools.
3. **The model provider.**
   Claude models run on the Claude Platform on AWS, Anthropic's own API billed through Amazon Web Services (AWS).
   Every community uses Claude by default.
   If you choose GPT-6 Luna, Qwen3 Next or gpt-oss-120b in Settings, that model runs on Amazon Bedrock in US regions.
   Both run under zero data retention.
   A provider may hold part of a prompt in memory for a few minutes (prompt caching) so the next request is faster.
   If you enter your own key, the request goes to Anthropic or OpenRouter under your own agreement with them.
4. **Community tools.**
   When the assistant looks something up, the service it asks receives that lookup;
   for example, NEMAR's dataset server (`mcp.nemar.org`) receives the dataset ids and search terms.
   When code runs in your browser, your browser fetches the recording directly from the community's data host;
   the data does not pass through OSA.

## What is stored

**On the OSA server, in memory.**
The conversation (your messages, the replies, the code the assistant wrote and its printed output)
is held for up to 24 hours after its last message, so a follow-up question has its context.
Figures are not kept.
It is never written to disk, and a restart or a busy server clears it sooner.

**On the OSA server, on disk: usage metrics.**
One row per request:
the time, the community, the endpoint, the model, token counts, the estimated cost,
the names of the tools used, the status, the duration, whether the quick or the analysis model answered,
and the conversation id.
No row holds your question, the reply, the page's address or your IP address.
They are used to track cost, budgets and reliability, and there is no automatic deletion yet.

**On the OSA server, on disk: feedback, only when you send it.**
A thumbs up or down, the comment you write,
the page's address if you share it,
and the ids that tie it to the reply it rates.
Feedback is read by the OSA maintainers and that community's maintainers, so please do not include personal information.

**Server logs.**
They do not contain questions or replies.
They hold errors, request paths and status codes,
and, when the assistant reads the page you shared, that page's address.

## What stays in your browser

- **Chat history:** your last 30 chats, in this browser's local storage. Delete them in History.
- **Figures and code from runs:** in this browser's workspace storage. Delete them in Settings.
- **Settings,** including an API key you enter.
- **Code runs** happen in your browser (Python in a Web Worker), and the notebook (`notebook.osc.earth`) runs entirely in your browser and never talks to the OSA server.

## Your choices

- **Sharing the page:** the "Share page URL to help answer questions" box in the widget's footer is on by default;
  your choice is remembered in this browser.
- **Your history:** delete chats in History and the workspace in Settings.
- **Your own key:** with your own Anthropic or OpenRouter key, requests go to that provider under your agreement.
- **What you type:** please keep personal information out of questions and feedback.

## What OSA does not do

- No tracing or observability service receives conversation content on osc.earth.
  (OSA can send traces to LangFuse when an operator installs and configures it; the osc.earth deployment does not.)
- The widget loads no analytics, advertising or tracking scripts.
- Conversations are not sold, shared for advertising, or used to train models.

## Communities

Each community that embeds the assistant may also describe it in its own privacy policy;
for NEMAR, see the [NEMAR privacy policy](https://docs.nemar.org/policies/privacy/).

## Questions and changes

Open an issue at [github.com/OpenScience-Collective/osa](https://github.com/OpenScience-Collective/osa/issues).
Changes to this page are in its history on GitHub.
