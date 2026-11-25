"""Tests for the bot's deterministic rules: the ones that decide what goes live on the site."""
import shutil
from datetime import date

import pytest

import common
import publisher
import writer


# --------------------------------------------------------------------------- slugs / duplicates
def test_slugify_basic():
    assert common.slugify("Thales Software Intern — Bengaluru 2026!") == "thales-software-intern-bengaluru-2026"


def test_slugify_truncates_without_trailing_dash():
    s = common.slugify("a" * 89 + " bcd", max_len=90)
    assert len(s) <= 90 and not s.endswith("-")


def test_url_id_ignores_scheme_www_trailing_slash_and_tracking():
    a = common.url_id("https://www.example.com/careers/job-1/?utm_source=x")
    b = common.url_id("http://example.com/careers/job-1")
    assert a == b


def test_url_id_keeps_job_number_params():
    a = common.url_id("https://boards.greenhouse.io/acme?gh_jid=111")
    b = common.url_id("https://boards.greenhouse.io/acme?gh_jid=222")
    assert a != b


def test_similarity_ignores_stopwords():
    a = "Stripe Hiring Freshers Software Engineer Intern 2026"
    b = "stripe-software-engineer-intern-hiring-freshers-bengaluru-2026"
    assert common.similarity(a, b) >= 0.75
    assert common.similarity("Stripe SDE Intern", "ISRO Apprentice Recruitment") == 0.0
    assert common.similarity("", "anything") == 0.0


def test_free_slug_dedupes_against_existing_posts(tmp_path, monkeypatch):
    (tmp_path / "blogs").mkdir()
    (tmp_path / "blogs" / "acme-intern.html").write_text("x")
    (tmp_path / "blogs" / "acme-intern-2.html").write_text("x")
    monkeypatch.setattr(writer, "REPO_DIR", tmp_path)
    assert writer.free_slug("acme-intern") == "acme-intern-3"
    assert writer.free_slug("new-post") == "new-post"


# --------------------------------------------------------------------------- official links
LEAD_FROM_JOB_SITE = {"url": "https://www.freshersnow.com/acme-hiring/", "source_type": "rss"}


@pytest.mark.parametrize("url", [
    "https://www.freejobalert.com/acme-recruitment/",   # aggregator
    "https://acme.com/",                                # bare homepage
    "https://acme.com",
    "",                                                 # nothing
])
def test_not_official_link_rejects(url):
    assert writer.not_official_link(url, LEAD_FROM_JOB_SITE, set())


def test_not_official_link_rejects_the_lead_article_itself():
    job = {"url": "https://jobsite.example/acme-intern", "source_type": "rss"}
    assert writer.not_official_link("https://jobsite.example/acme-intern-apply", job, set())


def test_not_official_link_accepts_real_apply_pages():
    assert not writer.not_official_link("https://careers.acme.com/jobs/123", LEAD_FROM_JOB_SITE, set())
    # a prefetch-confirmed link is trusted even if it is a homepage
    assert not writer.not_official_link("https://acme.com/", LEAD_FROM_JOB_SITE, {"https://acme.com"})


def test_not_official_link_trusts_official_sources_on_same_domain():
    job = {"url": "https://boards.greenhouse.io/acme/jobs/1", "source_type": "ats"}
    assert not writer.not_official_link("https://boards.greenhouse.io/acme/jobs/1", job, set())


def test_is_pdf():
    assert writer.is_pdf("https://x.gov.in/notice.PDF?v=2#p1")
    assert not writer.is_pdf("https://x.gov.in/notice.html")


# --------------------------------------------------------------------------- house rules
BODY = """
<h2>About the Role</h2>
<p>Apply at <a href="https://careers.acme.com/1" target="_blank">the careers page <i class="fa fa-x"></i></a>.</p>
<h2>Official Links</h2>
<p><a href="https://careers.acme.com/1">Apply Now</a></p>
<h2>FAQ</h2>
<p>See <a href="https://acme.com/faq">FAQ</a> and <a href="/blogs/other.html">this post</a>.</p>
"""


def test_external_links_only_in_official_section():
    out = writer.links_only_in_official_section(BODY)
    before, rest = out.split("<h2>Official Links</h2>")
    official, after = rest.split("<h2>FAQ</h2>")
    assert "<a " not in before and "<strong>the careers page</strong>" in before
    assert '<a href="https://careers.acme.com/1">Apply Now</a>' in official
    assert "https://acme.com/faq" not in after and "<strong>FAQ</strong>" in after
    assert '<a href="/blogs/other.html">' in after  # internal links stay clickable


