from app.models import SourcingRequestStatus
from app.services import sourcing_requests


def test_create_request_stores_criteria(db_session):
    record = sourcing_requests.create_request(
        db_session, requested_by_user_id=None, criteria={"industry": "roofing", "locations": "Indiana"}
    )
    assert record.status == SourcingRequestStatus.REQUESTED.value
    assert record.criteria["industry"] == "roofing"


def test_build_prompt_includes_filled_criteria_and_omits_blank_ones():
    from app.models import SourcingRequest

    request = SourcingRequest(
        criteria={
            "industry": "general contractors",
            "job_titles": "Owner, GC",
            "locations": "Indiana",
            "company_size": "",
            "company_revenue": "",
            "keywords": "",
            "number_of_results": 50,
            "notes": "",
        }
    )
    prompt = sourcing_requests.build_prompt(request)
    assert "general contractors" in prompt
    assert "Owner, GC" in prompt
    assert "Indiana" in prompt
    assert "Number of results: 50" in prompt
    assert "company_revenue" not in prompt  # blank fields aren't rendered as literal keys
    assert "cost estimate" in prompt.lower()


def test_build_prompt_defaults_number_of_results_when_missing():
    from app.models import SourcingRequest

    request = SourcingRequest(criteria={})
    prompt = sourcing_requests.build_prompt(request)
    assert "Number of results: 30" in prompt


def test_mark_fulfilled_updates_status_and_clears_pending_csv(db_session):
    record = sourcing_requests.create_request(db_session, requested_by_user_id=None, criteria={})
    sourcing_requests.store_pending_csv(db_session, record.id, "a,b\n1,2\n")

    updated = sourcing_requests.mark_fulfilled(db_session, record.id, 5)

    assert updated.status == SourcingRequestStatus.FULFILLED.value
    assert updated.imported_lead_count == 5
    assert updated.pending_csv == ""
    assert updated.fulfilled_at is not None


def test_mark_fulfilled_accumulates_across_multiple_imports(db_session):
    record = sourcing_requests.create_request(db_session, requested_by_user_id=None, criteria={})
    sourcing_requests.mark_fulfilled(db_session, record.id, 3)
    updated = sourcing_requests.mark_fulfilled(db_session, record.id, 4)
    assert updated.imported_lead_count == 7
