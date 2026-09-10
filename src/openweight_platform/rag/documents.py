"""Load synthetic enterprise policies as LangChain documents."""

from pathlib import Path

from langchain_core.documents import Document


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_DIRECTORY = REPOSITORY_ROOT / "data/policies"
REQUIRED_METADATA_FIELDS = ("policy_id", "title", "domain")


def _parse_front_matter(path: Path, text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Policy is missing front matter: {path}")

    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise ValueError(f"Policy has unclosed front matter: {path}") from error

    metadata = {}

    for line in lines[1:closing_index]:
        key, separator, value = line.partition(":")

        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"Invalid front matter in {path}: {line!r}")

        metadata[key.strip()] = value.strip()

    missing_fields = [
        field
        for field in REQUIRED_METADATA_FIELDS
        if not metadata.get(field)
    ]

    if missing_fields:
        missing = ", ".join(missing_fields)
        raise ValueError(f"Policy {path} is missing metadata: {missing}")

    content = "\n".join(lines[closing_index + 1 :]).strip()

    if not content:
        raise ValueError(f"Policy has no content: {path}")

    return metadata, content


def _source_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def load_policy_documents(
    policy_directory: str | Path = DEFAULT_POLICY_DIRECTORY,
) -> list[Document]:
    """Load and validate all Markdown policies in a directory."""

    directory = Path(policy_directory)

    if not directory.is_dir():
        raise FileNotFoundError(f"Policy directory does not exist: {directory}")

    documents = []
    seen_policy_ids = set()

    for path in sorted(directory.glob("*.md")):
        metadata, content = _parse_front_matter(
            path,
            path.read_text(encoding="utf-8"),
        )
        policy_id = metadata["policy_id"]

        if policy_id in seen_policy_ids:
            raise ValueError(f"Duplicate policy_id {policy_id!r} in {path}")

        seen_policy_ids.add(policy_id)
        metadata["source_path"] = _source_path(path)
        documents.append(
            Document(
                page_content=content,
                metadata=metadata,
            )
        )

    return documents
