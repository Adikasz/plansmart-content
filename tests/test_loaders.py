"""Zero-network tests for src/core/config/loaders.py.

Uses the checked-in config/*.yml and prompts/*.yml as read-only fixtures.
No network, no external services.
"""
from __future__ import annotations

from src.core.config.loaders import (
    PROJECT_ROOT,
    load_accounts,
    load_config,
    load_content_strategy,
    load_prompt_list,
    load_scoring,
    load_sources,
    load_yaml,
)


def test_load_yaml_returns_non_empty_dict():
    """load_yaml on a real config returns a non-empty dict."""
    data = load_yaml("config/scoring.yml")
    assert isinstance(data, dict)
    assert len(data) > 0


def test_load_yaml_relative_path_resolves_against_repo_root():
    """A relative path is resolved against PROJECT_ROOT, matching an absolute read."""
    rel = load_yaml("config/scoring.yml")
    abs_path = PROJECT_ROOT / "config" / "scoring.yml"
    assert load_yaml(abs_path) == rel
    # sanity: the resolved file actually lives under the repo root
    assert (PROJECT_ROOT / "config" / "scoring.yml").is_file()


def test_load_yaml_is_lru_cached_same_object():
    """Calling load_yaml with the same path returns the identical cached object."""
    a = load_yaml("config/scoring.yml")
    b = load_yaml("config/scoring.yml")
    assert a is b


def test_load_config_equals_load_scoring():
    """load_config('scoring.yml') returns the same cached object as load_scoring()."""
    # Both funnel through load_config(CONFIG_DIR / name) -> same lru_cache key/object.
    assert load_config("scoring.yml") is load_scoring()
    assert load_config("scoring.yml") == load_scoring()


def test_load_content_strategy_returns_dict():
    data = load_content_strategy()
    assert isinstance(data, dict)
    assert len(data) > 0


def test_load_accounts_returns_accounts_subsection():
    """load_accounts returns the 'accounts' subsection dict (not the whole file)."""
    accounts = load_accounts()
    assert isinstance(accounts, dict)
    # It is the inner 'accounts' mapping, so it must NOT still contain the top-level key.
    raw = load_config("accounts.yml")
    assert accounts == raw.get("accounts", {})
    # config/accounts.yml defines at least the david account.
    assert "david" in accounts


def test_load_sources_returns_dict():
    data = load_sources()
    assert isinstance(data, dict)
    assert len(data) > 0


def test_load_prompt_list_returns_list_for_valid_key():
    """load_prompt_list returns the list stored under the given key."""
    topics = load_prompt_list("educational_topics.yml", "educational_topics")
    assert isinstance(topics, list)
    assert len(topics) > 0
    # entries are the mapping dicts from the yaml
    assert all(isinstance(item, dict) for item in topics)


def test_load_prompt_list_missing_key_returns_empty_list():
    """An absent key yields [] (not KeyError, not None)."""
    result = load_prompt_list("educational_topics.yml", "no_such_key")
    assert result == []


def test_load_prompt_list_resolves_under_prompts_dir():
    """The filename is resolved under PROMPTS_DIR (prompts/), confirming path handling."""
    assert (PROJECT_ROOT / "prompts" / "educational_topics.yml").is_file()
    via_helper = load_prompt_list("educational_topics.yml", "educational_topics")
    direct = load_yaml(PROJECT_ROOT / "prompts" / "educational_topics.yml")["educational_topics"]
    assert via_helper == direct
