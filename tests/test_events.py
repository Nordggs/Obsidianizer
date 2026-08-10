"""Event contract tests — order, payloads, resilience of the event hook."""

import shutil
from pathlib import Path

from obsidianizer.config import Settings
from obsidianizer.events import EventType
from obsidianizer.llm import LLMClient
from obsidianizer.md_processor import MdProcessor
from obsidianizer.pipeline import run as run_pipeline
from obsidianizer.registry import ProcessorRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "md"


def _prepare(tmp_path: Path):
    source = tmp_path / "source"
    target = tmp_path / "processed"
    shutil.copytree(FIXTURES, source)
    return source, target


def _runner(source: Path, target: Path, llm=None, **kw):
    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    settings = Settings()
    settings.source = source
    settings.target = target
    settings.llm_enabled = False
    return run_pipeline(reg, settings, llm, **kw)


def test_first_run_event_sequence(tmp_path):
    source, target = _prepare(tmp_path)
    events = []
    report = _runner(source, target, on_event=events.append)

    kinds = [e.type for e in events]
    assert kinds[0] == EventType.SCAN_STARTED
    assert kinds[-1] == EventType.FINISHED
    # two files processed: started → done for each
    assert kinds.count(EventType.FILE_STARTED) == 2
    assert kinds.count(EventType.FILE_DONE) == 2
    assert kinds.count(EventType.FILE_SKIPPED) == 0
    assert kinds.count(EventType.LLM_STARTED) == 0
    assert kinds == [EventType.SCAN_STARTED, *kinds[1:-1], EventType.FINISHED]

    assert report.processed == 2

    # per-file payload: 1-based progress within the batch total
    for e in events:
        if e.type is EventType.FILE_DONE:
            assert e.path.endswith(".md")
            assert e.total == 2
            assert 1 <= e.index <= 2


def test_second_run_emits_skips(tmp_path):
    source, target = _prepare(tmp_path)
    _runner(source, target)

    events = []
    _runner(source, target, on_event=events.append)
    assert [e.type for e in events].count(EventType.FILE_SKIPPED) == 2
    assert EventType.FILE_DONE not in [e.type for e in events]
    assert events[-1].type is EventType.FINISHED


def test_llm_started_is_emitted_before_llm_call(tmp_path):
    source, target = _prepare(tmp_path)

    reg = ProcessorRegistry()
    reg.register(".md", MdProcessor)
    settings = Settings()
    settings.source = source
    settings.target = target
    settings.llm_enabled = True
    dead_llm = LLMClient(
        endpoint="http://127.0.0.1:1",
        model="anything",
        timeout=1,
        limit_chars=6000,
        prompt="{content}",
    )

    events = []
    report = run_pipeline(reg, settings, dead_llm, on_event=events.append)
    kinds = [e.type for e in events]
    assert kinds.count(EventType.LLM_STARTED) == 2
    # per file: LLM_STARTED arrives strictly between its FILE_STARTED and FILE_DONE
    order_per_file: dict[int, list[EventType]] = {}
    for e in events:
        if e.type in (EventType.FILE_STARTED, EventType.LLM_STARTED, EventType.FILE_DONE):
            order_per_file.setdefault(e.index, []).append(e.type)
    for seq in order_per_file.values():
        assert seq == [
            EventType.FILE_STARTED,
            EventType.LLM_STARTED,
            EventType.FILE_DONE,
        ]
    # graceful degradation: the batch still completes
    assert report.failed == []
    assert report.processed == 2


def test_finished_carries_summary(tmp_path):
    source, target = _prepare(tmp_path)
    events = []
    _runner(source, target, on_event=events.append)
    last = events[-1]
    assert last.type is EventType.FINISHED
    assert "обработано=2" in last.message
    assert last.total == 2


def test_broken_listener_does_not_kill_batch(tmp_path):
    source, target = _prepare(tmp_path)

    def boom(ev, _state=[]):  # noqa: B006 - intentional stateful fixture stub
        raise RuntimeError("listener exploded")

    report = _runner(source, target, on_event=boom)
    assert report.failed == []
    assert report.processed == 2