from decimal import Decimal
import json

from src.consensus import ConsensusEngine
from src.models import BillItem, ConsensusFailure, ConsensusSuccess, ExtractedBill


class FakeClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def generate_text(self, prompt):
        return self.outputs.pop(0)


PLAN = """
{
  "currency": "SGD",
  "people": ["Alex", "C"],
  "items": [
    {"name": "Pizza", "quantity": "1", "total": "24.00", "split": {"type": "equal", "people": ["Alex", "C"]}}
  ],
  "service_charge": "0",
  "tax": "0",
  "discount": "0",
  "charge_allocation": "proportional_by_subtotal",
  "rounding": "half_up_2dp"
}
"""


def bill():
    return ExtractedBill(
        currency="SGD",
        items=[BillItem(name="Pizza", quantity=Decimal("1"), total=Decimal("24.00"))],
        service_charge=Decimal("0"),
        tax=Decimal("0"),
        discount=Decimal("0"),
        total=Decimal("24.00"),
    )


def test_consensus_success_when_all_five_outputs_match(tmp_path):
    engine = ConsensusEngine(FakeClient([PLAN] * 5), runs=5, log_dir=tmp_path)

    outcome = engine.run(bill(), "split pizza between Alex and C")

    assert isinstance(outcome, ConsensusSuccess)
    assert outcome.result.people["Alex"]["final"] == Decimal("12.00")
    assert outcome.result.people["C"]["final"] == Decimal("12.00")
    assert outcome.result.validation_runs_requested == 5
    assert outcome.result.validation_runs_matched == 5

    log_file = next(tmp_path.glob("consensus_*.json"))
    payload = json.loads(log_file.read_text(encoding="utf-8"))
    assert payload["requested_runs"] == 5
    assert payload["valid_runs"] == 5
    assert len(payload["allocation_hashes"]) == 5
    assert len(payload["result_hashes"]) == 5
    assert payload["all_final_payable_amounts_matched"] is True


def test_consensus_failure_when_one_output_differs(tmp_path):
    different = """
{
  "currency": "SGD",
  "people": ["Alex", "C"],
  "items": [
    {"name": "Pizza", "quantity": "1", "total": "24.00", "split": {"type": "fixed", "person": "Alex"}}
  ],
  "service_charge": "0",
  "tax": "0",
  "discount": "0",
  "charge_allocation": "proportional_by_subtotal",
  "rounding": "half_up_2dp"
}
"""
    engine = ConsensusEngine(FakeClient([PLAN, PLAN, different, PLAN, PLAN]), runs=5, log_dir=tmp_path)

    outcome = engine.run(bill(), "split pizza between Alex and C")

    assert isinstance(outcome, ConsensusFailure)
    assert "did not agree" in outcome.reason


def test_invalid_json_from_llm_fails_consensus(tmp_path):
    engine = ConsensusEngine(FakeClient([PLAN, "not json", PLAN, PLAN, PLAN]), runs=5, log_dir=tmp_path)

    outcome = engine.run(bill(), "split pizza between Alex and C")

    assert isinstance(outcome, ConsensusFailure)
    assert "validation runs failed" in outcome.reason
