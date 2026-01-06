# QP Tool System Guide

## Overview

The QP system uses a flexible tool architecture that allows AI assistants to call external functions to retrieve information, execute code, or perform other actions. You **can** define your own tools - the system is designed to make this straightforward.

## What Are Tools?

Tools are functions that the AI can call during a conversation. Examples:
- **retrieve_hed_docs**: Fetches HED documentation from URLs
- **retrieve_bids_docs**: Fetches BIDS specification documents
- **execute_python_code**: Runs Python code in a Jupyter kernel
- **get_nwb_file_info**: Gets information about NWB files
- **get_dandiset_assets**: Lists assets in a Dandiset

Tools are **NOT executed by the worker** - they run in the client's browser (or wherever the QP client is running).

## Tool Interface (`QPTool`)

Every tool must implement the `QPTool` interface:

```typescript
export interface QPTool {
  // Tool function definition (sent to AI model)
  toolFunction: QPFunctionDescription;

  // Execute the tool with given parameters
  execute: (
    params: any,
    context?: ToolExecutionContext
  ) => Promise<{
    result: string;
    newMessages?: ChatMessage[]
  }>;

  // Get detailed description for system prompt
  getDetailedDescription: () => Promise<string>;

  // Whether tool requires user permission before execution
  requiresPermission: boolean;

  // Optional: Custom UI for displaying tool calls
  createToolCallView?: (
    toolCall: ORToolCall,
    toolOutput: (ChatMessage & { role: "tool" }) | undefined
  ) => React.JSX.Element;
}
```

### Component Breakdown

#### 1. `toolFunction: QPFunctionDescription`

This is the **OpenAI/OpenRouter function calling format** that gets sent to the AI model:

```typescript
export type QPFunctionDescription = {
  description?: string;  // What the tool does
  name: string;          // Tool name (must be unique)
  parameters: object;    // JSON Schema defining parameters
};
```

**Example** (from `retrieveHedDocs.tsx`):
```typescript
export const toolFunction: QPFunctionDescription = {
  name: "retrieve_hed_docs",
  description: "Retrieve content from a list of HED documentation files.",
  parameters: {
    type: "object",
    properties: {
      urls: {
        type: "array",
        items: {
          type: "string",
        },
        description: "List of document URLs to retrieve",
      },
    },
    required: ["urls"],
  },
};
```

#### 2. `execute()` - Tool Implementation

This function does the actual work when the AI calls the tool:

```typescript
execute: (params: any, context?: ToolExecutionContext) => Promise<{
  result: string;           // Required: String result to send back to AI
  newMessages?: ChatMessage[];  // Optional: Additional messages (e.g., images)
}>
```

**Parameters**:
- `params`: The arguments passed by the AI (parsed from JSON)
- `context`: Execution context with useful utilities

**ToolExecutionContext**:
```typescript
export interface ToolExecutionContext {
  // Jupyter connectivity for execute_python_code tool
  jupyterConnectivity?: any;

  // Whether images need to be sent as user messages (model-specific)
  imageUrlsNeedToBeUser?: boolean;

  // Cancellation support for long-running operations
  onCancelRef?: {
    onCancel?: () => void;
  };

  // Request user permission before executing
  requestPermission?: (
    toolName: string,
    toolDescription: string
  ) => Promise<boolean>;
}
```

