import random
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from models import (
    MAX_STAGE,
    MIN_STAGE,
    AddResult,
    BatchAddResult,
    DBStatus,
    FetchResult,
    FetchStatus,
    PartOfSpeech,
)


def process_string(text):
    return text.lower().strip()


def process_strings(text):
    return [t.lower().strip() for t in text]


class Fetcher:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/115.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.5",
    }
    timeout = (5, 10)

    def make_unordered_list(self, ol_element):
        item_symbol = "*"
        the_list = ol_element.find_all("li", recursive=False)
        for li in the_list:
            if li.contents:
                if isinstance(li.contents[0], str):
                    li.contents[0].replace_with(
                        f"{item_symbol} {li.contents[0]}")
                else:
                    li.insert(0, f"{item_symbol} ")
            nested_ol = li.find("ol")
            if nested_ol:
                self.make_unordered_list(nested_ol)

    def enumerate_list(self, ol_element):
        the_list = ol_element.find_all("li", recursive=False)
        for index, li in enumerate(the_list, start=1):
            enumeration = f"{index})"
            if li.contents:
                if isinstance(li.contents[0], str):
                    li.contents[0].replace_with(
                        f"{enumeration} {li.contents[0]}")
                else:
                    li.insert(0, f"{enumeration} ")
            nested_ol = li.find("ol")
            if nested_ol:
                self.make_unordered_list(nested_ol)

    def merge_strings(self, input_list):
        result = []
        buffer = ""
        for s in input_list:
            stripped_str = s.strip()
            if stripped_str:
                if stripped_str[0].isdigit():
                    if buffer:
                        result.append(buffer)
                    buffer = s
                elif stripped_str.startswith("*"):
                    buffer += " " + s
        if buffer:
            result.append(buffer)
        return result

    def process_def_list(self, def_list):
        for li in def_list.find_all("li"):
            first_c = li.find(True)
            if first_c and first_c.name == "b" and first_c.get_text().strip().isdigit():
                li.decompose()
        unwanted_classes = [
            "citation-whole", "h-usage-example", "Latn mention e-example",
            "cited-source", "q-hellip-sp", "q-hellip-b", "see-cites",
            "external text", "mw-empty-elt"
        ]
        for unwanted in def_list.find_all(class_=unwanted_classes):
            unwanted.extract()
        self.enumerate_list(def_list)
        remaining_text = def_list.get_text()
        lines = remaining_text.splitlines()
        merged_strings = self.merge_strings(lines)
        return [item.replace("*", "\n*") for item in merged_strings]

    def from_word_to_list(self, word):
        url = f"https://en.wiktionary.org/wiki/{quote(word.strip())}"
        try:
            response = requests.get(
                url, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as e:
            return FetchResult(FetchStatus.NETWORK_ERROR, detail=str(e))
        if response.status_code == 404:
            return FetchResult(FetchStatus.NOT_FOUND)
        if response.status_code != 200:
            return FetchResult(FetchStatus.NETWORK_ERROR,
                               detail=f"HTTP {response.status_code}")
        soup = BeautifulSoup(response.content, "html.parser")
        if not soup.find("h2", string="English"):
            return FetchResult(FetchStatus.NOT_FOUND)
        list_of_res = []
        for i in PartOfSpeech:
            for part_section in soup.find_all(id=re.compile(rf"^{i}(_\d+)?$")):
                def_list = part_section.findNext("ol")
                if def_list:
                    processed_list = self.process_def_list(def_list)
                    if processed_list:
                        list_of_res.append(i)
                        list_of_res.extend(processed_list)
        if not list_of_res:
            return FetchResult(FetchStatus.NOT_FOUND)
        return FetchResult(FetchStatus.OK, definition=" ".join(list_of_res))


class StudyPlanner:
    STAGE_INTERVALS = {
        1: timedelta(0),
        2: timedelta(minutes=5),
        3: timedelta(minutes=10),
        4: timedelta(hours=1),
        5: timedelta(days=1),
        6: timedelta(days=3),
        7: timedelta(days=7),
        8: timedelta(days=14),
        9: timedelta(days=21),
        10: timedelta(days=30),
        11: timedelta(days=60),
        12: timedelta(days=90),
        13: timedelta(days=180),
        14: timedelta(days=270),
        15: timedelta(days=365),
    }
    LOOKAHEAD_STAGES = (2, 3, 4)

    def __init__(self, db, rng=random):
        self.db = db
        self.rng = rng

    def next_word(self, advance_days=0):
        now = datetime.now(timezone.utc)
        horizons = [timedelta(days=advance_days)]
        horizons += [self.STAGE_INTERVALS[s] for s in self.LOOKAHEAD_STAGES]
        for horizon in horizons:
            due = self.db.get_words_due(now + horizon)
            if due:
                return self.rng.choice(due)
        return None

    @staticmethod
    def answer_stages(current_stage):
        stages = [MIN_STAGE]
        if current_stage != MIN_STAGE:
            stages.append(current_stage)
        stages += [s for s in (current_stage + 1,
                               current_stage + 2) if s <= MAX_STAGE]
        return stages

    def reschedule(self, word, stage):
        stage = min(MAX_STAGE, max(MIN_STAGE, stage))
        revision_at = datetime.now(timezone.utc) + self.STAGE_INTERVALS[stage]
        return self.db.set_stage(word, stage, revision_at)

    def reset(self, word):
        return self.reschedule(word, MIN_STAGE)


class WordsAdder:
    def __init__(self, db, fetcher):
        self.db = db
        self.fetcher = fetcher

    def add_single_word(self, text):
        text = process_string(text)
        existing = self.db.get_word(text)
        if existing:
            return AddResult(DBStatus.EXISTS, existing)
        fetched = self.fetcher.from_word_to_list(text)
        if fetched.code is FetchStatus.NETWORK_ERROR:
            return AddResult(DBStatus.NETWORK_ERROR, detail=fetched.detail)
        if fetched.code is FetchStatus.NOT_FOUND:
            return AddResult(DBStatus.NOT_FOUND)
        result = self.db.add_word(text, fetched.definition)
        if result.success:
            return AddResult(DBStatus.OK, self.db.get_word(text))
        if result.code is DBStatus.EXISTS:
            return AddResult(DBStatus.EXISTS, self.db.get_word(text))
        return AddResult(DBStatus.ERROR, detail=result.detail)

    def add_multiple_words(self, words):
        batch = BatchAddResult(added=[], already_added=[],
                               not_found=[], failed=[], skipped=[])
        words = process_strings(words)

        for index, text in enumerate(words):
            if self.db.get_word(text):
                batch.already_added.append(text)
                continue
            fetched = self.fetcher.from_word_to_list(text)
            if fetched.code is FetchStatus.NETWORK_ERROR:
                batch.skipped.extend(
                    w for w in words[index:] if not self.db.get_word(w))
                return BatchAddResult(
                    batch.added, batch.already_added, batch.not_found,
                    batch.failed, batch.skipped, network_detail=fetched.detail)
            if fetched.code is FetchStatus.NOT_FOUND:
                batch.not_found.append(text)
                continue
            result = self.db.add_word(text, fetched.definition)
            if result.success:
                batch.added.append(text)
            elif result.code is DBStatus.EXISTS:
                batch.already_added.append(text)
            else:
                batch.failed.append((text, result.detail))
        return batch
