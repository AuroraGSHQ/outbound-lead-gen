from app.config import Settings
from app.models import Lead, LeadStatus
from app.services import lead_import


def _settings(tmp_icp_path: str) -> Settings:
    return Settings(icp_config_path=tmp_icp_path)


def _write_icp(tmp_path):
    icp_path = tmp_path / "icp.yaml"
    icp_path.write_text(
        """
target_industries: ["general contractor"]
target_titles: ["Owner"]
company_size_min: 2
company_size_max: 250
target_locations: ["Indiana"]
keywords_boost: []
exclude_domains: ["gmail.com"]
min_score_to_contact: 0.5
"""
    )
    return str(icp_path)


def test_suggest_mapping_guesses_common_explorium_headers():
    headers = ["prospect_full_name", "prospect_email", "company_name", "prospect_job_title", "company_employee_count"]
    mapping = lead_import.suggest_mapping(headers)
    assert mapping["contact_email"] == "prospect_email"
    assert mapping["contact_name"] == "prospect_full_name"
    assert mapping["company_name"] == "company_name"
    assert mapping["contact_title"] == "prospect_job_title"
    assert mapping["company_size"] == "company_employee_count"


def test_suggest_mapping_does_not_reuse_a_header_for_two_fields():
    headers = ["company_name", "name"]
    mapping = lead_import.suggest_mapping(headers)
    # "company_name" should win company_name, not get double-claimed by contact_name
    assert mapping["company_name"] == "company_name"
    assert mapping.get("contact_name") == "name"


def test_parse_headers_returns_first_row():
    csv_text = "a,b,c\n1,2,3\n"
    assert lead_import.parse_headers(csv_text) == ["a", "b", "c"]


def test_parse_headers_handles_empty_file():
    assert lead_import.parse_headers("") == []


def test_import_scores_and_queues_matching_rows(db_session, tmp_path):
    icp_path = _write_icp(tmp_path)
    settings = _settings(icp_path)
    csv_text = (
        "full_name,email,company,title,size,loc\n"
        "Jamie Lee,jamie@acmegc.example.com,Acme GC,Owner,50,Indianapolis Indiana\n"
    )
    mapping = {
        "contact_name": "full_name",
        "contact_email": "email",
        "company_name": "company",
        "contact_title": "title",
        "company_size": "size",
        "location": "loc",
    }

    stats = lead_import.import_leads_from_csv(db_session, settings, csv_text, mapping)

    assert stats == {"rows": 1, "imported": 1, "duplicates": 0, "excluded": 0, "queued": 1, "missing_email": 0}
    lead = db_session.query(Lead).one()
    assert lead.contact_email == "jamie@acmegc.example.com"
    assert lead.source == "vibe_prospecting"
    assert lead.status == LeadStatus.QUEUED.value


def test_import_skips_rows_without_a_mapped_email_column(db_session, tmp_path):
    settings = _settings(_write_icp(tmp_path))
    csv_text = "full_name,company\nJamie Lee,Acme GC\n"
    stats = lead_import.import_leads_from_csv(db_session, settings, csv_text, {"contact_name": "full_name"})
    assert stats["missing_email"] == 1
    assert stats["imported"] == 0


def test_import_deduplicates_by_email(db_session, tmp_path):
    settings = _settings(_write_icp(tmp_path))
    csv_text = "email,company\nrepeat@example.com,Acme GC\n"
    mapping = {"contact_email": "email", "company_name": "company"}

    first = lead_import.import_leads_from_csv(db_session, settings, csv_text, mapping)
    second = lead_import.import_leads_from_csv(db_session, settings, csv_text, mapping)

    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["duplicates"] == 1


def test_import_excludes_configured_domains(db_session, tmp_path):
    settings = _settings(_write_icp(tmp_path))
    csv_text = "email,domain\nsomeone@gmail.com,gmail.com\n"
    mapping = {"contact_email": "email", "domain": "domain"}

    stats = lead_import.import_leads_from_csv(db_session, settings, csv_text, mapping)

    assert stats["excluded"] == 1
    assert stats["imported"] == 0