**Example implementation** (simplified from `retrieveHedDocs.tsx`):
```typescript
export const execute = async (
  params: RetrieveHedDocsParams,
  _o?: ToolExecutionContext,
): Promise<{ result: string }> => {
  const { urls } = params;

  const results: string[] = [];

  for (const url of urls) {
    try {
      // Fetch the documentation
      const response = await fetch(url);
      const text = await response.text();

      // Parse and format
      const content = parseMarkdown(text);
      results.push(`# ${url}\n\n${content}`);
    } catch (error) {
      results.push(`# ${url}\n\nError: ${error.message}`);
    }
  }

  return {
    result: results.join("\n\n---\n\n"),
  };
};
```

#### 3. `getDetailedDescription()` - Extended Documentation

This provides **comprehensive information** that gets added to the system prompt. This is where you:
- List all available options (like documentation URLs)
- Explain when to use the tool
- Provide usage examples
- Include preloaded content

**Example** (from `retrieveHedDocs.tsx`):
```typescript
export const getDetailedDescription = async (): Promise<string> => {
  const docPages = getDocPages();
  const retLines: string[] = [];

  retLines.push("Retrieve HED documentation files.");
  retLines.push("");
  retLines.push("### Preloaded Documents (already available):");

  // List preloaded docs
  const preloaded = docPages.filter(doc => doc.includeFromStart);
  for (const doc of preloaded) {
    retLines.push(`- ${doc.title}: ${doc.url} (preloaded)`);
  }

  // List on-demand docs by category
  retLines.push("");
  retLines.push("### Specification Details:");
  const specDocs = docPages.filter(d =>
    d.category === "specification-details" && !d.includeFromStart
  );
  for (const doc of specDocs) {
    retLines.push(`- ${doc.title}: ${doc.url}`);
  }

  // Include full content of preloaded docs
  retLines.push("");
  retLines.push("Here are the contents of the preloaded documents:");
  for (const doc of preloaded) {
    const content = await fetchAndParse(doc.sourceUrl);
    retLines.push("");
    retLines.push(`### ${doc.title}`);
    retLines.push("```");
    retLines.push(content);
    retLines.push("```");
  }

  return retLines.join("\n");
};
```

**This entire output becomes part of the system message** sent to the AI!

#### 4. `requiresPermission: boolean`

If `true`, the user will be prompted to approve execution before the tool runs.

**Example use cases**:
- `execute_python_code`: `true` (can execute arbitrary code)
- `retrieve_hed_docs`: `false` (just fetches documentation)

```typescript
export const requiresPermission = true;  // Ask user before executing
```

When permission is required, the execution flow is:
```
AI calls tool
    ↓
Client shows permission dialog
    ↓
User approves/denies
    ↓
If approved: execute()
If denied: return "User canceled execution."
```

#### 5. `createToolCallView()` - Custom UI (Optional)

Provides a custom React component to display the tool call in the chat:

```typescript
export const createToolCallView = (
  toolCall: ORToolCall,
  toolOutput: (ChatMessage & { role: "tool" }) | undefined,
): React.JSX.Element => {
  const args = JSON.parse(toolCall.function.arguments || "{}");
  const urls: string[] = args.urls || [];

  if (!toolOutput) {
    // Tool is being called (before result)
    return (
      <div className="tool-call-message">
        Calling {toolCall.function.name} to retrieve {urls.length} document(s)...
      </div>
    );
  } else {
    // Tool has completed (after result)
    return (
      <div className="tool-call-message">
        Retrieved:{" "}
        {urls.map(url => (
          <a key={url} href={url} target="_blank" rel="noreferrer">
            {url.split("/").pop()}
          </a>
        ))}
      </div>
    );
  }
};
```

## How Tools Are Loaded

### 1. App-Specific Tool Loading

Each assistant can have its own set of tools. The `getTools()` function in `src/tools/getTools.ts` routes to the appropriate assistant's tools:

```typescript
import getHedAssistantTools from "../assistants/hed-assistant/getTools";
import getBidsAssistantTools from "../assistants/bids-assistant/getTools";
// ... other imports

const getTools = async (_chat: Chat): Promise<QPTool[]> => {
  const appName = getAppName();

  if (appName === "hed-assistant") {
    return getHedAssistantTools();
  } else if (appName === "bids-assistant") {
    return getBidsAssistantTools();
  }
  // ... other assistants

  return []; // No tools for this assistant
};

export default getTools;
```

### 2. Assistant-Specific Tool Modules

Each assistant has its own `getTools.ts` file:

**Example**: `src/assistants/hed-assistant/getTools.ts`
```typescript
import * as retrieveHedDocs from "./retrieveHedDocs";
import { QPTool } from "../../qpcommon/types";

const getTools = async (): Promise<QPTool[]> => {
  return [retrieveHedDocs];  // Export as QPTool
};

export default getTools;
```

The tool module exports all required fields:
```typescript
// retrieveHedDocs.tsx exports:
export const toolFunction: QPFunctionDescription = { ... };
export const execute = async (...) => { ... };
export const getDetailedDescription = async () => { ... };
export const requiresPermission = false;
export const createToolCallView = (...) => { ... };
```

## Tool Execution Flow

Here's the complete flow when an AI calls a tool:

```
[1. AI decides to call a tool]
    Response: {
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "retrieve_hed_docs",
          "arguments": "{\"urls\": [\"https://...\"]}"
        }
      }]
    }
         ↓
