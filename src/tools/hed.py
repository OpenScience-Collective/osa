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
        # === PRELOADED: Core + Specification (2 docs, ~13k tokens) ===
        DocPage(
            title="HED annotation semantics",
            url="https://www.hedtags.org/hed-resources/HedAnnotationSemantics.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedAnnotationSemantics.md",
            preload=True,
            category="core",
            description="Outlines the fundamental principles and rules for HED annotation syntax and structure.",
        ),
        # NOTE: HED schema is NOT included here - it's too large (~890KB)
        # Instead, use hed-lsp tool for schema lookups with autocomplete and semantic search
        # See: /Users/yahya/Documents/git/HED/hed-lsp
        # === Specification (1 preloaded, rest on-demand) ===
        DocPage(
            title="HED terminology",
            url="https://www.hedtags.org/hed-specification/02_Terminology.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/02_Terminology.md",
            preload=True,
            category="specification",
            description="Defines key terms and concepts used throughout the HED specification to ensure consistent understanding.",
        ),
        DocPage(
            title="Basic annotation",
            url="https://www.hedtags.org/hed-specification/04_Basic_annotation.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/04_Basic_annotation.md",
            preload=False,  # Covered by HED annotation semantics
            category="specification",
            description="Covers the essential guidelines and methods for creating basic HED annotations for events.",
        ),
        # === ON-DEMAND: Introductory (2 docs) ===
        DocPage(
            title="Introduction to HED",
            url="https://www.hedtags.org/hed-resources/IntroductionToHed.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/IntroductionToHed.md",
            preload=False,  # Can be fetched on demand for new users
            category="introductory",
            description="Provides an overview of the Hierarchical Event Descriptors (HED) system, its purpose, and its applications in event annotation.",
        ),
        DocPage(
            title="How can you use HED?",
            url="https://www.hedtags.org/hed-resources/HowCanYouUseHed.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HowCanYouUseHed.md",
            preload=False,  # Large doc (~11k tokens), fetch when discussing use cases
            category="introductory",
            description="Explains various use cases and scenarios where HED can be effectively applied for event annotation in research data.",
        ),
        # === ON-DEMAND: Specification Details (6 docs) ===
        DocPage(
            title="HED formats",
            url="https://www.hedtags.org/hed-specification/03_HED_formats.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/03_HED_formats.md",
            category="specification",
            description="Describes the different formats in which HED schemas and annotations can be represented and stored.",
        ),
        DocPage(
            title="Advanced annotation",
            url="https://www.hedtags.org/hed-specification/05_Advanced_annotation.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/05_Advanced_annotation.md",
            category="specification",
            description="Discusses use of definitions, temporal scope, and other advanced annotation features.",
        ),
        DocPage(
            title="HED support of BIDS",
            url="https://www.hedtags.org/hed-specification/06_Infrastructure_and_tools.html#hed-support-of-bids",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/06_Infrastructure_and_tools.md",
            category="specification",
            description="Explains how HED integrates with the Brain Imaging Data Structure (BIDS) for standardized event annotation in neuroimaging datasets.",
        ),
        DocPage(
            title="Library schemas",
            url="https://www.hedtags.org/hed-specification/07_Library_schemas.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/07_Library_schemas.md",
            category="specification",
            description="Details the concept of library schemas in HED, which allow for domain-specific extensions to the base HED schema.",
        ),
        DocPage(
            title="HED errors",
            url="https://www.hedtags.org/hed-specification/Appendix_B.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/main/docs/source/Appendix_B.md",
            category="specification",
            description="Lists and explains the various error codes and messages that can be encountered during HED annotation validation.",
        ),
        DocPage(
            title="Test cases",
            url="https://raw.githubusercontent.com/hed-standard/hed-specification/refs/heads/main/tests/javascriptTests.json",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-specification/refs/heads/main/tests/javascriptTests.json",
            category="examples",
            description="Examples of correct and incorrect HED annotations in JSON format for testing validation tools.",
        ),
        # === ON-DEMAND: Quickstarts (3 docs) ===
        DocPage(
            title="HED annotation quickstart",
            url="https://www.hedtags.org/hed-resources/HedAnnotationQuickstart.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedAnnotationQuickstart.md",
            category="quickstart",
            description="A step-by-step guide to quickly get started with HED annotation for events in datasets.",
        ),
        DocPage(
            title="BIDS annotation quickstart",
            url="https://www.hedtags.org/hed-resources/BidsAnnotationQuickstart.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/BidsAnnotationQuickstart.md",
            category="quickstart",
            description="A concise tutorial on how to apply HED annotations within the BIDS framework for neuroimaging data.",
        ),
        DocPage(
            title="HED annotation in NWB",
            url="https://www.hedtags.org/ndx-hed/",
            source_url="https://raw.githubusercontent.com/hed-standard/ndx-hed/main/README.md",
            category="quickstart",
            description="A concise tutorial on how to apply HED annotations within the NWB framework for neurophysiology data.",
        ),
        # === ON-DEMAND: Core concepts (3 docs) ===
        DocPage(
            title="Getting started with HED in NWB",
            url="https://www.hedtags.org/ndx-hed/description.html",
            source_url="https://raw.githubusercontent.com/hed-standard/ndx-hed/refs/heads/main/docs/source/description.rst",
            category="core",
            description="More detailed description of the ndx-hed extension architecture and usage for Neurodata Without Borders.",
        ),
        DocPage(
            title="HED conditions and design matrices",
            url="https://www.hedtags.org/hed-resources/HedConditionsAndDesignMatrices.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedConditionsAndDesignMatrices.md",
            category="core",
            description="Explains how to represent experimental conditions and design matrices using HED annotations for complex study designs.",
        ),
        DocPage(
            title="HED schemas",
            url="https://www.hedtags.org/hed-resources/HedSchemas.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedSchemas.md",
            category="core",
            description="Describes the structure and organization of HED schemas, including the standard schema and library schemas for specific domains.",
        ),
        # === ON-DEMAND: Tools (4 docs) ===
        DocPage(
            title="HED python tools",
            url="https://www.hedtags.org/hed-python/user_guide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-python/refs/heads/main/docs/user_guide.md",
            category="tools",
            description="Comprehensive guide to using the HED Python library for annotating, validating, and processing HED tags in datasets.",
        ),
        DocPage(
            title="HED MATLAB tools",
            url="https://www.hedtags.org/hed-matlab/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-matlab/main/README.md",
            category="tools",
            description="Instructions for utilizing the HED MATLAB toolbox to work with HED annotations within MATLAB environments.",
        ),
        DocPage(
            title="HED JavaScript tools",
            url="https://www.hedtags.org/hed-javascript/docs/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-javascript/refs/heads/main/README.md",
            category="tools",
            description="Guide to using HED JavaScript libraries for client-side annotation and validation of HED tags in web applications.",
        ),
        DocPage(
            title="HED online tools",
            url="https://www.hedtags.org/hed-web/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-web/main/README.md",
            category="tools",
            description="Overview of online tools available for HED annotation, validation, and schema browsing through web interfaces.",
        ),
        # === ON-DEMAND: Advanced (4 docs) ===
        DocPage(
            title="HED schema developers guide",
            url="https://www.hedtags.org/hed-schemas/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-schemas/main/README.md",
            category="advanced",
            description="Instructions and best practices for developers looking to create and maintain HED library schemas.",
        ),
        DocPage(
            title="HED validation guide",
            url="https://www.hedtags.org/hed-resources/HedValidationGuide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedValidationGuide.md",
            category="advanced",
            description="Detailed instructions on how to validate HED annotations using various tools and best practices to ensure compliance with HED standards.",
        ),
        DocPage(
            title="HED search guide",
            url="https://www.hedtags.org/hed-resources/HedSearchGuide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedSearchGuide.md",
            category="advanced",
            description="Instructions on how to effectively search and query HED tags within datasets using available tools.",
        ),
        DocPage(
            title="HED summary guide",
            url="https://www.hedtags.org/hed-resources/HedSummaryGuide.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedSummaryGuide.md",
            category="advanced",
            description="Guidance on generating and interpreting summaries of HED annotations in datasets to facilitate data analysis.",
        ),
        # === ON-DEMAND: Integration (1 doc) ===
        DocPage(
            title="HED and EEGLAB",
            url="https://www.hedtags.org/hed-matlab/",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-matlab/main/README.md",
            category="integration",
            description="Describes how to integrate HED annotations within the EEGLAB environment for EEG data analysis.",
        ),
        # === ON-DEMAND: Reference (2 docs) ===
        DocPage(
            title="Documentation summary",
            url="https://www.hedtags.org/hed-resources/DocumentationSummary.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/DocumentationSummary.md",
            category="reference",
            description="An overview of all HED documentation resources, providing quick access to various guides, specifications, and tools.",
        ),
        DocPage(
            title="HED test datasets",
            url="https://www.hedtags.org/hed-resources/HedTestDatasets.html",
            source_url="https://raw.githubusercontent.com/hed-standard/hed-resources/main/docs/source/HedTestDatasets.md",
            category="reference",
            description="A collection of datasets specifically designed for testing HED annotations and validation tools.",
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
