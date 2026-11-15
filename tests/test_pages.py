"""Page routes: the landing page links to both modes, each mode has its own page,
and the header shows only the two modes."""

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_postmortem_has_its_own_page_with_the_bench_and_results():
    page = client.get("/postmortem")

    assert page.status_code == 200
    for marker in ('id="drop"', 'id="analyse"', 'id="output"', 'data-example="1"', "/static/results.css"):
        assert marker in page.text


def test_design_constraints_sit_in_step_03_above_the_analyse_button():
    page = client.get("/postmortem").text
    step3 = page.index('<span class="step-n" aria-hidden="true">03</span>')

    assert step3 < page.index('id="agent-options"') < page.index('id="analyse"')


def test_landing_page_links_to_the_postmortem_page_not_a_section():
    page = client.get("/").text

    assert 'href="/postmortem"' in page
    assert 'href="#postmortem"' not in page
    assert 'id="drop"' not in page


def test_header_shows_only_the_two_modes_on_every_page():
    for path in ("/", "/postmortem", "/interiors"):
        nav = client.get(path).text.split('<nav class="site-nav"', 1)[1].split("</nav>", 1)[0]
        assert "Postmortem" in nav and "Forecast" in nav
        assert "Method" not in nav and "Collection" not in nav