[2. Client receives streaming response]
    Parses tool call from stream
         ↓
[3. Client looks up tool]
    const tool = tools.find(t => t.toolFunction.name === "retrieve_hed_docs");
         ↓
[4. Check if permission required]
    if (tool.requiresPermission && context.requestPermission) {
      const granted = await context.requestPermission(name, description);
      if (!granted) {
        return { result: "User canceled execution." };
      }
    }
         ↓
[5. Execute the tool]
    const params = JSON.parse(arguments);
    const { result, newMessages } = await tool.execute(params, context);
         ↓
[6. Add tool result to messages]
    messages.push({
      role: "tool",
      content: result,
      tool_call_id: "call_abc123",
    });

    // Add any extra messages (like images)
    if (newMessages) {
      messages.push(...newMessages);
    }
         ↓
[7. Send back to AI for final response]
    Calls processCompletion() again with updated messages
         ↓
[8. AI generates response using tool result]
    "Based on the HED documentation, a button press can be
     annotated using the tag (Sensory-event, (Visual-presentation, ...))"
```

**Code reference** (`src/qpcommon/completion/processCompletion.ts`):
```typescript
for (const toolCall of toolCalls) {
  const functionName = toolCall.function.name;
  const functionArgs = JSON.parse(toolCall.function.arguments || "{}");

  // Find the tool
  const tool = tools.find(t => t.toolFunction.name === functionName);
  if (!tool) {
    throw new Error("Tool not found: " + functionName);
  }

  // Check permission
  if (tool.requiresPermission && context.requestPermission) {
    const granted = await context.requestPermission(
      functionName,
      tool.toolFunction.description || ""
    );
    if (!granted) {
      ret.push({
        role: "tool",
        content: "User canceled execution.",
        tool_call_id: toolCall.id,
      });
      continue;
    }
  }

  // Execute
  const { result, newMessages } = await tool.execute(functionArgs, context);

  // Add new messages (if any)
  for (const m of newMessages || []) {
    ret.push(m);
  }

  // Add tool result
  ret.push({
    role: "tool",
    content: result,
    tool_call_id: toolCall.id,
  });
}

// Recursively call processCompletion with tool results
return await processCompletion(
  { ...chat, messages: [...chat.messages, ...ret] },
  onPartialResponse,
  tools,
  initialSystemMessage,
  context
);
```

## Creating Your Own Tool

### Step 1: Create Tool File

Create a new file in your assistant's directory, e.g., `src/assistants/hed-assistant/myNewTool.tsx`:

```typescript
/* eslint-disable @typescript-eslint/no-explicit-any */
import { ORToolCall } from "../../qpcommon/completion/openRouterTypes";
import { ChatMessage, QPFunctionDescription, ToolExecutionContext } from "../../qpcommon/types";

// 1. Define the tool function (OpenAI format)
export const toolFunction: QPFunctionDescription = {
  name: "my_new_tool",
  description: "Does something useful",
  parameters: {
    type: "object",
    properties: {
      input_param: {
        type: "string",
        description: "Description of the parameter",
      },
      optional_param: {
        type: "number",
        description: "An optional parameter",
      },
    },
    required: ["input_param"],
  },
};

// 2. Define parameter type
type MyNewToolParams = {
  input_param: string;
  optional_param?: number;
};

// 3. Implement the execute function
export const execute = async (
  params: MyNewToolParams,
  _context?: ToolExecutionContext,
): Promise<{ result: string; newMessages?: ChatMessage[] }> => {
  const { input_param, optional_param } = params;

  try {
    // Do your work here
    const result = `Processed: ${input_param} with ${optional_param || "default"}`;

    return {
      result: result,
    };
  } catch (error) {
    return {
      result: `Error: ${error instanceof Error ? error.message : "Unknown error"}`,
    };
  }
};

// 4. Provide detailed description for system prompt
export const getDetailedDescription = async (): Promise<string> => {
  return `
This tool does something useful.

### Usage:
- Use this tool when you need to...
- The input_param should be...
- The optional_param defaults to...

### Examples:
- Example 1: ...
- Example 2: ...
`;
};

// 5. Set permission requirement
export const requiresPermission = false;

