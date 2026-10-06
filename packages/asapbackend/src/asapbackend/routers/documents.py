"""
Document collection ingestion — available to all authenticated users.

POST /documents/collections
    Multipart upload (files + metadata). Stages the files on the shared
    upload volume, records a document_processing_job row, and spawns an
    asapdocworker Kubernetes Job (cloned from the helm-deployed suspended
    template, same pattern as the extractor trigger). One in-flight job per
    user, enforced by a partial unique index in Postgres.

GET /documents/jobs/current
    The calling user's most recent ingestion job with live progress —
    polled by the document-management UI.

The backend deliberately does NOT write to Neo4j (its MCP access is
read-only); the worker creates all document nodes. Metadata travels on the
job row.
"""

import asyncio
import copy
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import psycopg
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from kubernetes import client as k8s
from kubernetes.client.exceptions import ApiException
from pydantic import BaseModel

from asapbackend.auth.dependencies import CurrentUser
from asapbackend.services import conversation as conv_svc
from asapbackend.config import settings
from asapbackend.database import get_db
from asapbackend.routers.extractor import _get_batch_v1
from asapbackend.services import graph_docs

from typing import Annotated
from fastapi import Depends

log = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]

_WORKER_LABELS = {"app": "asapdocworker", "triggered-by": "asapbackend"}


# ── Models ────────────────────────────────────────────────────────────────────

class JobProgress(BaseModel):
    id: str
    status: str
    collection_title: str
    document_collection_id: str
    total_documents: int
    processed_documents: int
    total_chunks: int
    current_document: str | None
    error: str | None
    created_ts: datetime
    completed_ts: datetime | None


class UploadResponse(BaseModel):
    job_id: str
    document_collection_id: str
    staged_files: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_relative_path(filename: str) -> PurePosixPath:
    """Sanitise a client-supplied relative path (folder uploads carry the
    webkitRelativePath in the filename). Rejects traversal outright."""
    p = PurePosixPath(filename.replace("\\", "/"))
    parts = [part for part in p.parts if part not in ("", ".", "/")]
    if not parts or any(part == ".." for part in parts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file path in upload: {filename!r}",
        )
    return PurePosixPath(*parts)


def _spawn_worker_job(job_id: str):
    """Clone the suspended asapdocworker template Job, injecting JOB_ID."""
    namespace = settings.k8s_namespace
    api = _get_batch_v1()
    template = api.read_namespaced_job(
        name=settings.docworker_job_name, namespace=namespace
    )

    _MANAGED_POD_LABELS = {
        "controller-uid",
        "batch.kubernetes.io/controller-uid",
        "job-name",
        "batch.kubernetes.io/job-name",
    }
    pod_template = copy.deepcopy(template.spec.template)
    orig_labels = pod_template.metadata.labels or {}
    pod_template.metadata.labels = {
        k: v for k, v in orig_labels.items() if k not in _MANAGED_POD_LABELS
    }
    container = pod_template.spec.containers[0]
    container.env = (container.env or []) + [k8s.V1EnvVar(name="JOB_ID", value=job_id)]

    new_job = k8s.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=k8s.V1ObjectMeta(
            generate_name=f"{settings.docworker_job_name}-",
            namespace=namespace,
            labels=_WORKER_LABELS,
        ),
        spec=k8s.V1JobSpec(
            template=pod_template,
            backoff_limit=template.spec.backoff_limit,
            ttl_seconds_after_finished=template.spec.ttl_seconds_after_finished,
        ),
    )
    return api.create_namespaced_job(namespace=namespace, body=new_job)


_JOB_COLS = """
    id, status, collection_title, document_collection_id, total_documents,
    processed_documents, total_chunks, current_document, error,
    created_ts, completed_ts
"""


def _row_to_progress(row) -> JobProgress:
    # get_db connections use psycopg's dict_row factory — rows are dicts.
    d = dict(row)
    d["id"] = str(d["id"])
    d["document_collection_id"] = str(d["document_collection_id"])
    return JobProgress(**d)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/collections", response_model=UploadResponse,
             status_code=status.HTTP_201_CREATED)
