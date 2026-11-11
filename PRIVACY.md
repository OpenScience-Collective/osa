# Privacy

This page describes what the Open Science Assistant (OSA), an artificial intelligence (AI) assistant, does with what you type,
as it runs at osc.earth for the communities that embed it:
for example the Neuroelectromagnetic Data Archive and Tools Resource (NEMAR),
the Hierarchical Event Descriptors (HED) standard, the Brain Imaging Data Structure (BIDS), and EEGLAB.
The Open Science Collective (OSC) operates that deployment.
If you run OSA yourself, your own configuration decides the model provider, logging and tracing.

## In short

- The server does not store your questions or the assistant's answers on disk,
  apart from the log lines described under [Server logs](#what-is-stored).
  It holds a conversation in memory for 24 hours after its last message so you can continue it;
  after that it cannot be continued, and it is removed at the next new chat in that community or on a restart.
- The models run under zero data retention agreements:
  the provider is not to store your prompts or the replies once it has answered,
  apart from a short in-memory prompt cache, and is not to train on them.
- Usage metrics record how each request was served (model, token counts, cost, timing, tool names, conversation id),
  never what was asked or answered.
  With "Share usage data" off in the Privacy choices dialog, a request's row keeps only the counts, costs and timing:
  no conversation id, no tool names and no trace.
- Your chat history, figures, code and settings stay in your browser.
- No tracing service, analytics script or advertising receives your conversations.

## What is sent when you ask a question

| What | When | Why |
| --- | --- | --- |
| Your message | Always | To answer it. |
| A conversation id | From the second message on (the server issues it) | To continue the same conversation. Sent whether or not you share usage data; the server only stops writing it to the usage log. |
| A note that you turned off "Share usage data" | When that choice in the Privacy choices dialog is off (on by default) | So the server leaves the conversation id and tool names out of that request's usage row and makes no trace of it (see [What is stored](#what-is-stored)). |
| The page's full address (including anything after a `?` or `#`) and its title | When "Use this page for answers" in the Privacy choices dialog is on (the default) | So the assistant knows where you are; it may also read that public page to answer. (A URL, or uniform resource locator, is the page's web address.) Check the address bar before you leave sharing on. |
| The dataset the page shows, and the community's instructions for that page | On pages that set them (for example a NEMAR dataset page) | The question is usually about that dataset. Sent whether or not you use the page's address. |
| The text of earlier turns in this chat | When the server may have lost the conversation (after a reload, or after more than 24 hours) | To restore the conversation. Text only: never code runs, results or figures. |
| The results of code you allow to run in your browser, and your decision on a plan | When the assistant runs code or proposes a plan | Printed output, any error text, a short summary, the names of files the run saved, the run time and up to three figures; for a plan, the steps as you left them and any note you wrote. So the assistant can continue the analysis. Code you edit and run yourself is never sent. |
| Your memory budget setting | When code can run | So the assistant sizes its reads. |
| Your search words, its filters and the top results | When AI Mode is on in a community's dataset search (NEMAR's Discover page, once it offers it) | To write a short overview of the results. The overview is kept in the server's memory for 24 hours, and a reader who runs the same search gets that one. |
| Your own application programming interface (API) key | Only if you enter one in Settings | To call the provider with your key. It is stored in this browser and sent with each request through the Cloudflare Worker and the OSA server, which do not write it to disk. |

## Where it goes

1. **A Cloudflare Worker** (`widget.osc.earth`) receives the request and passes it to the OSA server.
   It uses your Internet Protocol (IP) address to limit how many requests one address can make.
   The counters are named by the IP address and expire within two hours,
   and the Worker does not forward your IP address to the OSA server.
   Cloudflare, which runs the Worker, sees your IP address as any web host does,
   and keeps logs of the Worker's requests (request metadata such as the path, status and errors), not the text you send.
2. **The OSA server** (`api.osc.earth`) builds the prompt, calls the model, and runs the community's tools.
3. **The model provider.**
   Claude models run on the Claude Platform on Amazon Web Services (AWS), Anthropic's own API billed through AWS.
   Every community uses Claude by default.
   If you choose GPT-6 Luna, Qwen3 Next or gpt-oss-120b in Settings, that model runs on Amazon Bedrock in US regions.
   Both run under zero data retention agreements.
   A provider may hold part of a prompt in memory (prompt caching), five minutes by default, so the next request is faster.
   If you enter your own key, the request goes to Anthropic or OpenRouter under your own agreement with them.
