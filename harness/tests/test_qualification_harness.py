import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from qualification_harness import (  # noqa: E402
    HardwareProfile,
    PSICapability,
    QualificationHarness,
    QualificationTest,
)


def hw() -> HardwareProfile:
    return HardwareProfile(
        model='R720', cpu_model='test', num_sockets=2, cores_per_socket=4,
        threads_per_core=2, total_memory_gb=64.0, memory_type='DDR3',
        memory_speed_mhz=1600, numa_nodes=2,
        numa_topology=[{'node_id': 0, 'memory_kb': 1, 'cpus': [0]},
                       {'node_id': 1, 'memory_kb': 1, 'cpus': [1]}],
        storage_devices=[], network_interfaces=[]
    )


def test_reported_skip_is_not_promoted_to_pass():
    suite = QualificationTest(hw())
    result = suite.run('x', 'X', lambda: {'status': 'SKIP', 'message': 'not installed'})
    assert result.status == 'SKIP'


def test_category_filter_runs_only_selected_suite(tmp_path, monkeypatch):
    harness = QualificationHarness(str(tmp_path))
    harness.probe.probe = hw
    monkeypatch.setattr(
        PSICapability,
        'test_psi_available',
        lambda self: {
            'memory': {'available': True},
            'cpu': {'available': True},
            'io': {'available': True},
        },
    )

    report = harness.run_full_qualification(category='PSI')
    assert report['summary']['total_tests'] == 1
    assert report['summary']['passed'] == 1
    assert report['summary']['qualified'] is True
    assert report['results'][0]['category'] == 'PSI'


def test_skip_blocks_qualified_summary(tmp_path):
    harness = QualificationHarness(str(tmp_path))
    harness.hw_profile = hw()
    suite = QualificationTest(harness.hw_profile)
    harness.all_results = [suite.run('x', 'X', lambda: {'status': 'SKIP'})]
    report = harness._generate_report()
    assert report['summary']['qualified'] is False
    assert report['summary']['skipped'] == 1


def test_unknown_category_is_rejected(tmp_path):
    harness = QualificationHarness(str(tmp_path))
    harness.probe.probe = hw
    try:
        harness.run_full_qualification(category='bogus')
    except ValueError as exc:
        assert 'Unknown qualification category' in str(exc)
    else:
        raise AssertionError('unknown category should fail')