async def upload_collection(
    user: CurrentUser,
    conn: DBConn,
    title: Annotated[str, Form(min_length=1, max_length=100)],
    description: Annotated[str, Form(min_length=1, max_length=1000)],
    visibility: Annotated[str, Form(pattern="^(private|shared)$")],
    source_location: Annotated[str, Form(max_length=512)] = "",
    files: Annotated[list[UploadFile], File()] = ...,
):
    if not files:
        raise HTTPException(status_code=400, detail="No files in upload.")

    job_id = str(uuid.uuid4())
    collection_id = str(uuid.uuid4())
    staging = Path(settings.upload_root) / job_id

    # 1. Claim the user's job slot FIRST — the partial unique index makes this
    #    atomic, so two concurrent uploads cannot both proceed.
    try:
        await conn.execute(
            """
            INSERT INTO document_processing_job
                (id, user_id, document_collection_id, status, collection_title,
                 collection_description, visibility, source_location)
            VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s)
            """,
            (job_id, user.id, collection_id, title, description,
             visibility, source_location or title),
        )
        await conn.commit()
    except psycopg.errors.UniqueViolation:
        await conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a document collection being processed. "
                   "Wait for it to finish before uploading another.",
        )

    async def _fail_job(detail: str, code: int):
        shutil.rmtree(staging, ignore_errors=True)
        await conn.execute(
            "UPDATE document_processing_job "
            "SET status = 'failed', error = %s, completed_ts = now() WHERE id = %s",
            (detail, job_id),
        )
        await conn.commit()
        raise HTTPException(status_code=code, detail=detail)

    # 2. Stage the files under /uploads/<job_id>/, preserving folder structure.
    staged = 0
    try:
        for f in files:
            rel = _safe_relative_path(f.filename or "unnamed")
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as out:
                while chunk := await f.read(1024 * 1024):
                    out.write(chunk)
            staged += 1
    except HTTPException as exc:
        await _fail_job(exc.detail, exc.status_code)
    except OSError as exc:
        log.exception("Staging failed")
        await _fail_job(f"Could not store the upload: {exc}", 500)

    # 3. Spawn the worker Job.
    try:
        created = await asyncio.to_thread(_spawn_worker_job, job_id)
        log.info("Docworker job created: %s (job row %s)", created.metadata.name, job_id)
    except ApiException as exc:
        log.exception("Worker job spawn failed")
        detail = (
            f"Worker template job '{settings.docworker_job_name}' not found."
            if exc.status == 404
            else "Could not start the document processing job."
        )
        await _fail_job(detail, status.HTTP_503_SERVICE_UNAVAILABLE)

    return UploadResponse(
        job_id=job_id, document_collection_id=collection_id, staged_files=staged
    )


# ── Dead-worker reconciliation ────────────────────────────────────────────────
# The worker writes its own 'failed' status on any exception it can catch, but
# a container the kernel kills (OOMKilled, eviction, node restart) never gets
# the chance, leaving the job row 'running' forever and the collection stuck
# at 'processing'. The Kubernetes Job object is the source of truth for
# whether the worker is still alive, so every progress poll checks it.

_NOT_FOUND_GRACE_S = 60  # job creation / listing lag before "missing" means dead


