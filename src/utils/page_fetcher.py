"""Page content fetching with SSRF protection.

This module provides secure page fetching functionality with protection
against Server-Side Request Forgery (SSRF) attacks. Used by assistants
to fetch page content for context (e.g., when widget is embedded on a page).
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx
from markdownify import markdownify

logger = logging.getLogger(__name__)

# Maximum characters to return from fetched page content
MAX_PAGE_CONTENT_LENGTH = 30000


def is_safe_url(url: str) -> tuple[bool, str, str | None]:
    """Validate URL is safe to fetch (prevents SSRF attacks).

    Checks that the URL:
    - Uses HTTP or HTTPS protocol
    - Has a valid hostname
    - Does not resolve to private, loopback, link-local, or reserved IPs

    Note: This validation happens at check-time; DNS could theoretically
    resolve to a different IP during the actual fetch (TOCTOU risk).
    For lab server deployments with trusted users, this risk is acceptable.

    Args:
        url: The URL to validate.

    Returns:
        Tuple of (is_safe, error_message, resolved_ip).
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        logger.warning("SSRF blocked: invalid scheme '%s' in URL: %s", parsed.scheme, url)
        return False, "Only HTTP/HTTPS protocols are allowed", None

    hostname = parsed.hostname
    if not hostname:
        logger.warning("SSRF blocked: empty hostname in URL: %s", url)
        return False, "Invalid hostname", None

    try:
        resolved_ip = socket.gethostbyname(hostname)
    except socket.gaierror as e:
        logger.warning("SSRF blocked: DNS resolution failed for %s: %s", hostname, e)
        return False, f"DNS resolution failed for {hostname}: {e}", None
    except socket.herror as e:
        logger.warning("SSRF blocked: host error for %s: %s", hostname, e)
        return False, f"Host error for {hostname}: {e}", None
    except TimeoutError:
        logger.warning("SSRF blocked: DNS timeout for %s", hostname)
        return False, f"DNS resolution timed out for {hostname}", None

    try:
        ip_obj = ipaddress.ip_address(resolved_ip)
    except ValueError as e:
        logger.warning("SSRF blocked: invalid IP address '%s': %s", resolved_ip, e)
        return False, f"Invalid IP address: {resolved_ip}", None

    if ip_obj.is_private:
        logger.warning("SSRF blocked: private IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to private IP ranges is not allowed: {resolved_ip}", None
    if ip_obj.is_loopback:
        logger.warning("SSRF blocked: loopback IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to loopback addresses is not allowed: {resolved_ip}", None
    if ip_obj.is_link_local:
        logger.warning("SSRF blocked: link-local IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to link-local addresses is not allowed: {resolved_ip}", None
    if ip_obj.is_reserved:
        logger.warning("SSRF blocked: reserved IP %s for host %s", resolved_ip, hostname)
        return False, f"Access to reserved IP ranges is not allowed: {resolved_ip}", None

    return True, "", resolved_ip


def fetch_page_content(url: str) -> str:
    """Fetch page content with SSRF protection.

    Fetches HTML content from a URL, converts to markdown, and returns
    a truncated version suitable for LLM context.

    Args:
        url: The URL to fetch content from.

    Returns:
        The page content in markdown format, or an error message.
    """
    if not url or not url.startswith(("http://", "https://")):
        logger.warning("Page fetch blocked: invalid URL format: %s", url)
        return f"Error: Invalid URL '{url}'. URL must start with http:// or https://"

    is_safe_result, error_msg, resolved_ip = is_safe_url(url)
    if not is_safe_result:
        return f"Error: {error_msg}"

    logger.info("Fetching page content from %s (resolved to %s)", url, resolved_ip)

    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(url)

            # Handle redirects manually with validation
            redirect_count = 0
            max_redirects = 3
            while response.is_redirect and redirect_count < max_redirects:
                redirect_url = response.headers.get("location")
                if not redirect_url:
                    logger.warning("Redirect response missing Location header from %s", url)
                    break

                if redirect_url.startswith("/"):
                    parsed = urlparse(url)
                    redirect_url = f"{parsed.scheme}://{parsed.netloc}{redirect_url}"

                redirect_safe, redirect_error, _ = is_safe_url(redirect_url)
                if not redirect_safe:
                    logger.warning(
                        "SSRF blocked: redirect from %s to unsafe URL %s: %s",
                        url,
                        redirect_url,
                        redirect_error,
                    )
                    return f"Error: Redirect to unsafe URL blocked: {redirect_error}"

                logger.info("Following redirect to %s", redirect_url)
                response = client.get(redirect_url)
                redirect_count += 1

            if response.is_redirect:
                logger.warning("Too many redirects (>%d) from %s", max_redirects, url)
                return f"Error: Too many redirects (exceeded {max_redirects})"

            response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            logger.warning("Non-HTML content type from %s: %s", url, content_type)
            return f"Error: URL returned non-HTML content: {content_type}"

        content = markdownify(response.text, heading_style="ATX", strip=["script", "style"])
        lines = [line.strip() for line in content.split("\n")]
        content = "\n".join(line for line in lines if line)

        if len(content) > MAX_PAGE_CONTENT_LENGTH:
            logger.info(
                "Content from %s truncated from %d to %d chars",
                url,
                len(content),
                MAX_PAGE_CONTENT_LENGTH,
            )
            content = content[:MAX_PAGE_CONTENT_LENGTH] + "\n\n... [content truncated]"

        return f"# Content from {url}\n\n{content}"

    except httpx.HTTPStatusError as e:
        logger.warning("HTTP error fetching %s: %d", url, e.response.status_code)
        return f"Error fetching {url}: HTTP {e.response.status_code}"
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url)
        return f"Error: Request timed out fetching {url}"
    except httpx.RequestError as e:
        logger.warning("Request error fetching %s: %s", url, e)
        return f"Error fetching {url}: {e}"
