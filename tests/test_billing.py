from app.config import Settings
from app.models import ActionItem, Client, ClientStatus
from app.services import actions, billing


def _settings() -> Settings:
    return Settings()


def test_billing_sweep_flags_one_invoice_per_active_client(db_session):
    client = Client(company_name="Acme GC", status=ClientStatus.ACTIVE.value, monthly_fee=500)
    db_session.add(client)
    db_session.commit()

    stats = billing.run_billing_sweep(db_session, _settings())

    assert stats["invoices_flagged"] == 1
    items = db_session.query(ActionItem).filter(ActionItem.client_id == client.id, ActionItem.category == "billing").all()
    assert len(items) == 1
    assert "500" in items[0].description


def test_billing_sweep_does_not_duplicate_same_month(db_session):
    client = Client(company_name="Acme GC", status=ClientStatus.ACTIVE.value, monthly_fee=500)
    db_session.add(client)
    db_session.commit()

    billing.run_billing_sweep(db_session, _settings())
    stats_second = billing.run_billing_sweep(db_session, _settings())

    assert stats_second["invoices_flagged"] == 0
    assert db_session.query(ActionItem).filter(ActionItem.client_id == client.id).count() == 1


def test_billing_sweep_escalates_a_prior_months_unconfirmed_invoice(db_session):
    client = Client(company_name="Acme GC", status=ClientStatus.ACTIVE.value, monthly_fee=500)
    db_session.add(client)
    db_session.commit()

    # Simulate last month's invoice reminder that never got marked done.
    actions.create_action_item(
        db_session,
        title=f"Invoice due — {client.company_name} (2020-01)",
        category="billing",
        client_id=client.id,
        created_by="agent:equinox",
    )

    stats = billing.run_billing_sweep(db_session, _settings())

    assert stats["invoices_flagged"] == 1  # this month's fresh invoice
    assert stats["overdue_flagged"] == 1
    escalation = (
        db_session.query(ActionItem)
        .filter(ActionItem.client_id == client.id, ActionItem.title.ilike("Overdue%"))
        .first()
    )
    assert escalation is not None


def test_billing_sweep_skips_clients_with_no_fee(db_session):
    client = Client(company_name="Free Pilot", status=ClientStatus.ACTIVE.value, monthly_fee=0)
    db_session.add(client)
    db_session.commit()

    stats = billing.run_billing_sweep(db_session, _settings())

    assert stats["invoices_flagged"] == 0


def test_billing_sweep_does_not_escalate_a_settled_prior_invoice(db_session):
    client = Client(company_name="Acme GC", status=ClientStatus.ACTIVE.value, monthly_fee=500)
    db_session.add(client)
    db_session.commit()

    prior = actions.create_action_item(
        db_session,
        title=f"Invoice due — {client.company_name} (2020-01)",
        category="billing",
        client_id=client.id,
        created_by="agent:equinox",
    )
    actions.mark_done(db_session, prior.id)

    stats = billing.run_billing_sweep(db_session, _settings())

    assert stats["overdue_flagged"] == 0
