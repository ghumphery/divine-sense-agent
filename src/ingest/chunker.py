"""Text chunker — RecursiveCharacter splitter.

Two APIs coexist for backward compatibility with v0.1.x:
  - ``chunk_text(text)`` — module-level function, returns list[str]
  - ``TextChunker().chunk_document(text, file_path, metadata)`` —
    returns list[DocumentChunk] with metadata, mirrors milvus-mcp-agent
    so the new pipeline can share code paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import config


@dataclass
class DocumentChunk:
    """A chunk of text with metadata (mirrors milvus-mcp-agent's class)."""
    text: str
    metadata: dict = field(default_factory=dict)
    chunk_id: str | None = None
    embedding: list[float] | None = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "metadata": self.metadata,
            "chunk_id": self.chunk_id,
        }


class TextChunker:
    """Split documents using LangChain's RecursiveCharacterTextSplitter.

    Falls back to module-level ``config.ingest.chunk_size`` / ``chunk_overlap``
    so the existing env-driven knobs keep working.
    """

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ):
        self.chunk_size = chunk_size or config.ingest.chunk_size
        self.chunk_overlap = chunk_overlap or config.ingest.chunk_overlap
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ".", " ", ""],
        )

    def chunk_document(
        self,
        text: str,
        file_path: str,
        metadata: dict | None = None,
    ) -> list[DocumentChunk]:
        """Split text into chunks and attach per-chunk metadata."""
        base_meta = dict(metadata or {})
        base_meta["source"] = str(file_path)

        chunk_texts = self.splitter.split_text(text)
        chunks: list[DocumentChunk] = []
        for i, chunk_text in enumerate(chunk_texts):
            chunks.append(DocumentChunk(
                text=chunk_text,
                metadata={
                    **base_meta,
                    "chunk_index": i,
                    "total_chunks": len(chunk_texts),
                },
            ))
        return chunks

    def chunk_document_with_headings(
        self,
        text: str,
        file_path: str,
        metadata: dict | None = None,
    ) -> list[DocumentChunk]:
        """Heading-aware chunking (v0.9.113.0 — ROADMAP STAIR item).

        Splits the text into heading-delimited sections first; every section
        is recursively split independently and its chunks inherit
        ``metadata["heading_path"]`` (the accumulated ``"H1 > H2"`` stack).
        The preamble before the first heading gets no ``heading_path`` key.
        A document without ATX headings falls back to the plain
        ``chunk_document`` path byte-identical to the legacy contract.
        """
        from src.ingest.headings import heading_sections

        base_meta = dict(metadata or {})
        base_meta["source"] = str(file_path)

        sections = heading_sections(text)
        if (len(sections) == 1 and not sections[0]["path"]):
            return self.chunk_document(text, file_path, metadata)

        chunks: list[DocumentChunk] = []
        for sec in sections:
            if not sec["text"].strip():
                continue
            sec_texts = self.splitter.split_text(sec["text"])
            for chunk_text in sec_texts:
                meta = {**base_meta}
                if sec["path"]:
                    meta["heading_path"] = sec["path"]
                chunks.append(DocumentChunk(text=chunk_text, metadata=meta))
        total = len(chunks)
        for i, ch in enumerate(chunks):
            ch.metadata["chunk_index"] = i
            ch.metadata["total_chunks"] = total
        return chunks


def chunk_text(text: str) -> list[str]:
    """Backward-compatible module-level function.

    Returns list of chunk strings (no metadata). New code should use
    ``TextChunker().chunk_document(...)`` instead.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        length_function=len,
    )
    return splitter.split_text(text)