4. **Community tools and your browser's downloads.**
   When the assistant looks something up, the service it asks receives that lookup;
   for example, NEMAR's dataset server (`mcp.nemar.org`) receives the dataset ids and search terms.
   When code runs in your browser, your browser fetches the recording directly from the community's data host;
   the data does not pass through OSA.
   When code first runs, your browser downloads the Python runtime from the jsDelivr content delivery network,
   which sees your IP address.

## What is stored

**On the OSA server, in memory.**
The conversation (your messages, the replies, the code the assistant wrote and its output)
is kept for 24 hours after its last message, so a follow-up question has its context.
After that it cannot be continued.
The server removes expired conversations the next time a new chat starts in that community,
so on a quiet community one can stay in memory longer;
a restart or a busy server clears it sooner.
Figures are not kept, and the conversation is never written to disk.

**On the OSA server, on disk: usage metrics.**
One row per request:
the time, the community, the endpoint, the model, token counts, the estimated cost,
the names of the tools used, the status, the duration,
whether the quick or the analysis model answered, whether your own key paid for it,
a short note on why a request failed,
and, unless you turned off "Share usage data", the conversation id, and the id of the trace described below.
With that choice off, a request's row still holds the counts, the estimated cost, the tier and the timing,
so budgets add up, but it has no conversation id, no tool names and no trace id,
and no trace of the request is made.
The dataset a page names is still sent with the question, as the table says.
Your thumbs and comments are still received, but with usage sharing off they are not attached to a trace.
No usage-metrics row holds your question, the reply, the page's address or your IP address.
They are used to track cost, budgets and reliability, and there is no automatic deletion yet.

**On the OSA server, on disk: feedback.**
A thumbs up or down, and any comment you wrote with it.
A thumbs down you have clicked is sent when you press Send or Skip,
and also when you send another question, start a new chat or close the window, with whatever you had typed.
Feedback from the "Send feedback" form carries your comment and, only if you share the page, the page's address.
Each also carries the ids that tie it to the conversation and the reply.
Feedback is read by the OSA maintainers and that community's maintainers, so please do not include personal information.
There is no automatic deletion.

**Server logs.**
They do not record your questions or the replies as such.
They hold errors, request paths and status codes.
A line about a failure can also hold what failed:
the search words a lookup was running, the address of a page being read (the page you shared),
and the error text a provider returned.
Logs are kept on the server's disk.

## What stays in your browser

- **Chat history:** your last 30 chats, in this browser's local storage. Delete them in History.
- **Figures and code from runs:** in this browser's workspace storage. Delete them in Settings.
- **Settings,** including an API key you enter (stored unencrypted in this browser's local storage).
- **Code runs** happen in your browser (Python in a Web Worker).
  The notebook (`notebook.osc.earth`) also runs in your browser and sends nothing to the OSA server;
  its Python runtime also comes from the jsDelivr network, and your code reads data directly from the community's data host.

## Your choices

- **Privacy choices:** the "Privacy choices" button in the widget's footer opens a dialog with two choices, both on by default.
  "Use this page for answers" sends the page's address and title with your question
  (the dataset a page names is still sent when it is off).
  "Share usage data" lets the usage log link your requests into a conversation and name the tools that ran;
  off, a request's row has no conversation id, tool names or trace id.
  Your choices are remembered in this browser and apply in your other open tabs without a reload.
- **Your history:** delete chats in History and the workspace in Settings.
  This clears your browser's copy; the server's in-memory copy cannot be continued after 24 hours and is removed shortly after (see [What is stored](#what-is-stored)).
- **Your own key:** with your own Anthropic or OpenRouter key, requests go to that provider under your agreement.
- **What you type:** please keep personal information out of questions and feedback.

## What OSA does not do

- No tracing or observability service receives conversation content on osc.earth.
  (OSA can send traces to LangFuse when an operator installs and configures it; the osc.earth deployment does not.
  With "Share usage data" off, OSA makes no trace even where one is configured.)
- The widget loads no analytics, advertising or tracking scripts.
- Conversations are not sold, shared for advertising, or used to train models.

## Communities

Each community that embeds the assistant may also describe it in its own privacy policy;
for NEMAR, see the [NEMAR privacy policy](https://docs.nemar.org/policies/privacy/).

## Questions and changes

For a privacy question, or a request about data that concerns you,
email [privacy@osc.earth](mailto:privacy@osc.earth); the OSA maintainers read it.
For anything else, open an issue at [github.com/OpenScience-Collective/osa](https://github.com/OpenScience-Collective/osa/issues);
issues are public, so please do not put personal information in one.
Changes to this page are in its history on GitHub.
