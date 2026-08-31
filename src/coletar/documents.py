"""Source documents as provenance (ROADMAP M9).

*"Prove today's answer derives from today's policy"* should be a link, not an
argument. A fact extracted from a policy document is only as defensible as the
document behind it, and an auditor's next question after "what did we believe" is
always "says who".

**No new table.** §2 is explicit that a property applying to one workflow does not
earn a column, and this needs neither: a source document *is* an `ARTIFACT`, and
`Provenance.source_object_ids` already links a fact to what it came from. What was
missing was the act of attaching one, not a place to put it.

**Deliberately narrow.** Documents that are the source of a fact — a policy, a
contract, a spec. Not a multimodal vault: photos, voice notes and OCR are a different
product with a different ingestion pipeline and different quality bars, and building
them here would make coletar worse at the thing it is uniquely good at.

**Stored by content hash**, so re-attaching the same policy after re-downloading it
is recognised as the same document rather than kept twice under a new name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

#: Read natively. Anything else is refused with a pointer rather than mangled: a
#: parser that returns a PDF's raw bytes as "content" produces a fact whose source
#: is unreadable, which is worse than no source at all.
TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml"})

#: A policy that would fill a context window is a policy nobody will read in the
#: Inspector, and the Inspector is where these get approved.
MAX_DOCUMENT_CHARS = 200_000


class DocumentError(Exception):
    """A refusal phrased for the person who chose the file."""


@dataclass(frozen=True)
class AttachedDocument:
    object_id: str
    filename: str
    digest: str
    chars: int
    already_held: bool


def _read(path: Path) -> str:
    if not path.exists():
        raise DocumentError(f"{path} does not exist")
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise DocumentError(
            f"{path.name} is not a text format this reads natively "
            f"({', '.join(sorted(TEXT_SUFFIXES))}). Convert it first — a PDF parser "
            "is a dependency worth adding deliberately rather than silently, and a "
            "fact whose source is unreadable bytes is worse than one with no source."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentError(f"{path.name} is not UTF-8 text: {exc}") from exc
    if not text.strip():
        raise DocumentError(f"{path.name} is empty")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise DocumentError(
            f"{path.name} is {len(text)} characters, over the {MAX_DOCUMENT_CHARS} "
            "limit — split it, or attach the section a fact actually rests on"
        )
    return text


async def attach_document(
    store: Store,
    tenant_id: TenantId,
    path: Path,
    *,
    scope: Any = None,
    valid_from: Any = None,
    valid_until: Any = None,
) -> AttachedDocument:
    """Store a document as an `ARTIFACT` a fact can point at.

    `valid_from`/`valid_until` belong here as much as on a fact: a policy document is
    itself in force for a period, and an audit asking "which version of the handbook
    applied in March" is asking about the document, not only about what was extracted
    from it.
    """
    from coletar.schema.events import Actor, Event, EventType
    from coletar.schema.objects import (
        GLOBAL_SCOPE,
        ContextObject,
        ExtractionMethod,
        ObjectType,
        OriginType,
        Provenance,
        Provider,
    )

    text = _read(path)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # Same document, same id: re-attaching after a re-download is not a second source.
    existing = [
        obj
        for obj in await store.list_objects(
            tenant_id, type=ObjectType.ARTIFACT, include_retired=True, limit=10_000
        )
        if obj.payload.get("digest") == digest
    ]
    if existing:
        return AttachedDocument(
            object_id=existing[0].id,
            filename=path.name,
            digest=digest,
            chars=len(text),
            already_held=True,
        )

    document = ContextObject(
        type=ObjectType.ARTIFACT,
        content=text,
        scope=GLOBAL_SCOPE if scope is None else scope,
        # The user chose this file and said it is the source. That is a statement,
        # not an inference (§3.1).
        confidence=1.0,
        extraction_method=ExtractionMethod.EXPLICIT_STATEMENT,
        provenance=Provenance(
            origin_type=OriginType.USER, provider=Provider.COLETAR, confidence=1.0
        ),
        valid_from=valid_from,
        valid_until=valid_until,
        payload={"filename": path.name, "digest": digest, "bytes": path.stat().st_size},
    )
    await store.put_object(
        tenant_id,
        document,
        event=Event(
            type=EventType.OBJECT_CREATED,
            object_id=document.id,
            actor=Actor.USER,
            detail={"source_document": path.name, "digest": digest[:16]},
        ),
    )
    return AttachedDocument(
        object_id=document.id,
        filename=path.name,
        digest=digest,
        chars=len(text),
        already_held=False,
    )


async def cite(
    store: Store, tenant_id: TenantId, object_id: str, document_id: str
) -> None:
    """Record that a fact came from a document.

    Appends rather than replaces: a fact can rest on more than one source, and
    overwriting would quietly discard the first thing that justified it.
    """
    from coletar.schema.events import Actor, Event, EventType

    obj = await store.get_object(tenant_id, object_id)
    if obj is None:
        raise DocumentError(f"no object {object_id!r} in this tenant")
    document = await store.get_object(tenant_id, document_id)
    if document is None:
        raise DocumentError(f"no document {document_id!r} in this tenant")

    if document_id in obj.provenance.source_object_ids:
        return
    obj.provenance.source_object_ids = [*obj.provenance.source_object_ids, document_id]
    await store.put_object(
        tenant_id,
        obj,
        event=Event(
            type=EventType.OBJECT_UPDATED,
            object_id=obj.id,
            actor=Actor.USER,
            detail={"cited": document_id},
        ),
    )


async def sources_for(
    store: Store, tenant_id: TenantId, object_id: str
) -> list[Any]:
    """The documents a fact rests on — the answer to "says who"."""
    obj = await store.get_object(tenant_id, object_id)
    if obj is None:
        return []
    found = []
    for source_id in obj.provenance.source_object_ids:
        document = await store.get_object(tenant_id, source_id)
        if document is not None and document.payload.get("digest"):
            found.append(document)
    return found
