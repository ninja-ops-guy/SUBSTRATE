import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from failure_injection import FailureInjector, FailureType, InjectionResult, Severity  # noqa: E402


def test_destructive_campaign_fails_closed_without_opt_in(tmp_path):
    injector = FailureInjector(str(tmp_path), allow_destructive=False)
    report = injector.run_campaign(['stall'])
    assert report['complete'] is False
    assert report['total_tests'] == 0
    assert len(report['execution_errors']) == 1
    assert 'destructive' in report['execution_errors'][0]['error'].lower()


def test_unknown_test_makes_campaign_incomplete(tmp_path):
    injector = FailureInjector(str(tmp_path), allow_destructive=True)
    report = injector.run_campaign(['does-not-exist'])
    assert report['complete'] is False
    assert report['execution_errors'][0]['error'] == 'unknown test'


def test_checkpoint_lane_requires_explicit_path(tmp_path):
    injector = FailureInjector(str(tmp_path), allow_destructive=True)
    report = injector.run_campaign(['corrupted_checkpoint'])
    assert report['complete'] is False
    assert 'checkpoint path is required' in report['execution_errors'][0]['error']


def test_checkpoint_lane_does_not_modify_source(tmp_path):
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.write_text('keep-me')
    injector = FailureInjector(str(tmp_path / 'results'), allow_destructive=True)
    result = injector.inject_corrupted_checkpoint(str(checkpoint))
    assert result.recovery_successful is False
    assert result.details['source_modified'] is False
    assert checkpoint.read_text() == 'keep-me'


def test_cgroupfs_guard_rejects_arbitrary_paths(tmp_path):
    with pytest.raises(ValueError):
        FailureInjector._require_cgroupfs_path(str(tmp_path / 'not-cgroup'))
