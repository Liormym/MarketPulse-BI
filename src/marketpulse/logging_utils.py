"""Structured stdout logging plus PipelineExecutionLog / DataQualityResults writers (spec §7/§9)."""
import logging
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .quality.checks import CheckResult


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


@contextmanager
def log_stage(conn: Connection, run_id: uuid.UUID, stage: str):
    """Wraps a pipeline stage: writes a Running row, then Success/Failed on exit.
    Never lets a stage's failure kill the whole run — caller decides whether to continue.
    """
    log = logging.getLogger(f"pipeline.{stage}")
    start = datetime.now(timezone.utc)
    conn.execute(
        text(
            """
            INSERT INTO "PipelineExecutionLog" ("RunID", "PipelineStage", "StartTime", "Status")
            VALUES (:run_id, :stage, :start, 'Running')
            """
        ),
        {"run_id": str(run_id), "stage": stage, "start": start},
    )
    conn.commit()

    result = {"records_processed": None, "error": None}
    try:
        log.info("stage started")
        yield result
        status = "Success"
        log.info("stage finished: %s records", result["records_processed"])
    except Exception as exc:
        status = "Failed"
        result["error"] = str(exc)
        log.exception("stage failed")
        raise
    finally:
        conn.execute(
            text(
                """
                UPDATE "PipelineExecutionLog"
                SET "EndTime" = :end, "Status" = :status,
                    "RecordsProcessed" = :records, "ErrorMessage" = :error
                WHERE "RunID" = :run_id AND "PipelineStage" = :stage
                """
            ),
            {
                "end": datetime.now(timezone.utc),
                "status": status,
                "records": result["records_processed"],
                "error": result["error"],
                "run_id": str(run_id),
                "stage": stage,
            },
        )
        conn.commit()


def write_dq_results(conn: Connection, run_id: uuid.UUID, results: list[CheckResult]) -> None:
    for r in results:
        conn.execute(
            text(
                """
                INSERT INTO "DataQualityResults"
                    ("CheckID", "RunID", "CheckType", "Passed", "FailedRecords", "Details")
                VALUES (:check_id, :run_id, :check_type, :passed, :failed_records, :details)
                """
            ),
            {
                "check_id": str(uuid.uuid4()),
                "run_id": str(run_id),
                "check_type": r.check_type,
                "passed": r.passed,
                "failed_records": r.failed_records,
                "details": r.details,
            },
        )
    conn.commit()
