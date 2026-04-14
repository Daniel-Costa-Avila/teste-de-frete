import argparse
import json
import sys
from app.config import Settings
from app.infra.driver_factory import build_driver
from app.infra.results_csv import append_result, ensure_results_csv
from app.services.freight_test_service import FreightTestService


def run():
    parser = argparse.ArgumentParser(description="Freight tester (Probel).")
    parser.add_argument("--url", required=False, help="Product URL")
    parser.add_argument("--cep", required=False, help="Destination CEP (e.g. 79800-002)")
    parser.add_argument(
        "--results-csv",
        required=False,
        default=None,
        help="Path to results CSV (default: artifacts/results.csv).",
    )
    parser.add_argument(
        "--no-results-csv",
        action="store_true",
        help="Do not write results CSV.",
    )
    parser.add_argument(
        "--template-results-csv",
        action="store_true",
        help="Create an empty results CSV template (header only) and exit.",
    )
    args = parser.parse_args()

    settings = Settings()
    results_csv_path = (args.results_csv or settings.results_csv_path) if not args.no_results_csv else None

    if args.template_results_csv:
        if not results_csv_path:
            print("CSV output is disabled (--no-results-csv).", file=sys.stderr)
            return 2
        ensure_results_csv(results_csv_path)
        print(results_csv_path)
        return 0

    url = args.url or "https://probel.com.br/colchao-casal-mola-ensacada-probel-excede-premium/p"
    cep = args.cep or "79800-002"

    driver = build_driver(settings)
    try:
        service = FreightTestService(driver, settings)
        result = service.execute(url=url, cep=cep)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if results_csv_path:
            append_result(results_csv_path, result)
        if result.status == "SUCCESS":
            return 0
        return 2
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(run())