def test_links_rule_noop_without_official_section():
    body = '<h2>Intro</h2><a href="https://x.com">x</a>'
    assert writer.links_only_in_official_section(body) == body


@pytest.mark.parametrize("text", ["only 25 posts", "12 vacancies", "5+ openings", "40 seats", "1 position"])
def test_small_counts_are_caught(text):
    assert writer.small_count_mentions({}, f"<p>{text}</p>")


@pytest.mark.parametrize("text", ["1,265 posts", "3047 vacancies", "143 posts", "2026 openings", "B.Tech 2027 batch"])
def test_large_counts_and_years_are_allowed(text):
    assert writer.small_count_mentions({}, f"<p>{text}</p>") == []


def test_small_counts_checked_in_meta_too():
    assert writer.small_count_mentions({"seo_title": "ACME hiring 30 posts", "slug": "acme-30-posts"}, "")


def test_house_style_makes_official_link_green_and_deadline_box_red():
    body = ('<p><a href="https://careers.acme.com/1">Apply</a></p>'
            '<div class="tip-box"><h4><i class="fa-lightbulb"></i> Last date</h4><p>x</p></div>'
            '<div class="tip-box"><h4>Prep tip</h4><p>y</p></div>')
    out = writer.house_style(body, "https://careers.acme.com/1")
    assert "color:#22c55e" in out
    assert out.count('class="alert-box"') == 1 and "fa-clock" in out
    assert '<div class="tip-box"><h4>Prep tip</h4>' in out


def test_validate_page():
    writer.validate_page('<script type="application/ld+json">{"a": 1}</script>')
    with pytest.raises(ValueError):
        writer.validate_page("<title>{{title}}</title>")
    with pytest.raises(ValueError):
        writer.validate_page('<script type="application/ld+json">{"a": }</script>')


# --------------------------------------------------------------------------- sitemap
@pytest.fixture
def site(tmp_path, monkeypatch):
    shutil.copy(common.REPO_DIR / "sitemap.xml", tmp_path / "sitemap.xml")
    monkeypatch.setattr(publisher, "REPO_DIR", tmp_path)
    monkeypatch.setattr(publisher, "today_ist", lambda: date(2030, 1, 2))
    monkeypatch.setitem(publisher.CONFIG, "site_url", "https://www.getupdated.in")
    return tmp_path


def test_wire_sitemap_adds_one_valid_entry(site):
    before = (site / "sitemap.xml").read_text(encoding="utf-8")
    publisher.wire_sitemap({"slug": "zz-test-post-2030", "title": "Test & Post"})
    after = (site / "sitemap.xml").read_text(encoding="utf-8")
    loc = "<loc>https://www.getupdated.in/blogs/zz-test-post-2030.html</loc>"
    assert after.count(loc) == 1
    assert after.count("<url>") == before.count("<url>") + 1
    assert "Test &amp; Post" in after
    assert "<lastmod>2030-01-02</lastmod>" in after


def test_wire_sitemap_refuses_duplicate(site):
    meta = {"slug": "zz-test-post-2030", "title": "T"}
    publisher.wire_sitemap(meta)
    with pytest.raises(RuntimeError):
        publisher.wire_sitemap(meta)


# --------------------------------------------------------------------------- source health
def test_record_health_alerts_once_after_threshold_and_on_recovery():
    import watchdog
    health = {}
    msgs = [watchdog.record_health(health, "greenhouse/acme", "404") for _ in range(watchdog.FAIL_ALERT + 2)]
    assert [m is not None for m in msgs].count(True) == 1           # reported once, not every scan
    assert msgs[watchdog.FAIL_ALERT - 1].startswith("⚠️ greenhouse/acme")
    assert "works again" in watchdog.record_health(health, "greenhouse/acme", None)
    assert health["greenhouse/acme"]["fails"] == 0


def test_record_health_short_blips_are_silent():
    import watchdog
    health = {}
    for _ in range(watchdog.FAIL_ALERT - 1):
        assert watchdog.record_health(health, "feed", "timeout") is None
    assert watchdog.record_health(health, "feed", None) is None
