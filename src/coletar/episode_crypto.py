"""Encrypt raw episodes under disposable per-object keys.

The graph and event log contain ciphertext only. The Store keeps the data-encryption
key separately, so erasure destroys that key without deleting the object, its hashes,
or its provenance history.
"""

from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from coletar.schema.objects import ContextObject, ObjectType
from coletar.schema.tenancy import TenantId
from coletar.store.base import Store

PREFIX = "coletar:episode:aesgcm:v1:"


class EpisodeKeyUnavailable(Exception):
    """The episode key was shredded or never durably stored."""


def _aad(tenant_id: TenantId, object_id: str) -> bytes:
    return f"{tenant_id}\0{object_id}".encode()


def encrypt_episode(
    tenant_id: TenantId, object_id: str, plaintext: str
) -> tuple[str, bytes]:
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), _aad(tenant_id, object_id))
    encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode()
    return f"{PREFIX}{encoded}", key


async def decrypt_episode(
    store: Store, tenant_id: TenantId, episode: ContextObject
) -> str:
    if episode.type is not ObjectType.EPISODE or not episode.content.startswith(PREFIX):
        raise ValueError("object is not an encrypted episode")
    key = await store.get_object_key(tenant_id, episode.id)
    if key is None:
        raise EpisodeKeyUnavailable(episode.id)
    try:
        raw = base64.urlsafe_b64decode(episode.content.removeprefix(PREFIX))
        nonce, ciphertext = raw[:12], raw[12:]
        return AESGCM(key).decrypt(
            nonce, ciphertext, _aad(tenant_id, episode.id)
        ).decode()
    except (binascii.Error, InvalidTag, UnicodeDecodeError, ValueError) as exc:
        # Corrupt ciphertext is as unavailable as a shredded key. Callers must not
        # display bytes or acknowledge a pending episode they could not examine.
        raise EpisodeKeyUnavailable(episode.id) from exc
