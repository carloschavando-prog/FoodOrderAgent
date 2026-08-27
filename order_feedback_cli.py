"""Operator commands for pricing-feedback previews and controlled delivery."""

import argparse
import json
import pathlib
import sys

from order_feedback import (
    FeedbackConfig,
    FeedbackError,
    FeedbackService,
    Representative,
    SUPPLIERS,
    SupabaseFeedbackStore,
    qualify_supplier_items,
    render_feedback_email,
)


ROOT = pathlib.Path(__file__).resolve().parent
DEFAULT_FIXTURE = ROOT / "fixtures" / "order_feedback_sample.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "order_feedback_previews"


def _supplier_ids(value):
    if value == "all":
        return sorted(SUPPLIERS)
    for supplier_id, supplier in SUPPLIERS.items():
        if value in {str(supplier_id), supplier["slug"]}:
            return [supplier_id]
    raise FeedbackError(f"Unknown supplier: {value}")


def _fixture_previews(path):
    fixture = json.loads(path.read_text(encoding="utf-8"))
    contact = fixture["purchasing_contact"]
    representatives = {
        int(row["supplier_id"]): Representative(
            supplier_id=int(row["supplier_id"]),
            company=SUPPLIERS[int(row["supplier_id"])]["company"],
            name=row["name"],
            email=row["email"],
        )
        for row in fixture["representatives"]
    }
    results = []
    for supplier_id in sorted(SUPPLIERS):
        items, omissions = qualify_supplier_items(fixture["decisions"], supplier_id)
        if not items:
            results.append(
                {
                    "supplier_id": supplier_id,
                    "status": "skipped",
                    "intended_recipient": representatives[supplier_id].email,
                    "item_total": 0,
                    "case_total": 0,
                    "omission_summary": omissions,
                    "subject": "",
                    "html": "",
                    "text": "",
                }
            )
            continue
        rendered = render_feedback_email(
            representatives[supplier_id],
            fixture["order_date"],
            items,
            contact["name"],
            contact["business_name"],
            contact.get("contact_detail", ""),
        )
        results.append(
            {
                **rendered,
                "supplier_id": supplier_id,
                "status": "dry-run",
                "intended_recipient": representatives[supplier_id].email,
                "item_total": rendered["item_count"],
                "case_total": rendered["case_count"],
                "omission_summary": omissions,
            }
        )
    return fixture["order_id"], results


def _save_previews(order_id, results, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    index = {"order_id": order_id, "delivery_mode": "dry-run", "suppliers": []}
    for result in results:
        supplier = SUPPLIERS[int(result["supplier_id"])]
        row = {
            "supplier_id": result["supplier_id"],
            "supplier": supplier["company"],
            "status": result["status"],
            "intended_recipient": result["intended_recipient"],
            "subject": result.get("subject", ""),
            "item_count": result["item_total"],
            "case_count": result["case_total"],
            "omission_summary": result.get("omission_summary", {}),
        }
        if result["status"] != "skipped":
            html_path = output_dir / f"{supplier['slug']}.html"
            text_path = output_dir / f"{supplier['slug']}.txt"
            html_path.write_text(result["html"], encoding="utf-8")
            text_path.write_text(result["text"], encoding="utf-8")
            row["html_preview"] = str(html_path)
            row["text_preview"] = str(text_path)
        index["suppliers"].append(row)
        print(
            f"{supplier['company']}: {row['status']} | to={row['intended_recipient']} "
            f"| subject={row['subject'] or '(none)'} | items={row['item_count']} "
            f"| cases={row['case_count']}"
        )
    index_path = output_dir / "index.json"
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"Preview index: {index_path}")


def command_preview(args):
    service = FeedbackService(
        SupabaseFeedbackStore.from_env(), FeedbackConfig.from_env()
    )
    results = service.prepare_order(args.order_id)
    order_id = args.order_id
    _save_previews(order_id, results, pathlib.Path(args.output_dir))


def command_preview_fixture(args):
    order_id, results = _fixture_previews(pathlib.Path(args.fixture))
    _save_previews(order_id, results, pathlib.Path(args.output_dir))


def command_show_order(args):
    store = SupabaseFeedbackStore.from_env()
    order = store.get_latest_order() if args.latest else store.get_order(args.order_id)
    if not order:
        raise FeedbackError("No saved order matched the request")
    lines = store.get_order_lines(order["id"])
    payload = {
        "order_id": order["id"],
        "order_date": order["order_date"],
        "status": order["status"],
        "item_total": order.get("item_total", 0),
        "case_total": order.get("case_total", 0),
        "lines": lines,
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return
    print(
        f"Order {payload['order_id']} | {payload['order_date']} | "
        f"{payload['status']} | {payload['item_total']} items | "
        f"{payload['case_total']} cases"
    )
    grouped = {}
    for line in lines:
        grouped.setdefault(int(line["supplier_id"]), []).append(line)
    for supplier_id in sorted(grouped):
        supplier = SUPPLIERS.get(supplier_id, {"company": str(supplier_id)})
        supplier_lines = grouped[supplier_id]
        cases = sum(float(line.get("cases_ordered") or 0) for line in supplier_lines)
        print(f"\n{supplier['company']}: {len(supplier_lines)} items, {cases:g} cases")
        for line in supplier_lines:
            print(
                f"  - {line['item_name']} | {line.get('description') or '—'} | "
                f"item {line.get('supplier_item_number') or '—'} | "
                f"{float(line.get('cases_ordered') or 0):g} cases"
            )


def command_send(args, action):
    if action == "live-send" and not args.confirm_live:
        raise FeedbackError("Live sending also requires --confirm-live")
    service = FeedbackService(
        SupabaseFeedbackStore.from_env(), FeedbackConfig.from_env()
    )
    for supplier_id in _supplier_ids(args.supplier):
        result = service.send(args.order_id, supplier_id, action)
        print(
            f"{SUPPLIERS[supplier_id]['company']}: {result['status']} "
            f"to {result.get('delivered_to', result['intended_recipient'])}"
        )


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview", help="Render without contacting Resend")
    preview.add_argument("--order-id", required=True, help="Saved finalized order UUID")
    preview.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    preview.set_defaults(func=command_preview)

    fixture = commands.add_parser(
        "preview-fixture", help="Explicitly render synthetic sample data"
    )
    fixture.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    fixture.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    fixture.set_defaults(func=command_preview_fixture)

    show = commands.add_parser(
        "show-order", help="Display the exact persisted vendor order lines"
    )
    target = show.add_mutually_exclusive_group(required=True)
    target.add_argument("--order-id", help="Saved order UUID")
    target.add_argument("--latest", action="store_true", help="Most recently saved order")
    show.add_argument("--json", action="store_true", help="Emit structured JSON")
    show.set_defaults(func=command_show_order)

    test = commands.add_parser(
        "send-test", help="Send only to ORDER_FEEDBACK_TEST_RECIPIENT"
    )
    test.add_argument("--order-id", required=True)
    test.add_argument("--supplier", default="all")
    test.set_defaults(func=lambda args: command_send(args, "test-send"))

    live = commands.add_parser("send-live", help="Explicit live representative send")
    live.add_argument("--order-id", required=True)
    live.add_argument("--supplier", default="all")
    live.add_argument("--confirm-live", action="store_true")
    live.set_defaults(func=lambda args: command_send(args, "live-send"))
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        args.func(args)
        return 0
    except (FeedbackError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
