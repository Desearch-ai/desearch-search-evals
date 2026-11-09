from scripts import misses


def test_forms_strip_fragments_and_unwrap_archive_copies():
    assert misses.forms("https://a.org/x#:~:text=foo")[0] == ["https://a.org/x"]
    assert misses.forms("https://web.archive.org/web/2020id_/http:/a.org/x#top")[0] == ["http://a.org/x"]
    assert misses.forms("http://web.archive.org/web/1999/a.org/y")[0] == ["https://a.org/y"]


def test_wiki_titles_from_every_url_form():
    want = ("en.wikipedia.org", "Rock_(band)")
    for url in (
        "https://en.m.wikipedia.org/wiki/Rock_(band)",
        "https://en.wikipedia.org/wiki/Rock%20(band)",
        "https://en.wikipedia.org/w/index.php?title=Rock_(band)&oldid=123&diff=prev",
        "https://en.wikipedia.org/w/api.php?action=parse&page=Rock_(band)&format=json",
        "https://en.wikipedia.org/w/api.php?action=query&titles=Rock (band)|Other",
        "https://en.wikipedia.org/api/rest_v1/page/html/Rock_(band)",
        "https://en.wikipedia.org/w/rest.php/v1/page/Rock_(band)/html",
    ):
        assert misses.wiki_title(url) == want, url
    assert misses.wiki_title("https://www.wikipedia.org/") is None
    assert misses.wiki_title("https://a.org/wiki/X") is None


def test_wiki_url_encodes_like_our_keys():
    assert misses.wiki_url("en.wikipedia.org", "O'Neil (2001 film), Part!: A-B.~/C") == (
        "https://en.wikipedia.org/wiki/O%27Neil_(2001_film),_Part!:_A-B.~/C"
    )
    assert misses.wiki_url("fr.wikipedia.org", "Été") == "https://fr.wikipedia.org/wiki/%C3%89t%C3%A9"


def test_variants_cover_scheme_www_and_trailing_slash():
    assert misses.variants("http://a.org/x?q=1") == [
        "http://a.org/x?q=1",
        "https://a.org/x?q=1",
        "https://a.org/x/?q=1",
        "https://www.a.org/x?q=1",
        "https://www.a.org/x/?q=1",
    ]
    assert misses.variants("https://www.a.org/") == [
        "https://www.a.org/",
        "https://www.a.org",
        "https://a.org/",
        "https://a.org",
    ]


def held_only(*urls):
    calls = []

    def page(url):
        calls.append(url)
        if url in urls:
            return {"found": True, "page": {"live": True}, "vectors": 3}
        return {"found": False}

    return page, calls


def test_lookup_stops_at_the_first_held_form():
    page, calls = held_only("https://www.a.org/x/")
    assert misses.lookup("http://a.org/x#frag", page, None) == (True, "https://www.a.org/x/")
    assert calls[-1] == "https://www.a.org/x/" and len(calls) == 5


def test_lookup_needs_live_pages_with_vectors():
    def page(url):
        return {"found": True, "page": {"live": False}, "vectors": 3}

    assert misses.lookup("https://a.org/x", page, None) == (False, None)
    assert misses.lookup("https://a.org/x", lambda url: None, None) == (None, None)


def test_lookup_follows_wikipedia_redirects():
    target = "https://en.wikipedia.org/wiki/Rock_(band)"
    page, _ = held_only(target)
    assert misses.lookup("https://en.m.wikipedia.org/wiki/Rock_band", page, lambda h, t: ("Rock_(band)", True, False)) == (True, target)


def test_redirect_to_a_disambiguation_page_is_not_ours():
    page, _ = held_only()
    resolve = lambda h, t: ("Mercury", True, True)
    assert misses.lookup("https://en.wikipedia.org/wiki/Mercury_(x)", page, resolve) == (misses.NOT_OURS, None)
    assert misses.lookup("https://en.wikipedia.org/wiki/Mercury", page, lambda h, t: ("Mercury", False, True)) == (False, None)
    assert misses.lookup("https://en.wikipedia.org/wiki/X", page, lambda h, t: None) == (None, None)


def test_resolver_reads_redirects_and_disambiguation():
    seen = []

    def fetch(url):
        seen.append(url)
        return {"query": {"redirects": [{"from": "Mercury (x)", "to": "Mercury"}], "pages": [{"title": "Mercury", "pageprops": {"disambiguation": ""}}]}}

    resolve = misses.wiki_resolver(fetch, pause=0)
    assert resolve("en.wikipedia.org", "Mercury_(x)") == ("Mercury", True, True)
    resolve("en.wikipedia.org", "Mercury_(x)")
    assert len(seen) == 1 and "redirects=1" in seen[0] and "titles=Mercury+%28x%29" in seen[0]
    missing = misses.wiki_resolver(lambda url: {"query": {"pages": [{"title": "Nope", "missing": True}]}}, pause=0)
    assert missing("en.wikipedia.org", "Nope") == ("Nope", False, False)


def test_classify_groups_spellings_and_ranks_matched_pages():
    flag = {"hit": 1}
    profiles = {
        "desearch-index": [{"question_id": "q", "results": [{"url": "https://z.org"}]}],
        "exa": [{"question_id": "q", "results": [{"url": "https://en.m.wikipedia.org/wiki/Rock_band#x", "page": flag}]}],
        "tavily": [{"question_id": "q", "results": [{"url": "https://en.wikipedia.org/wiki/Rock_band", "page": flag}]}],
    }
    [case] = misses.collect(profiles)
    assert list(case["answering"]) == ["https://en.wikipedia.org/wiki/Rock_band"]
    page, _ = held_only("https://en.wikipedia.org/wiki/Rock_(band)")
    search = lambda qid: {"results": [{"rank": 7, "url": "https://en.wikipedia.org/wiki/Rock_(band)"}], "failed": {}}
    misses.classify(case, page, lambda h, t: ("Rock_(band)", True, False), search)
    assert case["held"] == {"https://en.wikipedia.org/wiki/Rock_band": True} and case["rank"] == 7
