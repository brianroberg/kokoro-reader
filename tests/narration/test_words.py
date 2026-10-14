import pytest
from hypothesis import given, settings, strategies as st

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


# ---- symbols and numbers the engine speaks ----
def test_spoken_symbols_and_numbers_are_words():
    assert words("Up 3.5% to $1,000 at 12:30, 1/2 & £2 or €3 @ 5 + 1") == [
        "up", "3.5", "%", "to", "$", "1,000", "at", "12:30", "1/2", "&", "£", "2",
        "or", "€", "3", "@", "5", "+", "1"]


def test_a_number_glued_to_letters_is_one_word():
    assert words("the 3rd of the 1990s") == ["the", "3rd", "of", "the", "1990s"]


@pytest.mark.parametrize("article, prepared", [
    # Each of these changes what the engine says, so each must be a problem.
    ("The rate is 5 and the dog saw the cat.", "The rate is $5% and the dog & saw + the cat @ €."),
    ("Prices rose 40 points.", "Prices rose 40% points."),
    ("It grew 3.5 times and 1/2 of it.", "It grew 3 5 times and 1 2 of it."),
    ("Prices rose 40% today.", "Prices rose 40 today."),
])
def test_changes_to_symbols_and_numbers_are_problems(article, prepared):
    assert check(article, prepared, [])


def test_matching_symbols_and_numbers_pass_through_formatting():
    article = "Prices rose 40% to $3.5 million at 12:30, and 1/2 of it was A & B @ home + more."
    prepared = ("Prices rose 40%, to $3.5 million\n\n[BREAK]\n\n"
                "at 12:30 -- and 1/2 of it was A & B @ home + more.")
    assert check(article, prepared, []) == []


# ---- phonetic overrides ----
def test_empty_phonemes_would_delete_a_word_from_the_audio():
    problems = check("It went well mostly.", "It went well [mostly](//).", [])
    assert len(problems) == 1 and "[mostly](//)" in problems[0] and "empty" in problems[0]


def test_one_override_cannot_voice_several_words():
    override = "[the](/bˈI kɹˈɪptoʊ nˈaʊ ðə/)"
    problems = check("The dog saw the cat.", f"The dog saw {override} cat.", [])
    assert len(problems) == 1 and override in problems[0] and "whitespace" in problems[0]


@pytest.mark.parametrize("word, length, allowed", [
    ("the", 12, True), ("the", 13, False),              # at least 12 characters
    ("Kevorkian", 27, True), ("Kevorkian", 28, False),  # or three per displayed character
])
def test_override_phonemes_are_bounded_by_the_displayed_word(word, length, allowed):
    article = f"We met {word} today."
    prepared = f"We met [{word}](/{'ɛ' * length}/) today."
    assert (check(article, prepared, []) == []) is allowed


def test_an_override_displays_exactly_one_word():
    problems = check("The dog saw the cat.", "The dog saw [the cat](/ðəkæt/).", [])
    assert len(problems) == 1 and "[the cat](/ðəkæt/)" in problems[0] and "exactly one word" in problems[0]


def test_a_real_respelling_override_passes():
    assert check("Kevorkian spoke.", "[Kevorkian](/kɛvˈɔːɹkiən/) spoke.", []) == []


# ---- bounds on what a substitution may change ----
def test_a_substitution_cannot_replace_the_same_word_everywhere():
    article = " ".join(["The dog saw the cat."] * 15)
    prepared = article.replace("the ", "crypto ").replace("The ", "Crypto ")
    problems = check(article, prepared, [Substitution("the", "crypto")])
    assert len(problems) == 1
    assert ("applied 30 times" in problems[0] and "changes 30 words" in problems[0]
            and "limit is 20" in problems[0])


def test_a_substitution_original_is_bounded_too():
    original = "the dog saw the cat the dog saw the cat the dog saw"  # 13 words
    article = original + " and left."
    spoken = "buy crypto now at our site"
    problems = check(article, article.replace(original, spoken), [Substitution(original, spoken)])
    assert any("original" in p and "too long" in p for p in problems)


def test_spelling_out_an_abbreviation_is_not_counted_as_a_change():
    article = " ".join(["The DOJ sued."] * 10)
    prepared = article.replace("DOJ", "D O J")
    assert check(article, prepared, [Substitution("DOJ", "D O J")]) == []  # adds 20 words