def _worker_job_verdict(job_id: str, created_ts: datetime) -> str | None:
    """Return an error message if the worker for `job_id` is dead without
    having reported, else None (still running, or too early to tell)."""
    namespace = settings.k8s_namespace
    batch = _get_batch_v1()
    selector = ",".join(f"{k}={v}" for k, v in _WORKER_LABELS.items())
    jobs = batch.list_namespaced_job(namespace=namespace, label_selector=selector).items

    def _job_id_of(job) -> str | None:
        for c in job.spec.template.spec.containers:
            for env in c.env or []:
                if env.name == "JOB_ID":
                    return env.value
        return None

    job = next((j for j in jobs if _job_id_of(j) == job_id), None)
    if job is None:
        age = (datetime.now(timezone.utc) - created_ts).total_seconds()
        if age < _NOT_FOUND_GRACE_S:
            return None
        return ("The worker job no longer exists in the cluster; it was removed "
                "before reporting a result.")

    st = job.status
    if (st.active or 0) > 0:
        return None
    if not ((st.failed or 0) > 0 or (st.succeeded or 0) > 0):
        return None  # created but not yet scheduled

    # Pull the container's termination reason for a useful message.
    reason, code, mem_limit = None, None, None
    try:
        core = k8s.CoreV1Api(api_client=batch.api_client)
        pods = core.list_namespaced_pod(
            namespace=namespace, label_selector=f"job-name={job.metadata.name}",
        ).items
        if pods:
            pod = max(pods, key=lambda p: p.metadata.creation_timestamp)
            cs = (pod.status.container_statuses or [None])[0]
            term = cs.state.terminated if cs and cs.state else None
            if term:
                reason, code = term.reason, term.exit_code
            res = pod.spec.containers[0].resources
            mem_limit = (res.limits or {}).get("memory") if res else None
    except Exception:  # noqa: BLE001 — diagnostics only
        log.exception("Could not read worker pod status for job %s", job_id)

    if (st.succeeded or 0) > 0:
        return "The worker exited without reporting completion."
    msg = f"The worker was terminated ({reason or 'unknown reason'}"
    if code is not None:
        msg += f", exit code {code}"
    msg += ")."
    if reason == "OOMKilled":
        msg += (f" It ran out of memory (limit {mem_limit or 'unknown'}); the upload "
                "is too large to process in one pass. Try splitting large "
                "documents or spreadsheets into smaller files.")
    return msg


async def _mark_job_failed(conn, job_id: str, error: str) -> None:
    """Apply the worker's own failure protocol on its behalf: fail the job
    row, tombstone the collection in Neo4j, and discard the staged upload."""
    cur = await conn.execute(
        """
        UPDATE document_processing_job
        SET status = 'failed', error = %s, current_document = NULL, completed_ts = now()
        WHERE id = %s AND status IN ('pending', 'running')
        RETURNING document_collection_id, collection_title, collection_description,
                  source_location, visibility, user_id
        """,
        (error, job_id),
    )
    job = await cur.fetchone()
    if not job:
        return  # someone else (the worker, a concurrent poll) got there first
    log.warning("Document job %s marked failed by reconciliation: %s", job_id, error)
    job = dict(job)
    job["document_collection_id"] = str(job["document_collection_id"])
    try:
        await asyncio.to_thread(graph_docs.mark_collection_failed, job)
    except Exception:  # noqa: BLE001
        log.exception("Could not tombstone collection for dead job %s", job_id)
    shutil.rmtree(Path(settings.upload_root) / job_id, ignore_errors=True)


@router.get("/jobs/current", response_model=JobProgress | None)
async def current_job(user: CurrentUser, conn: DBConn):
    """The calling user's most recent ingestion job (any status), or null.
    A job still marked running whose worker has died is failed here."""
    query = f"""
        SELECT {_JOB_COLS} FROM document_processing_job
        WHERE user_id = %s ORDER BY created_ts DESC LIMIT 1
    """
    cur = await conn.execute(query, (user.id,))
    row = await cur.fetchone()
    if row and row["status"] in ("pending", "running"):
        try:
            verdict = await asyncio.to_thread(
                _worker_job_verdict, str(row["id"]), row["created_ts"],
            )
        except Exception:  # noqa: BLE001 — never let a K8s hiccup break polling
            log.exception("Worker job reconciliation failed")
            verdict = None
        if verdict:
            await _mark_job_failed(conn, str(row["id"]), verdict)
            cur = await conn.execute(query, (user.id,))
            row = await cur.fetchone()
    return _row_to_progress(row) if row else None


# ═════════════════════════════════════════════════════════════════════════════
# Library management (Phase 3)
# ═════════════════════════════════════════════════════════════════════════════



class CollectionInfo(BaseModel):
    id: str
    title: str
    description: str | None = None
    visibility: str
    archived: bool
    status: str
    uploaded_by: str
    owned_by_me: bool = False
    source_location: str | None = None
    created_at: str | None = None
    documents: int = 0
    chunks: int = 0


class ArchiveRequest(BaseModel):
    archived: bool


def _require_manage_rights(user, collection: dict) -> None:
    """Archive/hard-delete: owner or admin. Admins cannot manage other
    users' PRIVATE collections (they cannot even see them)."""
    if collection["uploaded_by"] == str(user.id):
        return
    if user.has_role("admin") and collection["visibility"] == "shared":
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the collection's owner (or an admin, for shared collections) can do this.",
    )


