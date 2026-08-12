"""Search-document builder — must stay identical in Colab ETL and Lambda."""

from sarr.common.schemas import PackageRecord

MAX_DEPENDENCIES = 15
MAX_KEYWORDS = 20
MAX_CLASSIFIERS = 10


def build_search_document(package: PackageRecord) -> str:
    """Build the text that is embedded for a package.

    Order is intentional: identity and summary first so they dominate the vector.
    Numeric popularity signals (stars, forks) are intentionally excluded.
    """
    keywords = ", ".join(package.keywords[:MAX_KEYWORDS]) if package.keywords else ""
    deps = ", ".join(package.dependencies[:MAX_DEPENDENCIES]) if package.dependencies else ""
    classifiers = (
        ", ".join(package.classifiers[:MAX_CLASSIFIERS]) if package.classifiers else ""
    )

    parts = [f"Package: {package.name}"]
    if package.summary:
        parts.append(f"Summary: {package.summary.strip()}")
    if keywords:
        parts.append(f"Keywords: {keywords}")
    if deps:
        parts.append(f"Dependencies: {deps}")
    if classifiers:
        parts.append(f"Category: {classifiers}")
    return "\n".join(parts)