# ---- mismatch messages ----
def test_mismatch_message_ignores_correctly_applied_substitutions():
    article = " ".join(["Pengelley met the Pope."] * 10) + " It went well in State College today."
    prepared = article.replace("Pengelley", "Pengelly").replace("well", "badly")
    problems = check(article, prepared, [Substitution("Pengelley", "Pengelly")])
    assert any("badly" in p for p in problems)
    assert not any("pengelley" in p.lower() or "pengelly" in p.lower() for p in problems)


def test_mismatch_message_shows_context_from_both_texts():
    problems = check("one two three four five six seven", "one two three FOUR5 five six seven", [])
    assert len(problems) == 1
    assert "'one two three [four] five six seven'" in problems[0]
    assert "'one two three [four5] five six seven'" in problems[0]


def test_mismatch_message_names_a_missing_word():
    problems = check("one two three", "one three", [])
    assert len(problems) == 1
    assert "'one [two] three'" in problems[0] and "'one [(nothing)] three'" in problems[0]


# ---- properties ----
WORDS = ["the", "pope", "leo", "zoning", "alone", "court", "may", "end", "it", "and",
         "city", "in", "mars", "life", "is", "there", "neighbors", "xiv"]
# Numbers with internal punctuation, each with spellings that change what is said.
NUMBERS = {"3.5": ["3 5", "35", "3.6", "3.50"], "1,000": ["1 000", "1000", "1,001"],
           "1/2": ["1 2", "12", "1/3"], "12:30": ["12 30", "1230", "12:31"]}
SYMBOLS = ["%", "&", "@", "$", "£", "€"]
VOCAB = WORDS + list(NUMBERS) + SYMBOLS  # what the generated articles are made of
BAD_PHONEMES = ["", "a b", "ɛ" * 30]     # empty, whitespace, longer than any word here allows
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
    # Apply each substitution to a random subset of its occurrences, while both
    # budgets allow. Each original is one word, so an application adds
    # len(spoken) - 1 words and changes len(spoken) words (counted as if no
    # substitution were a respelling, the stricter reading).
    limit = added_words_limit(sum(len(p) for p in paras))
    added = changed = 0
    replaced = []
    for p in paras:
        row = []
        for w in p:
            n = len(mapping[w].split()) if w in mapping else 0
            if n and added + n - 1 <= limit and changed + n <= limit and data.draw(st.booleans()):
                added += n - 1
                changed += n
                row.extend(mapping[w].split())
            else:
                row.append(w)
        replaced.append(row)
    prepared = data.draw(formatted(replaced))
    assert check(render(paras), prepared, subs) == []


@settings(max_examples=300)
@given(st.data(), paragraphs)
def test_property_any_undeclared_word_change_fails(data, paras):
    source = [list(p) for p in paras]
    mutated = [list(p) for p in paras]
    pi = data.draw(st.integers(0, len(mutated) - 1))
    p = mutated[pi]
    kind = data.draw(st.sampled_from(
        ["insert", "delete", "replace", "symbol", "number", "override"]))
    k = data.draw(st.integers(0, len(p) - 1))
    if kind == "insert":
        p.insert(k, data.draw(st.sampled_from(VOCAB)))
    elif kind == "delete":
        del p[k]
    elif kind == "replace":
        p[k] = data.draw(st.sampled_from([w for w in VOCAB if w != p[k]]))
    elif kind == "symbol":
        # "40" -> "40%" or "$40": a symbol the engine speaks, glued to a word
        symbol = data.draw(st.sampled_from(SYMBOLS + ["+"]))
        p[k] = p[k] + symbol if data.draw(st.booleans()) else symbol + p[k]
    elif kind == "number":
        # the article says "3.5"; the prepared text says "3 5", "35", ...
        number = data.draw(st.sampled_from(sorted(NUMBERS)))
        source[pi].insert(k, number)
        p.insert(k, data.draw(st.sampled_from(NUMBERS[number])))
    else:
        # the right word, wrapped in an override that could say anything
        p[k] = f"[{p[k]}](/{data.draw(st.sampled_from(BAD_PHONEMES))}/)"
    mutated = [m for m in mutated if m]
    prepared = data.draw(formatted(mutated)) if mutated else ""
    assert check(render(source), prepared, []) != []
