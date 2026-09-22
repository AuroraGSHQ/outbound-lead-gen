from app.models import ActionItem, Client, ClientStatus
from app.services import onboarding


def test_create_onboarding_checklist_creates_five_items(db_session):
    client = Client(company_name="Acme GC", status=ClientStatus.ACTIVE.value)
    db_session.add(client)
    db_session.commit()

    items = onboarding.create_onboarding_checklist(db_session, client)

    assert len(items) == 5
    assert all(i.category == "onboarding" for i in items)
    assert all(i.client_id == client.id for i in items)


def test_create_onboarding_checklist_is_idempotent(db_session):
    client = Client(company_name="Acme GC", status=ClientStatus.ACTIVE.value)
    db_session.add(client)
    db_session.commit()

    onboarding.create_onboarding_checklist(db_session, client)
    second = onboarding.create_onboarding_checklist(db_session, client)

    assert second == []
    assert db_session.query(ActionItem).filter(ActionItem.client_id == client.id).count() == 5


def test_run_onboarding_sweep_catches_clients_missing_a_checklist(db_session):
    active_client = Client(company_name="No Checklist Yet", status=ClientStatus.ACTIVE.value)
    prospect = Client(company_name="Still A Prospect", status=ClientStatus.PROSPECT.value)
    db_session.add_all([active_client, prospect])
    db_session.commit()

    stats = onboarding.run_onboarding_sweep(db_session)

    assert stats["checklists_created"] == 1
    assert db_session.query(ActionItem).filter(ActionItem.client_id == active_client.id).count() == 5
    assert db_session.query(ActionItem).filter(ActionItem.client_id == prospect.id).count() == 0
