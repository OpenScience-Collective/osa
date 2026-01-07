"""HED Assistant - Specialized agent for Hierarchical Event Descriptors.

This agent provides expertise on HED annotation, schemas, validation,
and tool usage. It has access to 26 HED documents (6 preloaded, 20 on-demand).
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from src.agents.base import ToolAgent
from src.tools.hed import (
    HED_DOCS,
    get_preloaded_hed_content,
    retrieve_hed_doc,
)
from src.tools.hed_validation import get_hed_schema_versions, validate_hed_string

# HED System Prompt - adapted from QP's hedAssistantSystemPrompt.ts
HED_SYSTEM_PROMPT_TEMPLATE = """You are a technical assistant specialized in helping users with the Hierarchical Event Descriptors (HED) standard.
You provide explanations, troubleshooting, and step-by-step guidance for annotating events and data using HED tags.
You must stick strictly to the topic of HED and avoid digressions.
All responses should be accurate and based on the official HED specification and resource documentation.

When a user's question is ambiguous, assume the most likely meaning and provide a useful starting point,
but also ask clarifying questions when necessary.
Communicate in a formal and technical style, prioritizing precision and accuracy while remaining clear.
Balance clarity and technical accuracy, starting with accessible explanations and expanding into more detail when needed.
Answers should be structured and easy to follow, with examples where appropriate.

The HED homepage is https://www.hedtags.org/
The HED specification documentation is available at https://www.hedtags.org/hed-specification
Main HED resources and guides are at https://www.hedtags.org/hed-resources
The HED GitHub organization is at https://github.com/hed-standard
HED schemas can be viewed at https://www.hedtags.org/hed-schema-browser

You will respond with markdown formatted text. Be concise and include only the most relevant information unless told otherwise.

## Using Tools Liberally

You have access to tools for validation and documentation retrieval. **Use them proactively and liberally.**

- Tool calls are inexpensive, so don't hesitate to validate strings or retrieve docs
- When in doubt about syntax or tag validity, use the validation tool
- Validation tools strengthen your responses and build user trust
- Retrieve relevant documentation to ensure your answers are accurate and current

Think of tools as enhancing your capabilities at minimal cost. Prefer calling a tool to confirm your understanding over making assumptions.

## Using the retrieve_hed_docs Tool

Before responding, use the retrieve_hed_docs tool to get any documentation you need.
Include links to relevant documents in your response.

**Important guidelines:**
- Do NOT retrieve docs that have already been preloaded (listed below)
- Retrieve multiple relevant documents at once so you have all the information you need
- Get background information documents in addition to specific documents for the question
- If you have already loaded a document in this conversation, don't load it again

## Preloaded Documents

The following documents are already available to you (DO NOT retrieve these):

{preloaded_docs}

## On-Demand Documents

Use retrieve_hed_docs to fetch these when needed:

{ondemand_docs}

## Guidelines for HED Annotations

When providing examples of HED annotations:
- Use code blocks for clarity
- Your annotations MUST be valid
- Only use tags from the HED schema that follow HED rules
- ALWAYS use the SHORT FORM of tags

## CRITICAL: Validate Examples Before Showing to Users

**Important Workflow for Providing Examples:**

When you want to give the user an example HED annotation string:

1. **Generate** the example based on documentation and your knowledge
2. **VALIDATE** using the validate_hed_string tool BEFORE showing to user
3. **If valid**: Present the example to the user
4. **If invalid**:
   - Fix the example based on the error messages
   - OR use a known-good example from the documentation instead
   - Validate again until correct
5. **Never show invalid examples to users**

This self-check process ensures you only provide correct examples to researchers,
building trust and preventing users from adopting invalid annotation patterns.

**Example workflow:**
```
User asks: "How do I annotate a visual stimulus?"
Your internal process:
1. Generate: "Sensory-event, Visual-presentation, Red"
2. Call: validate_hed_string("Sensory-event, Visual-presentation, Red")
3. If valid → Show to user
4. If invalid → Fix based on errors OR find example in docs → Validate → Show
```

## Key References

- **HED standard schema**: JSON vocabulary with all valid tags and properties
- **HED annotation semantics**: How tags should be used in annotations (consult first for annotation advice)
- **HED errors**: List of validation errors and meanings (for explaining validation errors)
- **Test cases**: JSON examples of passing/failing tests (some examples have multiple error codes)

If you are unsure, do not guess or hallucinate. Stick to what you can learn from the documents.
Feel free to read as many documents as you need.

