"""
Knowledge-graph generation job management — admin only.

Triggers the kggenerator Kubernetes Job (extract → resolve → load) on
demand, cloning the helm-deployed suspended template Job exactly like the
extractor trigger does. The Kubernetes plumbing is shared with
routers/extractor.py; only the template name and labels differ.
"""

import asyncio
import copy
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes import client as k8s
from kubernetes.client.exceptions import ApiException

from asapbackend.auth.dependencies import require_role
from asapbackend.config import settings
from asapbackend.routers.extractor import (
    ExtractorStatus,
    JobRun,
    TriggerResponse,
    _create_job,
    _get_batch_v1,
    _read_template_job,
    _to_job_run,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/kggenerator",
    tags=["kggenerator"],
    dependencies=[Depends(require_role("admin"))],
)

_TRIGGER_LABEL = "triggered-by=asapbackend,app=kggenerator"
_TRIGGER_LABELS = {"app": "kggenerator", "triggered-by": "asapbackend"}

_MANAGED_POD_LABELS = {
    "controller-uid",
    "batch.kubernetes.io/controller-uid",
    "job-name",
    "batch.kubernetes.io/job-name",
}


def _list_triggered_jobs(namespace: str):
    return _get_batch_v1().list_namespaced_job(
        namespace, label_selector=_TRIGGER_LABEL
    )


@router.post("/trigger", response_model=TriggerResponse,
             status_code=status.HTTP_201_CREATED)
async def trigger_kg_generation():
    """Create a new kggenerator Job. Returns 409 if a run is already active."""
    namespace = settings.k8s_namespace
    template_name = settings.kggenerator_job_name

    job_list = await asyncio.to_thread(_list_triggered_jobs, namespace)
    active = [j for j in job_list.items if (j.status.active or 0) > 0]
    if active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"KG generation is already running (job: {active[0].metadata.name}).",
        )

    try:
        template = await asyncio.to_thread(_read_template_job, namespace, template_name)
    except ApiException as exc:
        if exc.status == 404:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"KG generator template job '{template_name}' not found "
                       f"in namespace '{namespace}'.",
            )
        raise

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
    log.info("KG generator job created: %s", created.metadata.name)

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
        key=lambda j: j.metadata.creation_timestamp
        or datetime.min.replace(tzinfo=timezone.utc),
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
    """List all triggered KG generation runs, most recent first."""
    namespace = settings.k8s_namespace
    job_list = await asyncio.to_thread(_list_triggered_jobs, namespace)
    jobs = sorted(
        job_list.items,
        key=lambda j: j.metadata.creation_timestamp
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return [_to_job_run(j) for j in jobs]
