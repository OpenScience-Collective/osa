"""HED documentation retrieval tools for OSA.

Provides tools for fetching HED (Hierarchical Event Descriptors)
documentation from hedtags.org and the hed-specification repository.
"""

from src.tools.base import DocPage, DocRegistry, RetrievedDoc
from src.tools.fetcher import DocumentFetcher, get_fetcher

# HED Documentation Registry
HED_DOCS = DocRegistry(
    name="hed",
    docs=[
        # Preloaded core documentation
        DocPage(
            title="HED Introduction",
            url="https://hed-specification.readthedocs.io/en/latest/01_Introduction.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/docs/source/01_Introduction.md",
            preload=True,
            category="getting-started",
        ),
        DocPage(
            title="HED Annotation Quick Guide",
            url="https://hed-specification.readthedocs.io/en/latest/04_Basic_annotation.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/docs/source/04_Basic_annotation.md",
            preload=True,
            category="getting-started",
        ),
        # On-demand specification documents
        DocPage(
            title="HED Terms and Notation",
            url="https://hed-specification.readthedocs.io/en/latest/02_Terminology.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/docs/source/02_Terminology.md",
            category="specification",
        ),
        DocPage(
            title="HED Schema Format",
            url="https://hed-specification.readthedocs.io/en/latest/03_HED_formats.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/docs/source/03_HED_formats.md",
            category="specification",
        ),
        DocPage(
            title="HED Advanced Annotation",
            url="https://hed-specification.readthedocs.io/en/latest/05_Advanced_annotation.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/docs/source/05_Advanced_annotation.md",
            category="annotation",
        ),
        DocPage(
            title="HED Errors and Warnings",
            url="https://hed-specification.readthedocs.io/en/latest/Appendix_B.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/master/docs/source/Appendix_B.md",
            category="reference",
        ),
        # HED tools documentation
        DocPage(
            title="HED Python Tools",
            url="https://hed-python.readthedocs.io/en/latest/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-python/master/docs/source/introduction.md",
            category="tools",
        ),
        DocPage(
            title="HED MATLAB Tools",
            url="https://hed-matlab.readthedocs.io/en/latest/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-matlab/main/docs/source/introduction.md",
            category="tools",
        ),
    ],
)


def get_hed_registry() -> DocRegistry:
    """Get the HED documentation registry."""
    return HED_DOCS


def retrieve_hed_doc(url: str, fetcher: DocumentFetcher | None = None) -> RetrievedDoc:
    """Retrieve a specific HED documentation page by URL.

    Args:
        url: The HTML URL of the document to retrieve.
        fetcher: Optional fetcher instance. Uses default if not provided.

    Returns:
        RetrievedDoc with content or error message.
    """
    if fetcher is None:
        fetcher = get_fetcher()

    doc = HED_DOCS.find_by_url(url)
    if doc is None:
        return RetrievedDoc(
            title="Unknown Document",
            url=url,
            content="",
            error=f"Document not found in HED registry: {url}",
        )

    return fetcher.fetch(doc)


def retrieve_hed_docs_by_category(
    category: str, fetcher: DocumentFetcher | None = None
) -> list[RetrievedDoc]:
    """Retrieve all HED documents in a category.

    Args:
        category: The category to retrieve (e.g., 'getting-started', 'specification').
        fetcher: Optional fetcher instance.

    Returns:
        List of RetrievedDoc results.
    """
    if fetcher is None:
        fetcher = get_fetcher()

    docs = HED_DOCS.get_by_category(category)
    return fetcher.fetch_many(docs)


def get_preloaded_hed_content(fetcher: DocumentFetcher | None = None) -> dict[str, str]:
    """Fetch and return all preloaded HED documentation.

    This content is meant to be embedded in the system prompt.

    Args:
        fetcher: Optional fetcher instance.

    Returns:
        Dictionary mapping URL to markdown content.
    """
    if fetcher is None:
        fetcher = get_fetcher()

    return fetcher.preload(HED_DOCS.docs)


def format_hed_doc_list() -> str:
    """Format a readable list of available HED documentation.

    Used in tool descriptions to show what docs are available.
    """
    return HED_DOCS.format_doc_list()


# LangChain-compatible tool function signature
def retrieve_hed_docs(url: str) -> str:
    """Retrieve HED documentation by URL.

    Use this tool to fetch HED documentation when you need detailed
    information about HED annotation, schemas, or tools.

    Available documents:
    {doc_list}

    Args:
        url: The HTML URL of the HED documentation page to retrieve.

    Returns:
        The document content in markdown format, or an error message.
    """
    result = retrieve_hed_doc(url)
    if result.success:
        return f"# {result.title}\n\nSource: {result.url}\n\n{result.content}"
    return f"Error retrieving {result.url}: {result.error}"


# Update docstring with available docs
retrieve_hed_docs.__doc__ = retrieve_hed_docs.__doc__.format(doc_list=format_hed_doc_list())
