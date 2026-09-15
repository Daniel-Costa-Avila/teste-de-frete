from __future__ import annotations

import unittest

from app.web.server import JobStore, _batch_summary, _freight_view, _recent_batches_with_flags


def _job(status: str, **extra: object) -> dict[str, object]:
    job = {
        "id": "job-" + status.lower(),
        "batch_id": "batch-test",
        "url": "https://example.com/product",
        "cep": "01001-000",
        "status": status,
        "created_at": "2026-08-26T12:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
    }
    job.update(extra)
    return job


def _freight_job(status: str, **freight: object) -> dict[str, object]:
    return _job(status, result={"freight": freight})


class BatchSummaryTests(unittest.TestCase):
    def test_all_failed_is_error(self) -> None:
        summary = _batch_summary([_job("TIMEOUT"), _job("ERRO_NO_LINK"), _job("CEP_FIELD_NOT_FOUND")])

        self.assertEqual(summary["status"], "ERROR")
        self.assertEqual(summary["status_label"], "ERRO")
        self.assertEqual(summary["error_count"], 3)

    def test_at_least_one_success_is_partial(self) -> None:
        summary = _batch_summary([_job("SUCCESS"), _job("SUCCESS"), _job("ERROR")])

        self.assertEqual(summary["status"], "PARTIAL_SUCCESS")
        self.assertEqual(summary["status_label"], "PARCIAL")

    def test_page_without_freight_counts_as_partial_not_error(self) -> None:
        summary = _batch_summary([_job("SUCCESS"), _job("FREIGHT_NOT_RETURNED")])

        self.assertEqual(summary["status"], "PARTIAL_SUCCESS")
        self.assertEqual(summary["warning_count"], 1)
        self.assertEqual(summary["error_count"], 0)

    def test_all_successful_is_done(self) -> None:
        summary = _batch_summary([_job("SUCCESS"), _job("SUCCESS"), _job("SUCCESS")])

        self.assertEqual(summary["status"], "DONE")
        self.assertEqual(summary["status_label"], "CONCLUÍDO")

    def test_active_batch_remains_running(self) -> None:
        summary = _batch_summary([_job("SUCCESS"), _job("RUNNING"), _job("QUEUED")])

        self.assertEqual(summary["status"], "RUNNING")
        self.assertTrue(summary["running"])
        self.assertEqual(summary["pending"], 2)
        self.assertEqual(summary["done"], 1)

    def test_percent_counts_only_finished_jobs(self) -> None:
        summary = _batch_summary([_job("SUCCESS"), _job("ERROR"), _job("QUEUED"), _job("QUEUED")])

        self.assertEqual(summary["percent"], 50)

    def test_empty_batch_does_not_divide_by_zero(self) -> None:
        summary = _batch_summary([])

        self.assertEqual(summary["percent"], 0)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["free_share"], 0)


class FreightBreakdownTests(unittest.TestCase):
    def test_counts_free_paid_and_average(self) -> None:
        summary = _batch_summary(
            [
                _freight_job("SUCCESS", price=0.0, price_kind="FREE"),
                _freight_job("SUCCESS", price=100.0, price_kind="PAID", currency="BRL"),
                _freight_job("SUCCESS", price=200.0, price_kind="PAID", currency="BRL"),
            ]
        )

        self.assertEqual(summary["free_count"], 1)
        self.assertEqual(summary["paid_count"], 2)
        self.assertEqual(summary["paid_average_text"], "R$ 150,00")
        self.assertEqual(summary["free_share"], 33)

    def test_free_shipping_is_price_zero(self) -> None:
        view = _freight_view({"result": {"freight": {"price": 0.0, "price_kind": "FREE"}}})

        self.assertEqual(view["kind"], "FREE")
        self.assertEqual(view["text"], "Grátis")

    def test_unknown_price_falls_back_to_page_text(self) -> None:
        view = _freight_view({"result": {"freight": {"price": None, "price_text": "Consulte"}}})

        self.assertEqual(view["kind"], "UNKNOWN")
        self.assertEqual(view["text"], "Consulte")

    def test_job_without_result_has_no_freight(self) -> None:
        view = _freight_view(_job("QUEUED"))

        self.assertEqual(view["kind"], "NONE")
        self.assertEqual(view["text"], "—")


class RecentBatchesTests(unittest.TestCase):
    def test_real_job_store_counts_mixed_item_statuses(self) -> None:
        store = JobStore()
        for status in ["SUCCESS", "TIMEOUT", "ERROR"]:
            job_id = store.create(
                url="https://example.com/product",
                cep="01001-000",
                headless=True,
                use_remote=False,
                batch_id="mixed-batch",
            )
            store.update(job_id, status=status)

        result = _recent_batches_with_flags(store)[0]

        self.assertEqual(result["batch_id"], "mixed-batch")
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["status"], "PARTIAL_SUCCESS")
        self.assertEqual(result["total_jobs"], 3)

    def test_real_job_store_treats_different_failures_as_total_error(self) -> None:
        store = JobStore()
        for status in ["TIMEOUT", "ERRO_NO_LINK", "CEP_FIELD_NOT_FOUND"]:
            job_id = store.create(
                url="https://example.com/product",
                cep="01001-000",
                headless=True,
                use_remote=False,
                batch_id="failed-batch",
            )
            store.update(job_id, status=status)

        result = _recent_batches_with_flags(store)[0]

        self.assertEqual(result["success_count"], 0)
        self.assertEqual(result["status"], "ERROR")

    def test_batches_come_back_newest_first(self) -> None:
        store = JobStore()
        for batch_id, created_at in [("older", "2026-08-25T10:00:00+00:00"), ("newer", "2026-08-26T10:00:00+00:00")]:
            job_id = store.create(
                url="https://example.com/product",
                cep="01001-000",
                headless=True,
                use_remote=False,
                batch_id=batch_id,
            )
            store.update(job_id, status="SUCCESS", created_at=created_at)

        batches = _recent_batches_with_flags(store)

        self.assertEqual([b["batch_id"] for b in batches], ["newer", "older"])


if __name__ == "__main__":
    unittest.main()
