"""
Extractor job management — admin only.

Triggers the asapextractor Kubernetes Job on demand. The helm-deployed Job
(spec.suspend=true) serves as a pod-spec template; each trigger creates a new
uniquely-named Job from that template so repeated runs are possible without
manual cleanup.

All Kubernetes API calls use the standard client configuration:
  - Inside a pod: in-cluster config (ServiceAccount token + CA bundle)
  - Locally: kubeconfig (~/.kube/config)
This is distribution-agnostic — works with kind, EKS, GKE, AKS, etc.

Kubernetes SDK calls are synchronous and are run in a thread pool via
asyncio.to_thread to avoid blocking FastAPI's async event loop.
"""

import asyncio
import copy
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes import client as k8s, config as k8s_config
from kubernetes.client.exceptions import ApiException
from pydantic import BaseModel

from asapbackend.auth.dependencies import require_role
from asapbackend.config import settings

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/extractor",
    tags=["extractor"],
    dependencies=[Depends(require_role("admin"))],
)

# Label applied to every job this backend creates — used to separate triggered
# runs from the helm-deployed template job in list/status queries.
_TRIGGER_LABEL = "triggered-by=asapbackend"
_TRIGGER_LABELS = {"app": "asapextractor", "triggered-by": "asapbackend"}

# ── Kubernetes client (singleton, lazy-initialised) ───────────────────────────

_batch_v1: k8s.BatchV1Api | None = None


def _get_batch_v1() -> k8s.BatchV1Api:
    global _batch_v1
    if _batch_v1 is None:
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        _batch_v1 = k8s.BatchV1Api()
    return _batch_v1


# ── Pydantic models ───────────────────────────────────────────────────────────

class JobRun(BaseModel):
    name: str
    started_at: datetime | None
    finished_at: datetime | None
    status: str          # "running" | "succeeded" | "failed" | "pending"
    duration_seconds: int | None


class ExtractorStatus(BaseModel):
    is_running: bool
    current_job: str | None      # name of the active job, if any
    last_run: JobRun | None


class TriggerResponse(BaseModel):
    job_name: str
    started_at: datetime


# ── Helpers ───────────────────────────────────────────────────────────────────

def _job_status(job) -> str:
    s = job.status
    if (s.active or 0) > 0:
        return "running"
    if (s.succeeded or 0) > 0:
        return "succeeded"
    if (s.failed or 0) > 0:
        return "failed"
    return "pending"


def _to_job_run(job) -> JobRun:
    started = job.metadata.creation_timestamp
    finished = job.status.completion_time
    duration = None
    if started and finished:
        duration = int((finished - started).total_seconds())
    return JobRun(
        name=job.metadata.name,
        started_at=started,
        finished_at=finished,
        status=_job_status(job),
        duration_seconds=duration,
    )


def _list_triggered_jobs(namespace: str):
    return _get_batch_v1().list_namespaced_job(
        namespace, label_selector=_TRIGGER_LABEL
    )


def _read_template_job(namespace: str, name: str):
    return _get_batch_v1().read_namespaced_job(name=name, namespace=namespace)


def _create_job(namespace: str, body):
    return _get_batch_v1().create_namespaced_job(namespace=namespace, body=body)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/trigger", response_model=TriggerResponse, status_code=status.HTTP_201_CREATED)
async def trigger_extraction():
    """
    Create a new asapextractor Job. Returns 409 if a run is already active.

    Reads the pod spec from the helm-deployed template Job (spec.suspend=true)
    so the trigger always uses the configuration that was deployed via helm,
    including the correct image tag, ConfigMap refs, and Secret mounts.
    """
    namespace = settings.k8s_namespace
    template_name = settings.extractor_job_name

    # Check for an already-running job.
    job_list = await asyncio.to_thread(_list_triggered_jobs, namespace)
    active = [j for j in job_list.items if (j.status.active or 0) > 0]
    if active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Extraction is already running (job: {active[0].metadata.name}).",
        )

    # Read the pod spec from the suspended template job.
    try:
        template = await asyncio.to_thread(_read_template_job, namespace, template_name)
    except ApiException as exc:
        if exc.status == 404:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Extractor template job '{template_name}' not found in namespace '{namespace}'.",
            )
        raise

    # Build a new Job from the template's pod spec.
    # generate_name lets Kubernetes append a unique suffix automatically.
    # We do NOT copy spec.selector — Kubernetes generates it to match the new Job.
    #
    # Strip the four managed labels that Kubernetes injected into the template's
    # pod spec when the template job was first created. They carry the template
    # job's UID and name, which would conflict with the new job's auto-generated
    # selector (causing a 422 Unprocessable Entity).
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

    new_job = k8s.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=k8s.V1ObjectMeta(
            generate_name=f"{template_name}-",
            namespace=namespace,
            labels=_TRIGGER_LABELS,
        ),
        spec=k8s.V1JobSpec(
            template=pod_template,
            backoff_limit=template.spec.backoff_limit,
            ttl_seconds_after_finished=template.spec.ttl_seconds_after_finished,
        ),
    )

    created = await asyncio.to_thread(_create_job, namespace, new_job)
    log.info("Extractor job created: %s", created.metadata.name)

    return TriggerResponse(
        job_name=created.metadata.name,
        started_at=created.metadata.creation_timestamp,
    )


@router.get("/status", response_model=ExtractorStatus)
async def get_status():
    """Return the current run state and the most recent completed run."""
    namespace = settings.k8s_namespace
    job_list = await asyncio.to_thread(_list_triggered_jobs, namespace)

    jobs = sorted(
        job_list.items,
        key=lambda j: j.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    running = next((j for j in jobs if (j.status.active or 0) > 0), None)
    last = jobs[0] if jobs else None

    return ExtractorStatus(
        is_running=running is not None,
        current_job=running.metadata.name if running else None,
        last_run=_to_job_run(last) if last else None,
    )


@router.get("/history", response_model=list[JobRun])
async def get_history():
    """List all triggered extraction runs, most recent first."""
    namespace = settings.k8s_namespace
    job_list = await asyncio.to_thread(_list_triggered_jobs, namespace)

    jobs = sorted(
        job_list.items,
        key=lambda j: j.metadata.creation_timestamp or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return [_to_job_run(j) for j in jobs]
