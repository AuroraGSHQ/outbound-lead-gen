from app.icp import ICPConfig, score_lead


def make_icp(**overrides) -> ICPConfig:
    base = dict(
        target_industries=["Software"],
        target_titles=["CEO", "Founder"],
        company_size_min=10,
        company_size_max=500,
        target_locations=["United States"],
        keywords_boost=["Series A"],
        exclude_domains=["gmail.com"],
        min_score_to_contact=0.6,
    )
    base.update(overrides)
    return ICPConfig(**base)


def test_perfect_match_scores_near_one():
    icp = make_icp()
    candidate = {
        "company_name": "Acme Software",
        "domain": "acme.io",
        "industry": "Software",
        "company_size": 50,
        "location": "San Francisco, CA, United States",
        "contact_title": "Founder & CEO",
        "description": "Acme just closed its Series A",
    }
    result = score_lead(candidate, icp)
    assert result.score >= 0.9
    assert not result.excluded


def test_excluded_domain_scores_zero():
    icp = make_icp()
    candidate = {
        "domain": "gmail.com",
        "industry": "Software",
        "contact_title": "CEO",
        "company_size": 50,
        "location": "United States",
    }
    result = score_lead(candidate, icp)
    assert result.excluded is True
    assert result.score == 0.0


def test_subdomain_of_excluded_domain_is_excluded():
    icp = make_icp(exclude_domains=["yahoo.com"])
    candidate = {"domain": "mail.yahoo.com", "contact_title": "CEO"}
    result = score_lead(candidate, icp)
    assert result.excluded is True


def test_wrong_industry_and_title_scores_low():
    icp = make_icp()
    candidate = {
        "domain": "randomshop.com",
        "industry": "Retail",
        "contact_title": "Store Associate",
        "company_size": 5000,
        "location": "Berlin, Germany",
    }
    result = score_lead(candidate, icp)
    assert result.score < 0.3
    assert not result.excluded


def test_unset_filters_do_not_penalize():
    icp = make_icp(target_industries=[], target_titles=[], target_locations=[])
    candidate = {"domain": "whatever.com", "company_size": 50}
    result = score_lead(candidate, icp)
    # industry(0.3) + title(0.25) + location(0.15) all pass through unset, plus size(0.2)
    assert result.score >= 0.9


def test_missing_company_size_gets_partial_credit_not_full():
    icp = make_icp()
    candidate = {
        "domain": "acme.io",
        "industry": "Software",
        "contact_title": "CEO",
        "location": "United States",
    }
    result = score_lead(candidate, icp)
    assert result.score < 1.0
