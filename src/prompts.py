"""Prompt builders for JSON-only local LLM extraction and interpretation."""

from __future__ import annotations

import json

from .models import ExtractedBill


JSON_ONLY_RULES = """
Return JSON only. Do not include Markdown, prose, code fences, comments, or calculations of final per-person payable amounts.
Use decimal strings for money. Use null when a value is not visible or unclear.
"""


def bill_text_extraction_prompt(text: str, default_currency: str) -> str:
    return f"""
You extract receipt or bill data into strict JSON.
{JSON_ONLY_RULES}

Required shape:
{{
  "currency": "{default_currency}",
  "items": [
    {{"name": "Item name", "quantity": "1", "unit_price": null, "total": "0.00"}}
  ],
  "subtotal": null,
  "tax": null,
  "service_charge": null,
  "discount": null,
  "total": null,
  "notes": []
}}

Input:
{text}
""".strip()


def bill_image_extraction_prompt(default_currency: str) -> str:
    return f"""
You are reading a receipt or bill image. Extract visible bill data into strict JSON.
{JSON_ONLY_RULES}

Required shape:
{{
  "currency": "{default_currency}",
  "merchant": null,
  "items": [
    {{"name": "Guinness", "quantity": "1", "unit_price": "14.00", "line_total": "14.00"}}
  ],
  "subtotal": null,
  "service_charge": {{"amount": null, "percentage": null}},
  "gst": {{"amount": null, "percentage": null}},
  "discount": null,
  "grand_total": null,
  "your_bill": null,
  "payable": null,
  "final_total": null,
  "notes": []
}}

Rules:
- Do not invent items or return placeholder items.
- If quantity and line total are visible but unit price is not, set unit_price to null.
- If "Not subject to GST" appears, set gst.amount to "0.00" and add that note.
- If "Payable", "Your bill", or "Amount paid" appears, use it as the final payable field.
- If "Your bill" and "Grand total" both appear, keep both. "Your bill" is the final payable amount.
- If "Grand total" matches item totals before charges, put it in grand_total or subtotal.
- If extraction is uncertain, include a short warning in notes instead of failing silently.
- For mobile order-summary screenshots, scan the white order panel carefully.
- Item rows are usually above Service charge, GST, Discount, Your bill, and Grand total rows.
- Item rows may have quantity badges such as "1x" and right-aligned prices.
- Smaller text below the item name may repeat the item price; do not confuse it with a separate item.
- Do not stop after the first item. Continue extracting visible item rows until charge rows begin.
""".strip()


def bill_image_repair_prompt(default_currency: str, current_json: str, reason: str, missing_amount: str) -> str:
    return f"""
You are repairing an incomplete receipt extraction from the same image.
{JSON_ONLY_RULES}

Previous extraction:
{current_json}

Validation issue:
- Reason: {reason}
- Missing item amount: {missing_amount}

Look carefully for missing item rows in the receipt image, especially between the first item and the charges section.
For mobile order-summary screenshots, focus on the white order summary panel and scan all rows above Service charge, GST, Discount, Your bill, and Grand total.

Return the corrected full receipt JSON only using currency "{default_currency}".
Do not invent items. Only include visible items.
""".strip()


def split_interpretation_prompt(bill: ExtractedBill, instructions: str) -> str:
    bill_json = json.dumps(bill.to_dict(), indent=2)
    return f"""
Convert the user's split instructions into a structured allocation plan.
{JSON_ONLY_RULES}

Important:
- Do not calculate final payable totals.
- Keep item names and financial fields aligned with the confirmed bill.
- Use split.type "equal" for equal sharing.
- Use split.type "quantity" when quantities are assigned to people.
- Use split.type "fixed" when one person owns the whole line item.
- GST, service charge, and discounts should normally use "proportional_by_subtotal".

Required shape:
{{
  "currency": "SGD",
  "people": ["Name"],
  "items": [
    {{
      "name": "Item name from confirmed bill",
      "quantity": "1",
      "total": "0.00",
      "split": {{"type": "equal", "people": ["Name"]}}
    }}
  ],
  "service_charge": null,
  "tax": null,
  "discount": null,
  "total": null,
  "charge_allocation": "proportional_by_subtotal",
  "rounding": "half_up_2dp",
  "notes": []
}}

Confirmed bill:
{bill_json}

User split instructions:
{instructions}
""".strip()
