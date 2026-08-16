"""
Drift guard: the About-tab cards and Data/wiki.json must stay in lockstep.

Feature copy used to live in three places (About cards, wiki.json, docs) and
drifted — cards without a wiki entry had dead click-through popups, and a wiki
entry without a card was orphaned. wiki.json is now the single source: each card
is a `data-wiki="KEY"` shell whose face + modal are rendered from the entry. This
test enforces that invariant so the drift cannot silently come back.

No git, no network — pure file parity:
  * every card key (minus the intentionally-dynamic roadmap card) has a wiki entry;
  * every wiki entry has exactly one card (catches reverse drift the `rbr` bug showed);
  * no duplicate `data-wiki` keys;
  * every entry has a non-empty `summary` (the card face) and `title`.
"""
import json
import os
import re
import unittest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_HTML = os.path.join(_ROOT, "web", "index.html")
_WIKI_JSON = os.path.join(_ROOT, "Data", "wiki.json")

# The roadmap card is deliberately NOT wiki-backed: its body is hydrated at
# runtime from /api/roadmap (single-sourced already). It carries data-wiki for
# click styling only, so it is exempt from the parity check.
EXCLUDE = {"aether_rd_roadmap"}


def _card_keys():
    with open(_INDEX_HTML, encoding="utf-8") as fh:
        html = fh.read()
    return re.findall(r'data-wiki="([^"]+)"', html)


def _wiki():
    with open(_WIKI_JSON, encoding="utf-8") as fh:
        return json.load(fh)


class AboutWikiSyncTest(unittest.TestCase):
    def test_no_duplicate_card_keys(self):
        keys = _card_keys()
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(dupes, [], f"duplicate data-wiki keys in index.html: {dupes}")

    def test_every_card_has_a_wiki_entry(self):
        cards = set(_card_keys()) - EXCLUDE
        wiki = set(_wiki())
        missing = sorted(cards - wiki)
        self.assertEqual(missing, [], f"cards with no wiki.json entry (dead popups): {missing}")

    def test_every_wiki_entry_has_a_card(self):
        cards = set(_card_keys()) - EXCLUDE
        wiki = set(_wiki())
        orphans = sorted(wiki - cards)
        self.assertEqual(orphans, [], f"wiki entries with no About card: {orphans}")

    def test_every_entry_has_summary_and_title(self):
        wiki = _wiki()
        no_summary = sorted(k for k, v in wiki.items() if not str(v.get("summary", "")).strip())
        no_title = sorted(k for k, v in wiki.items() if not str(v.get("title", "")).strip())
        self.assertEqual(no_summary, [], f"wiki entries missing a card-face 'summary': {no_summary}")
        self.assertEqual(no_title, [], f"wiki entries missing a 'title': {no_title}")


if __name__ == "__main__":
    unittest.main()