// 6. (Optional) Create custom view
export const createToolCallView = (
  toolCall: ORToolCall,
  toolOutput: (ChatMessage & { role: "tool" }) | undefined,
): React.JSX.Element => {
  const args = JSON.parse(toolCall.function.arguments || "{}");

  if (!toolOutput) {
    return <div>Calling my_new_tool with {args.input_param}...</div>;
  } else {
    return <div>✓ my_new_tool completed</div>;
  }
};
```

### Step 2: Register Tool with Assistant

Edit `src/assistants/hed-assistant/getTools.ts`:

```typescript
import * as retrieveHedDocs from "./retrieveHedDocs";
import * as myNewTool from "./myNewTool";  // Add import
import { QPTool } from "../../qpcommon/types";

const getTools = async (): Promise<QPTool[]> => {
  return [
    retrieveHedDocs,
    myNewTool,  // Add to array
  ];
};

export default getTools;
```

### Step 3: Update System Prompt (Optional)

If your tool needs specific instructions, update `src/assistants/hed-assistant/hedAssistantSystemPrompt.ts`:

```typescript
const hedAssistantSystemPrompt = `
You are a technical assistant specialized in HED...

When you need to [do something specific], use the my_new_tool tool.

...rest of prompt...
`;

export default hedAssistantSystemPrompt;
```

### Step 4: Test Your Tool

The system will automatically:
1. Load your tool via `getTools()`
2. Add tool description to system message via `getDetailedDescription()`
3. Make tool available to the AI
4. Execute tool when AI calls it
5. Display tool call via `createToolCallView()` (if provided)

## Tool Examples

### Example 1: Simple Fetch Tool (retrieve_hed_docs)

**Purpose**: Fetch documentation from URLs

**Key features**:
- Takes array of URLs as input
- Fetches and parses markdown
- Preloads some documents in the description
- No permission required (safe operation)

**Structure**:
```
retrieveHedDocs.tsx
├── toolFunction (name: "retrieve_hed_docs")
├── execute() - Fetches URLs and returns formatted content
├── getDetailedDescription() - Lists all 38+ available docs with preloaded content
├── requiresPermission = false
└── createToolCallView() - Shows fetched document names with links
```

### Example 2: Code Execution Tool (execute_python_code)

**Purpose**: Run Python code in a Jupyter kernel

**Key features**:
- Executes arbitrary Python code
- Returns text output and images
- Supports cancellation
- **Requires permission** (security)
- Uses Jupyter connectivity from context

**Structure**:
```
executePythonCode.tsx
├── toolFunction (name: "execute_python_code")
├── execute()
│   ├── Checks context.jupyterConnectivity
│   ├── Creates PythonSessionClient
│   ├── Runs code
│   ├── Collects stdout/stderr/images
│   ├── Supports cancellation via context.onCancelRef
│   └── Returns text + image messages
├── getDetailedDescription() - Explains Jupyter execution
├── requiresPermission = true (user must approve)
└── createToolCallView() - Shows code with syntax highlighting
```

**Code snippet**:
```typescript
export const execute = async (
  params: ExecutePythonCodeParams,
  context?: ToolExecutionContext,
): Promise<{ result: string; newMessages?: ChatMessage[] }> => {
  if (!context?.jupyterConnectivity?.jupyterServerIsAvailable) {
    throw new Error("Jupyter server is not available");
  }

  const client = new PythonSessionClient(context.jupyterConnectivity);

  // Setup cancellation
  if (context.onCancelRef) {
    context.onCancelRef.onCancel = () => {
      client.cancelExecution();
    };
  }

  // Execute code
  await client.initiate();
  await client.runCode(params.code);
  await client.waitUntilIdle();
  await client.shutdown();

  // Return text output + images in separate message
  return {
    result: outputText || "[no output]",
    newMessages: imageItems.length > 0 ? [{
      role: "user",  // Images must be user messages for some models
      content: imageItems.map(item => ({
        type: "image_url",
        image_url: {
          url: `data:image/png;base64,${item.content}`,
        },
      })),
    }] : undefined,
  };
};
```

### Example 3: Multi-Source Tool (retrieve_bids_docs)

**Purpose**: Similar to retrieve_hed_docs but for BIDS specification

**Key differences**:
- Different set of documentation URLs
- Different preloading strategy
- Different parsing approach

**Shows**: You can create similar tools for different domains by following the same pattern.

## Advanced Tool Features

### 1. Returning Additional Messages

Tools can return more than just text - they can add messages to the conversation:

```typescript
export const execute = async (params: any) => {
  // Generate an image
  const imageData = generateSomeImage();

  return {
    result: "I generated an image for you.",
    newMessages: [{
      role: "assistant",
      content: [{
        type: "image_url",
        image_url: {
          url: `data:image/png;base64,${imageData}`,
        },
      }],
    }],
  };
};
```

### 2. Using Execution Context

The `ToolExecutionContext` provides useful utilities:

**Jupyter connectivity**:
```typescript
if (context?.jupyterConnectivity?.jupyterServerIsAvailable) {
  // Use Jupyter features
}
```

**Cancellation support**:
```typescript
let canceled = false;
if (context?.onCancelRef) {
  context.onCancelRef.onCancel = () => {
    canceled = true;
    // Cancel long-running operation
  };
}
```

**Request permission**:
```typescript
if (context?.requestPermission) {
  const granted = await context.requestPermission(
    "dangerous_operation",
    "This will do something dangerous"
  );
  if (!granted) {
    return { result: "User denied permission" };
  }
}
```

### 3. Complex Parameters

Use JSON Schema to define complex parameter structures:

```typescript
export const toolFunction: QPFunctionDescription = {
  name: "complex_tool",
  description: "A tool with complex parameters",
  parameters: {
    type: "object",
    properties: {
      simple_string: {
        type: "string",
        description: "A simple string",
      },
      options: {
        type: "object",
        properties: {
          option_a: { type: "boolean" },
          option_b: { type: "number" },
        },
        description: "Nested options",
      },
      items: {
        type: "array",
        items: {
          type: "object",
          properties: {
            name: { type: "string" },
            value: { type: "number" },
          },
        },
        description: "Array of items",
      },
    },
    required: ["simple_string"],
  },
};
```

The AI will call this with properly structured JSON:
```json
{
  "simple_string": "test",
  "options": {
    "option_a": true,
    "option_b": 42
  },
  "items": [
    { "name": "item1", "value": 1 },
    { "name": "item2", "value": 2 }
  ]
}
```

### 4. Preloading Content

For documentation tools, preloading common content into `getDetailedDescription()` reduces API calls:

```typescript
export const getDetailedDescription = async (): Promise<string> => {
  const lines: string[] = [];

  // List available docs
  lines.push("### Available documentation:");
  for (const doc of allDocs) {
    lines.push(`- ${doc.title}: ${doc.url}`);
  }

  // Preload frequently used docs
  lines.push("");
  lines.push("### Preloaded (already available):");
  for (const doc of commonDocs) {
    const content = await fetchDoc(doc.url);
    lines.push("");
    lines.push(`#### ${doc.title}`);
    lines.push("```");
    lines.push(content);
    lines.push("```");
  }

  return lines.join("\n");
};
```

This way, the AI has immediate access to common docs without calling the tool.

## Tool Best Practices

### 1. Clear Naming

Use descriptive, action-oriented names:
- ✅ `retrieve_hed_docs`, `execute_python_code`, `get_dandiset_assets`
- ❌ `hed_tool`, `python`, `dandiset`

### 2. Comprehensive Descriptions

The `description` field should explain:
- **What** the tool does
- **When** to use it
- **What** parameters it needs

The `getDetailedDescription()` should provide:
- Complete list of options
- Usage examples
- Preloaded content (if applicable)
- Category organization

### 3. Error Handling

Always wrap tool execution in try-catch:

```typescript
export const execute = async (params: any) => {
  try {
    // Do work
    return { result: "Success" };
  } catch (error) {
    // Return error as string, don't throw
    return {
      result: `Error: ${error instanceof Error ? error.message : "Unknown error"}`,
    };
  }
};
```

This allows the AI to see and handle errors gracefully.

### 4. Parameter Validation

Validate parameters in execute():

```typescript
export const execute = async (params: MyParams) => {
  if (!params.required_field) {
    return { result: "Error: required_field is missing" };
  }

  if (params.number_field && params.number_field < 0) {
    return { result: "Error: number_field must be positive" };
  }

  // Continue with valid params
};
```

### 5. Permission for Dangerous Operations

Use `requiresPermission = true` for tools that:
- Execute code
- Make external API calls with side effects
- Modify files or data
- Access sensitive information

### 6. Custom Views for Better UX

Implement `createToolCallView()` to:
- Show progress during execution
- Display results in a formatted way
- Provide links to referenced resources
- Show syntax-highlighted code

## Tool Limitations

### What Tools CANNOT Do

**1. Access Worker Secrets**
- Tools run in the client (browser)
- Cannot access server-side secrets or environment variables
- Cannot directly call OpenRouter API (that's what the worker does)

**2. Persist State Between Calls**
- Each tool execution is independent
- No shared state between executions
- Use the conversation history if you need context

**3. Modify System Prompt**
- System prompt is assembled once at the start
- Tools cannot change the prompt mid-conversation
- `getDetailedDescription()` is called once when assembling the initial prompt

**4. Execute Synchronously**
- All tool execution is asynchronous
- The AI waits for tool results before responding
- Tools should complete quickly (< 30 seconds ideally)

### What Tools CAN Do

**1. Make HTTP Requests**
- Fetch documentation, APIs, etc.
- As long as CORS allows it

**2. Use Browser APIs**
- LocalStorage, IndexedDB
- WebSockets, WebRTC
- Canvas, WebGL

**3. Interact with Extensions**
- Jupyter kernels
- VS Code extensions (if running in that context)
- Other browser extensions

**4. Return Rich Content**
- Images (as base64 data URLs)
- Formatted text
- Multiple messages

## Common Patterns

### Pattern 1: Documentation Retrieval Tool

**Use case**: Fetch content from multiple possible sources

**Structure**:
```typescript
// List all available docs
export const getDocPages = (): DocPage[] => [...];