def _visible_to(user, collection: dict) -> bool:
    return (
        collection["visibility"] == "shared"
        or collection["uploaded_by"] == str(user.id)
    )


@router.get("/collections", response_model=list[CollectionInfo])
async def list_collections(user: CurrentUser):
    rows = await asyncio.to_thread(graph_docs.list_visible_collections, str(user.id))
    return [
        CollectionInfo(**r, owned_by_me=(r["uploaded_by"] == str(user.id)))
        for r in rows
    ]


@router.patch("/collections/{collection_id}", response_model=CollectionInfo)
async def set_collection_archived(
    collection_id: uuid.UUID, body: ArchiveRequest, user: CurrentUser, conn: DBConn
):
    coll = await asyncio.to_thread(graph_docs.get_collection, str(collection_id))
    if coll is None or not _visible_to(user, coll):
        raise HTTPException(status_code=404, detail="Collection not found.")
    _require_manage_rights(user, coll)
    await asyncio.to_thread(graph_docs.set_archived, str(collection_id), body.archived)
    if body.archived:
        # Archiving disassociates the collection everywhere: scope references
        # of EVERY user are removed, at both account and conversation level.
        # Unarchiving does NOT restore them — users re-attach deliberately.
        # (Conversation HISTORY is untouched: retrieved-chunk references live
        # in conversation_message_document_chunk and stay for rehydration.)
        cid = str(collection_id)
        await conn.execute(
            "DELETE FROM user_document_collection WHERE document_collection_id = %s", (cid,)
        )
        await conn.execute(
            "DELETE FROM conversation_document_collection WHERE document_collection_id = %s", (cid,)
        )
        await conn.commit()
    coll["archived"] = body.archived
    return CollectionInfo(
        **{k: coll.get(k) for k in ("id", "title", "visibility", "archived", "status", "uploaded_by")},
        owned_by_me=(coll["uploaded_by"] == str(user.id)),
    )


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(collection_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    """Hard delete. Succeeds only if no conversation turn ever retrieved the
    collection's chunks; scope references (any user) are removed automatically."""
    cid = str(collection_id)
    coll = await asyncio.to_thread(graph_docs.get_collection, cid)
    if coll is None or not _visible_to(user, coll):
        raise HTTPException(status_code=404, detail="Collection not found.")
    _require_manage_rights(user, coll)

    chunk_ids = await asyncio.to_thread(graph_docs.collection_chunk_ids, cid)
    if chunk_ids:
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM conversation_message_document_chunk "
            "WHERE document_chunk_id = ANY(%s::uuid[])) AS used",
            (chunk_ids,),
        )
        used = (await cur.fetchone())["used"]
        if used:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This collection has been used in conversations and cannot be "
                       "deleted — doing so would break those conversations' history. "
                       "Archive it instead to hide it from future use.",
            )

    # Auto-remove scope references (any user's), then the graph subtree.
    await conn.execute(
        "DELETE FROM user_document_collection WHERE document_collection_id = %s", (cid,)
    )
    await conn.execute(
        "DELETE FROM conversation_document_collection WHERE document_collection_id = %s", (cid,)
    )
    await conn.commit()
    await asyncio.to_thread(graph_docs.delete_collection_tree, cid)


# ═════════════════════════════════════════════════════════════════════════════
# Scopes: user-level and conversation-level enablement
# ═════════════════════════════════════════════════════════════════════════════

class ScopedCollection(BaseModel):
    id: str          # collection id
    title: str
    visibility: str
    scope: str       # 'user' | 'conversation'


async def _validate_enableable(user, collection_id: str) -> dict:
    coll = await asyncio.to_thread(graph_docs.get_collection, collection_id)
    if coll is None or not _visible_to(user, coll):
        raise HTTPException(status_code=404, detail="Collection not found.")
    if coll["archived"]:
        raise HTTPException(status_code=409, detail="This collection is archived.")
    if coll["status"] != "ready":
        raise HTTPException(status_code=409, detail="This collection is not ready yet.")
    return coll


