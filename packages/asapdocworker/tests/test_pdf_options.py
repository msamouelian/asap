"""
The worker must never hand docling's OCR an unbounded detector input, and
must size its thread pool from the container quota, not the host.

Regression tests from the "Alumni Bulletin - 1925" OOM (2026-11-20): a
39 x 1508 px bitmap region was enlarged to ~736 x 28,000 px by RapidOCR's
default "short side >= 736" rule and took the worker from 1.4 to 7 GiB.
"""

from asapdocworker import config, processing


def test_detector_input_is_capped_on_the_longest_side():
    params = processing.rapidocr_params()
    assert params["Det.limit_type"] == "max"        # "min" is the enlarging rule
    assert params["Det.limit_side_len"] == config.OCR_DET_MAX_SIDE
    assert config.OCR_DET_MAX_SIDE <= 4000          # 4000 x 4000 is the outer bound


def test_pdf_options_name_the_ocr_engine_so_the_cap_is_not_discarded():
    from docling.datamodel.pipeline_options import RapidOcrOptions

    opts = processing.pdf_pipeline_options()
    # Docling's automatic OCR options drop rapidocr_params; only an explicit
    # RapidOcrOptions carries them through to the engine.
    assert isinstance(opts.ocr_options, RapidOcrOptions)
    assert opts.ocr_options.backend == "torch"
    assert opts.ocr_options.rapidocr_params == processing.rapidocr_params()
    assert opts.do_ocr is True


def test_cpu_quota_reads_the_cgroup_limit(tmp_path):
    f = tmp_path / "cpu.max"
    f.write_text("300000 100000\n")
    assert processing.cpu_quota(str(f)) == 3
    f.write_text("150000 100000\n")
    assert processing.cpu_quota(str(f)) == 2       # rounds up, never 0
    f.write_text("max 100000\n")
    assert processing.cpu_quota(str(f)) >= 1       # unlimited -> host count
    assert processing.cpu_quota(str(tmp_path / "missing")) >= 1


def test_threads_are_shared_between_conversion_workers(monkeypatch):
    monkeypatch.setattr(processing, "cpu_quota", lambda *a: 3)
    monkeypatch.setattr(processing, "_pool_size", None)
    monkeypatch.setattr(config, "CONVERT_WORKERS", 2)
    assert processing.threads_per_worker() == 1
    monkeypatch.setattr(config, "CONVERT_WORKERS", 1)
    assert processing.threads_per_worker() == 3
    monkeypatch.setattr(processing, "cpu_quota", lambda *a: 1)
    monkeypatch.setattr(config, "CONVERT_WORKERS", 4)
    assert processing.threads_per_worker() == 1    # never zero

    opts = processing.pdf_pipeline_options()
    assert opts.accelerator_options.num_threads == 1


def test_a_single_file_job_gets_the_whole_quota(monkeypatch):
    monkeypatch.setattr(processing, "cpu_quota", lambda *a: 3)
    monkeypatch.setattr(config, "CONVERT_WORKERS", 2)
    monkeypatch.setattr(processing, "_pool_size", None)
    processing.init_worker(1)                      # one file -> one process
    assert processing.threads_per_worker() == 3


def test_pipeline_keeps_only_a_few_pages_in_flight():
    opts = processing.pdf_pipeline_options()
    assert opts.queue_max_size == config.PIPELINE_QUEUE_PAGES <= 8
    assert opts.ocr_batch_size == opts.layout_batch_size == opts.table_batch_size == 1