// Preload common docs in description
export const getDetailedDescription = async () => {
  // List all docs
  // Include full content of preloaded docs
};

// Fetch requested docs
export const execute = async (params: { urls: string[] }) => {
  const results = await Promise.all(
    params.urls.map(url => fetchAndParse(url))
  );
  return { result: results.join("\n\n") };
};
```

### Pattern 2: Code Execution Tool

**Use case**: Run code in a sandboxed environment

**Structure**:
```typescript
export const execute = async (
  params: { code: string },
  context?: ToolExecutionContext
) => {
  // Check environment is available
  if (!context?.environment) {
    throw new Error("Environment not available");
  }

  // Setup cancellation
  if (context.onCancelRef) {
    context.onCancelRef.onCancel = () => {
      // Cancel execution
    };
  }

  // Execute code
  const output = await runCode(params.code);

  // Return output
  return { result: output };
};

export const requiresPermission = true;
```

### Pattern 3: Data Query Tool

**Use case**: Query structured data or APIs

**Structure**:
```typescript
export const toolFunction: QPFunctionDescription = {
  name: "query_data",
  parameters: {
    type: "object",
    properties: {
      query: { type: "string", description: "Query string" },
      filters: {
        type: "object",
        properties: {
          field: { type: "string" },
          value: { type: "string" },
        },
      },
      limit: { type: "number", description: "Max results" },
    },
    required: ["query"],
  },
};

export const execute = async (params: QueryParams) => {
  const results = await queryAPI(params.query, params.filters, params.limit);

  // Format results as markdown table or JSON
  const formatted = formatResults(results);

  return { result: formatted };
};
```

## Summary

**Yes, you can define your own tools!** The system is designed to make this straightforward:

1. **Create a tool file** implementing the `QPTool` interface
2. **Register it** in your assistant's `getTools.ts`
3. **Deploy** (client-side only, no worker changes needed)

**Tools are powerful** because:
- They extend what the AI can do beyond just text
- They run client-side (no server deployment needed)
- They have access to local resources (Jupyter, browser APIs, etc.)
- They can return rich content (images, formatted data, etc.)

**Tools are safe** because:
- They can require user permission
- They execute in the client (isolated from server)
- They can be cancelled mid-execution
- Errors are caught and returned as strings

**Best practices**:
- Clear naming and descriptions
- Comprehensive documentation in `getDetailedDescription()`
- Error handling (return errors, don't throw)
- Permission for dangerous operations
- Custom views for better UX

**Remember**: The worker doesn't know about individual tools - it just validates the system message (which includes tool descriptions) and proxies the AI's tool call requests back to the client for execution.