async def _assert_own_conversation(conn, user, conversation_id: str) -> None:
    if not await conv_svc.owns_conversation(conn, conversation_id, user.id):
        raise HTTPException(status_code=404, detail="Conversation not found.")


@router.get("/scopes/user", response_model=list[ScopedCollection])
async def list_user_scope(user: CurrentUser, conn: DBConn):
    cur = await conn.execute(
        "SELECT document_collection_id FROM user_document_collection WHERE user_id = %s "
        "ORDER BY created_ts",
        (user.id,),
    )
    ids = [str(r["document_collection_id"]) for r in await cur.fetchall()]
    colls = {c["id"]: c for c in await asyncio.to_thread(graph_docs.get_collections_by_ids, ids)}
    # Stale-row shield: never surface archived or non-ready collections.
    return [
        ScopedCollection(id=i, title=colls[i]["title"],
                         visibility=colls[i]["visibility"], scope="user")
        for i in ids
        if i in colls and not colls[i]["archived"] and colls[i]["status"] == "ready"
    ]


@router.put("/scopes/user/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def enable_user_scope(collection_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    await _validate_enableable(user, str(collection_id))
    await conn.execute(
        "INSERT INTO user_document_collection (user_id, document_collection_id) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user.id, str(collection_id)),
    )
    await conn.commit()


@router.delete("/scopes/user/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_user_scope(collection_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    await conn.execute(
        "DELETE FROM user_document_collection WHERE user_id = %s AND document_collection_id = %s",
        (user.id, str(collection_id)),
    )
    await conn.commit()


@router.get("/scopes/conversation/{conversation_id}", response_model=list[ScopedCollection])
async def list_effective_scope(conversation_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    """Everything in effect for this conversation: user-scope + conversation-
    scope, each entry labelled with its origin (requirement: the UI must make
    the scopes distinguishable)."""
    await _assert_own_conversation(conn, user, str(conversation_id))
    cur = await conn.execute(
        "SELECT document_collection_id FROM user_document_collection WHERE user_id = %s",
        (user.id,),
    )
    user_ids = [str(r["document_collection_id"]) for r in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT document_collection_id FROM conversation_document_collection "
        "WHERE conversation_id = %s ORDER BY created_ts",
        (str(conversation_id),),
    )
    conv_ids = [str(r["document_collection_id"]) for r in await cur.fetchall()]

    all_ids = list(dict.fromkeys(user_ids + conv_ids))
    colls = {c["id"]: c for c in await asyncio.to_thread(graph_docs.get_collections_by_ids, all_ids)}

    def usable(i: str) -> bool:
        # Stale-row shield: never surface archived or non-ready collections.
        return i in colls and not colls[i]["archived"] and colls[i]["status"] == "ready"

    out: list[ScopedCollection] = []
    for i in user_ids:
        if usable(i):
            out.append(ScopedCollection(id=i, title=colls[i]["title"],
                                        visibility=colls[i]["visibility"], scope="user"))
    for i in conv_ids:
        if usable(i) and i not in user_ids:
            out.append(ScopedCollection(id=i, title=colls[i]["title"],
                                        visibility=colls[i]["visibility"], scope="conversation"))
    return out


@router.put("/scopes/conversation/{conversation_id}/{collection_id}",
            status_code=status.HTTP_204_NO_CONTENT)
async def enable_conversation_scope(
    conversation_id: uuid.UUID, collection_id: uuid.UUID, user: CurrentUser, conn: DBConn
):
    await _assert_own_conversation(conn, user, str(conversation_id))
    await _validate_enableable(user, str(collection_id))
    await conn.execute(
        "INSERT INTO conversation_document_collection (conversation_id, document_collection_id) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (str(conversation_id), str(collection_id)),
    )
    await conn.commit()


@router.delete("/scopes/conversation/{conversation_id}/{collection_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def disable_conversation_scope(
    conversation_id: uuid.UUID, collection_id: uuid.UUID, user: CurrentUser, conn: DBConn
):
    await _assert_own_conversation(conn, user, str(conversation_id))
    await conn.execute(
        "DELETE FROM conversation_document_collection "
        "WHERE conversation_id = %s AND document_collection_id = %s",
        (str(conversation_id), str(collection_id)),
    )
    await conn.commit()
