"""HED documentation retrieval tools for OSA.

Provides tools for fetching HED (Hierarchical Event Descriptors)
documentation from hedtags.org, hed-specification, and hed-resources repos.
"""

from src.tools.base import DocPage, DocRegistry, RetrievedDoc
from src.tools.fetcher import DocumentFetcher, get_fetcher

# HED Documentation Registry - synced with QP
HED_DOCS = DocRegistry(
    name="hed",
    docs=[
        # === PRELOADED: Core (1 doc) ===
        DocPage(
            title="HED annotation semantics",
            url="https://www.hedtags.org/hed-resources/HedAnnotationSemantics.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/HedAnnotationSemantics.md",
            preload=True,
            category="core",
        ),
        # === PRELOADED: Schema (1 doc) ===
        DocPage(
            title="HED standard schema (latest)",
            url="https://raw.githubusercontent.com/hed-standard/hed-schemas/main/schemas_latest_json/HEDLatest.json",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/schemas_latest_json/HEDLatest.json",
            preload=True,
            category="schemas",
        ),
        # === PRELOADED: Specification (2 docs) ===
        DocPage(
            title="HED terminology",
            url="https://www.hedtags.org/hed-specification/02_Terminology.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/02_Terminology.md",
            preload=True,
            category="specification",
        ),
        DocPage(
            title="Basic annotation",
            url="https://www.hedtags.org/hed-specification/04_Basic_annotation.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/04_Basic_annotation.md",
            preload=True,
            category="specification",
        ),
        # === PRELOADED: Introductory (2 docs) ===
        DocPage(
            title="Introduction to HED",
            url="https://www.hedtags.org/hed-resources/IntroductionToHed.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/IntroductionToHed.md",
            preload=True,
            category="introductory",
        ),
        DocPage(
            title="How can you use HED?",
            url="https://www.hedtags.org/hed-resources/HowCanYouUseHed.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HowCanYouUseHed.md",
            preload=True,
            category="introductory",
        ),
        # === ON-DEMAND: Specification Details (6 docs) ===
        DocPage(
            title="HED formats",
            url="https://www.hedtags.org/hed-specification/03_HED_formats.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/03_HED_formats.md",
            category="specification",
        ),
        DocPage(
            title="Advanced annotation",
            url="https://www.hedtags.org/hed-specification/05_Advanced_annotation.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/05_Advanced_annotation.md",
            category="specification",
        ),
        DocPage(
            title="HED support of BIDS",
            url="https://www.hedtags.org/hed-specification/06_Infrastructure_and_tools.html#hed-support-of-bids",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/06_Infrastructure_and_tools.md",
            category="specification",
        ),
        DocPage(
            title="Library schemas",
            url="https://www.hedtags.org/hed-specification/07_Library_schemas.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/07_Library_schemas.md",
            category="specification",
        ),
        DocPage(
            title="HED errors",
            url="https://www.hedtags.org/hed-specification/Appendix_B.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/Appendix_B.md",
            category="specification",
        ),
        DocPage(
            title="Test cases",
            url="https://raw.githubusercontent.com/hed-standard/hed-specification/refs/heads/main/tests/javascriptTests.json",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/refs/heads/main/tests/javascriptTests.json",
            category="examples",
        ),
        # === ON-DEMAND: Quickstarts (3 docs) ===
        DocPage(
            title="HED annotation quickstart",
            url="https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedAnnotationQuickstart.md",
            category="quickstart",
        ),
        DocPage(
            title="BIDS annotation quickstart",
            url="https://www.hedtags.org/hed-resources/BidsAnnotationQuickstart.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/BidsAnnotationQuickstart.md",
            category="quickstart",
        ),
        DocPage(
            title="HED annotation in NWB",
            url="https://www.hedtags.org/hed-resources/HedAnnotationInNWB.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedAnnotationInNWB.md",
            category="quickstart",
        ),
        # === ON-DEMAND: Core concepts (3 docs) ===
        DocPage(
            title="Getting started with HED in NWB",
            url="https://www.hedtags.org/ndx-hed/description.html",
            source_url="https://raw.githubusercontent.com/hed-standard/ndx-hed/refs/heads/main/docs/source/description.rst",
            category="core",
        ),
        DocPage(
            title="HED conditions and design matrices",
            url="https://www.hedtags.org/hed-resources/HedConditionsAndDesignMatrices.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedConditionsAndDesignMatrices.md",
            category="core",
        ),
        DocPage(
            title="HED schemas",
            url="https://www.hedtags.org/hed-resources/HedSchemas.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedSchemas.md",
            category="core",
        ),
        # === ON-DEMAND: Tools (4 docs) ===
        DocPage(
            title="HED python tools",
            url="https://www.hedtags.org/hed-python/user_guide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-python/refs/heads/main/docs/user_guide.md",
            category="tools",
        ),
        DocPage(
            title="HED MATLAB tools",
            url="https://www.hedtags.org/hed-resources/HedMatlabTools.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedMatlabTools.md",
            category="tools",
        ),
        DocPage(
            title="HED JavaScript tools",
            url="https://www.hedtags.org/hed-javascript/docs/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-javascript/refs/heads/main/README.md",
            category="tools",
        ),
        DocPage(
            title="HED online tools",
            url="https://www.hedtags.org/hed-resources/HedOnlineTools.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedOnlineTools.md",
            category="tools",
        ),
        # === ON-DEMAND: Advanced (4 docs) ===
        DocPage(
            title="HED schema developers guide",
            url="https://www.hedtags.org/hed-resources/HedSchemaDevelopersGuide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedSchemaDevelopersGuide.md",
            category="advanced",
        ),
        DocPage(
            title="HED validation guide",
            url="https://www.hedtags.org/hed-resources/HedValidationGuide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedValidationGuide.md",
            category="advanced",
        ),
        DocPage(
            title="HED search guide",
            url="https://www.hedtags.org/hed-resources/HedSearchGuide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedSearchGuide.md",
            category="advanced",
        ),
        DocPage(
            title="HED summary guide",
            url="https://www.hedtags.org/hed-resources/HedSummaryGuide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedSummaryGuide.md",
            category="advanced",
        ),
        # === ON-DEMAND: Integration (1 doc) ===
        DocPage(
            title="HED and EEGLAB",
            url="https://www.hedtags.org/hed-resources/HedAndEEGLAB.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedAndEEGLAB.md",
            category="integration",
        ),
        # === ON-DEMAND: Reference (2 docs) ===
        DocPage(
            title="Documentation summary",
            url="https://www.hedtags.org/hed-resources/DocumentationSummary.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/DocumentationSummary.md",
            category="reference",
        ),
        DocPage(
            title="HED test datasets",
            url="https://www.hedtags.org/hed-resources/HedTestDatasets.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedTestDatasets.md",
            category="reference",
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
        category: The category to retrieve (e.g., 'core', 'specification', 'tools').
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