Common topics include:
- Basic HED annotation and tag selection
- HED string syntax and formatting
- Working with HED schemas and vocabularies
- Validation procedures and error resolution
- Tool usage (Python, MATLAB, JavaScript, online)
- Integration with BIDS, NWB, and EEGLAB
- Event categorization and experimental design
- Advanced features like definitions and temporal scope
"""


def _format_preloaded_section(preloaded_content: dict[str, str]) -> str:
    """Format preloaded documents for the system prompt."""
    sections = []
    for doc in HED_DOCS.get_preloaded():
        content = preloaded_content.get(doc.url, "")
        if content:
            # Truncate very long content (like the schema JSON)
            if len(content) > 50000:
                content = content[:50000] + "\n\n... [truncated for length]"
            sections.append(f"### {doc.title}\nSource: {doc.url}\n\n{content}")
    return "\n\n---\n\n".join(sections)


def _format_ondemand_section() -> str:
    """Format on-demand documents list for the system prompt."""
    lines = []
    for category in HED_DOCS.get_categories():
        on_demand = [d for d in HED_DOCS.get_by_category(category) if not d.preload]
        if on_demand:
            category_name = category.replace("-", " ").replace("_", " ").title()
            lines.append(f"**{category_name}:**")
            for doc in on_demand:
                lines.append(f"- {doc.title}: `{doc.url}`")
            lines.append("")
    return "\n".join(lines)


@tool
def retrieve_hed_docs(url: str) -> str:
    """Retrieve HED documentation by URL.

    Use this tool to fetch HED documentation when you need detailed
    information about HED annotation, schemas, or tools.

    Args:
        url: The HTML URL of the HED documentation page to retrieve.
             Must be one of the URLs listed in the on-demand documents section.

    Returns:
        The document content in markdown format, or an error message.
    """
    result = retrieve_hed_doc(url)
    if result.success:
        return f"# {result.title}\n\nSource: {result.url}\n\n{result.content}"
    return f"Error retrieving {result.url}: {result.error}"


class HEDAssistant(ToolAgent):
    """Specialized assistant for HED (Hierarchical Event Descriptors).

    This agent has expertise in HED annotation, schemas, validation, and tools.
    It preloads 6 core documents into the system prompt and can fetch 20 more on-demand.

    Example:
        ```python
        from src.agents.hed import HEDAssistant
        from src.core.services.llm import get_llm_service

        llm_service = get_llm_service()
        model = llm_service.get_model("claude-3-5-sonnet")

        assistant = HEDAssistant(model)
        result = assistant.invoke("How do I annotate a button press event?")
        print(result["messages"][-1].content)
        ```
    """

    def __init__(
        self,
        model: BaseChatModel,
        preload_docs: bool = True,
    ) -> None:
        """Initialize the HED Assistant.

        Args:
            model: The language model to use.
            preload_docs: Whether to preload core docs into system prompt.
                         Set to False for testing without network calls.
        """
        self._preload_docs = preload_docs
        self._preloaded_content: dict[str, str] = {}

        # Preload documents if requested
        if preload_docs:
            self._preloaded_content = get_preloaded_hed_content()

        # Initialize with HED tools: documentation retrieval and validation
        super().__init__(
            model=model,
            tools=[retrieve_hed_docs, validate_hed_string, get_hed_schema_versions],
            system_prompt=None,  # We override get_system_prompt
        )

    def get_system_prompt(self) -> str:
        """Build the system prompt with preloaded documents."""
        if self._preload_docs and self._preloaded_content:
            preloaded_section = _format_preloaded_section(self._preloaded_content)
        else:
            preloaded_section = "(Preloaded documents not available - use retrieve_hed_docs tool)"

        ondemand_section = _format_ondemand_section()

        return HED_SYSTEM_PROMPT_TEMPLATE.format(
            preloaded_docs=preloaded_section,
            ondemand_docs=ondemand_section,
        )

    @property
    def preloaded_doc_count(self) -> int:
        """Number of documents successfully preloaded."""
        return len(self._preloaded_content)

    @property
    def available_doc_count(self) -> int:
        """Total number of documents available (preloaded + on-demand)."""
        return len(HED_DOCS.docs)


def create_hed_assistant(
    model_name: str | None = None,
    api_key: str | None = None,
    preload_docs: bool = True,
) -> HEDAssistant:
    """Convenience function to create a HED assistant.

    Args:
        model_name: Name of the model to use. If None, uses settings.default_model
                   (default: qwen/qwen3-235b-a22b-2507 via Cerebras).
        api_key: Optional API key override (for BYOK).
        preload_docs: Whether to preload core docs.

    Returns:
        Configured HEDAssistant instance.
    """
    from src.core.services.llm import get_llm_service

    llm_service = get_llm_service()
    model = llm_service.get_model(model_name, api_key=api_key)
    return HEDAssistant(model, preload_docs=preload_docs)
