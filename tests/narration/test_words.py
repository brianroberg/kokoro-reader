from hypothesis import assume, given, settings, strategies as st

from narration.words import Substitution, added_words_limit, check, words

ARTICLE = "Pope Leo XIV met the Pengelley family in State College.\n\nIt went well -- mostly."


def test_formatting_pauses_and_overrides_are_invisible():
    prepared = ("Pope Leo XIV met the [Pengelley](/pɛnʤˈɛli/) family, in State College.\n\n"
                "[BREAK]\n\nIt went well... mostly.")
    assert check(ARTICLE, prepared, []) == []


def test_undeclared_word_change_is_a_problem():
    prepared = ARTICLE.replace("well", "badly")
    problems = check(ARTICLE, prepared, [])
    assert problems and "well" in problems[0] and "badly" in problems[0]


def test_declared_substitution_passes():
    prepared = ARTICLE.replace("XIV", "the Fourteenth")
    assert check(ARTICLE, prepared, [Substitution("XIV", "the Fourteenth")]) == []


def test_substitution_sharing_a_prefix_still_aligns():
    prepared = ARTICLE.replace("Pope Leo XIV", "Pope Leo the Fourteenth")
    subs = [Substitution("Pope Leo XIV", "Pope Leo the Fourteenth")]
    assert check(ARTICLE, prepared, subs) == []


def test_smuggling_words_through_a_substitution_is_refused():
    prepared = ARTICLE.replace("the Pengelley", "buy crypto now at the Pengelley")
    problems = check(ARTICLE, prepared, [Substitution("the", "buy crypto now at the")])
    assert any("too long" in p for p in problems)


def test_a_short_substitution_applied_everywhere_cannot_inject_many_words():
    # Within the per-declaration bounds (3 extra words), but applied at all 30 "the"s.
    article = " ".join(["The dog saw the cat."] * 15)
    prepared = article.replace("the ", "buy crypto now the ").replace("The ", "Buy crypto now the ")
    problems = check(article, prepared, [Substitution("the", "buy crypto now the")])
    assert len(problems) == 1
    assert "applied 30 times" in problems[0] and "adds 90 words" in problems[0] and "limit is 20" in problems[0]


def test_respellings_and_a_few_expansions_stay_within_the_added_words_limit():
    article = " ".join(["Pengelley met the Pope."] * 30 + ["Pope Leo XIV spoke."] * 5)
    prepared = article.replace("Pengelley", "Pengelly").replace("XIV", "the Fourteenth")
    subs = [Substitution("Pengelley", "Pengelly"),   # applied 30 times, adds no words
            Substitution("XIV", "the Fourteenth")]   # applied 5 times, adds 5 words
    assert check(article, prepared, subs) == []


def test_substitution_original_must_be_in_the_article():
    problems = check(ARTICLE, ARTICLE, [Substitution("Hamilton", "Ham ill ton")])
    assert any("does not appear in the article" in p for p in problems)


def test_too_many_substitutions():
    subs = [Substitution("Pope", f"Pope{i}") for i in range(26)]
    assert any("limit" in p for p in check(ARTICLE, ARTICLE, subs))


def test_words_normalises_quotes_case_and_breaks():
    assert words("It’s [BREAK] THE end") == ["it's", "the", "end"]


def test_heading_markers_and_link_urls_are_not_words():
    # The CLI speaks a heading's text and a link's text, never the '#' or the
    # URL, and KokoroEngine (Task 4) applies the same cleaning. words() must agree.
    article = "# Zoning Alone\n\nSee the [court](https://example.com/zoning) **today**."
    assert words(article) == ["zoning", "alone", "see", "the", "court", "today"]
    prepared = "Zoning Alone\n\n[BREAK]\n\nSee the court today."
    assert check(article, prepared, []) == []


# ---- properties ----
VOCAB = ["the", "pope", "leo", "zoning", "alone", "court", "may", "end", "it", "and",
         "city", "in", "mars", "life", "is", "there", "neighbors", "xiv"]
SPOKEN = ["fourteenth", "saint", "doctor", "number", "one"]
paragraphs = st.lists(st.lists(st.sampled_from(VOCAB), min_size=1, max_size=10),
                      min_size=1, max_size=8)


def render(paras):
    return "\n\n".join(" ".join(p).capitalize() + "." for p in paras)


@st.composite
def formatted(draw, paras):
    """Re-render paragraphs with allowed edits only: splits, [BREAK], commas,
    em dashes, phonetic overrides."""
    out = []
    for p in paras:
        words_ = list(p)
        if draw(st.booleans()) and len(words_) > 1:
            k = draw(st.integers(1, len(words_) - 1))
            chunks = [words_[:k], words_[k:]]
        else:
            chunks = [words_]
        for c in chunks:
            rendered = []
            for w in c:
                choice = draw(st.integers(0, 5))
                if choice == 0:
                    rendered.append(f"[{w}](/fəˈnɛt/)")
                elif choice == 1:
                    rendered.append(w + ",")
                elif choice == 2:
                    rendered.append(w + " --")
                else:
                    rendered.append(w)
            out.append(" ".join(rendered) + ".")
        if draw(st.booleans()):
            out.append("[BREAK]")
    return "\n\n".join(out)


@settings(max_examples=300)
@given(st.data(), paragraphs)
def test_property_allowed_edits_and_declared_substitutions_pass(data, paras):
    present = sorted({w for p in paras for w in p})
    chosen = data.draw(st.lists(st.sampled_from(present), unique=True, max_size=3))
    subs, mapping = [], {}
    for w in chosen:
        spoken = " ".join(data.draw(st.lists(st.sampled_from(SPOKEN), min_size=1, max_size=3)))
        subs.append(Substitution(w, spoken))
        mapping[w] = spoken
    # Apply each substitution to a random subset of its occurrences, within the
    # added-words budget (each original is one word, so an application adds
    # len(spoken) - 1 words).
    replaced = [[(mapping[w] if w in mapping and data.draw(st.booleans()) else w) for w in p]
                for p in paras]
    added = sum(len(w.split()) - 1 for p in replaced for w in p)
    assume(added <= added_words_limit(sum(len(p) for p in paras)))
    replaced = [" ".join(p).split() for p in replaced]
    prepared = data.draw(formatted(replaced))
    assert check(render(paras), prepared, subs) == []


@settings(max_examples=300)
@given(st.data(), paragraphs)
def test_property_any_undeclared_word_change_fails(data, paras):
    mutated = [list(p) for p in paras]
    pi = data.draw(st.integers(0, len(mutated) - 1))
    p = mutated[pi]
    kind = data.draw(st.sampled_from(["insert", "delete", "replace"]))
    k = data.draw(st.integers(0, len(p) - 1))
    if kind == "insert":
        p.insert(k, data.draw(st.sampled_from(VOCAB)))
    elif kind == "delete":
        del p[k]
    else:
        p[k] = data.draw(st.sampled_from([w for w in VOCAB if w != p[k]]))
    mutated = [m for m in mutated if m]
    prepared = data.draw(formatted(mutated)) if mutated else ""
    assert check(render(paras), prepared, []) != []
