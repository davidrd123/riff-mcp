import json
from pathlib import Path

import pytest
from PIL import Image

from gemini_video_prompts import image_comparison as comparison
from gemini_video_prompts import cli, openai_images


VARIANTS = [
    {'model': 'gpt-image-2.5-flare', 'quality': 'medium', 'size': '1024x1024'},
    {'model': 'gpt-image-2.5-sunburst', 'quality': 'high', 'size': '1024x1024'},
]


def test_preview_no_files_or_billing(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, 'generate_openai_image_job', lambda **kw: pytest.fail('paid call'))
    result = comparison.compare_images(prompt='Cup', variants=VARIANTS, out_root=str(tmp_path))
    assert result['output_count'] == 2
    assert result['billing_confirmation_required']
    assert not list(tmp_path.iterdir())
    with pytest.raises(RuntimeError, match='API_BILLING_CONFIRMATION_REQUIRED'):
        comparison.compare_images(prompt='Cup', variants=VARIANTS, dry_run=False, out_root=str(tmp_path))


def test_prevalidates_later_variant_and_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, 'generate_openai_image_job', lambda **kw: pytest.fail('paid call'))
    with pytest.raises(RuntimeError, match='quality'):
        comparison.compare_images(prompt='Cup', variants=[VARIANTS[0], dict(VARIANTS[1], quality='bad')],
                                  dry_run=False, allow_api_billing=True, out_root=str(tmp_path))
    with pytest.raises(RuntimeError, match='IMAGE_NOT_FOUND'):
        comparison.compare_images(prompt='Cup', variants=VARIANTS, image=str(tmp_path / 'missing.png'),
                                  dry_run=False, allow_api_billing=True, out_root=str(tmp_path))
    assert not list(tmp_path.iterdir())


def test_review_outputs_costs_escaping_and_unique_runs(tmp_path, monkeypatch):
    def generate(**kw):
        path = kw['run_day_dir'] / f"{kw['job']['source_index']}.png"
        Image.new('RGB', (4, 4)).save(path)
        return {'outputs': [{'path': str(path)}], 'elapsed_seconds': 2,
                'usage': {'input_tokens_details': {'text_tokens': 16, 'image_tokens': 0}, 'output_tokens': 196}}
    monkeypatch.setattr(comparison, 'generate_openai_image_job', generate)
    results = [comparison.compare_images(prompt='<script>bad</script>', variants=VARIANTS,
               dry_run=False, allow_api_billing=True, out_root=str(tmp_path)) for _ in range(2)]
    assert results[0]['comparison_dir'] != results[1]['comparison_dir']
    result = results[0]
    assert result['status'] == 'ok' and result['total_estimated_usd'] == 0.01192
    assert json.loads(Path(result['record_path']).read_text())['status'] == 'ok'
    page = Path(result['review_path']).read_text()
    assert '<script>bad</script>' not in page and '&lt;script&gt;' in page
    assert 'src="1.png"' in page and 'src="2.png"' in page


def test_failure_stops_batch(tmp_path, monkeypatch):
    calls = []
    def fail(**kw):
        calls.append(kw)
        raise RuntimeError('timeout')
    monkeypatch.setattr(comparison, 'generate_openai_image_job', fail)
    result = comparison.compare_images(prompt='Cup', variants=VARIANTS, dry_run=False,
                                       allow_api_billing=True, out_root=str(tmp_path))
    assert len(calls) == 1 and result['status'] == 'failed'
    assert result['unsubmitted_count'] == 1 and result['total_estimated_usd'] is None
    assert Path(result['review_path']).is_file()


def test_unknown_usage_is_unknown():
    assert comparison.estimate_cost(None)['usd'] is None
    assert comparison.estimate_cost({'total_tokens': 42})['usd'] is None


@pytest.mark.parametrize('problem', ['dimensions', 'alpha', 'opaque', 'missing'])
def test_mask_validation(tmp_path, problem):
    source = tmp_path / 'source.png'
    mask = tmp_path / 'mask.png'
    Image.new('RGB', (16, 16)).save(source)
    if problem != 'missing':
        mode = 'RGB' if problem == 'alpha' else 'RGBA'
        size = (8, 8) if problem == 'dimensions' else (16, 16)
        color = (0, 0, 0) if mode == 'RGB' else (0, 0, 0, 255 if problem == 'opaque' else 0)
        Image.new(mode, size, color).save(mask)
    job = cli.build_resolved_image_job(prompt='Edit', provider='openai', image='source.png', mask='mask.png')
    with pytest.raises(RuntimeError, match='INVALID_INPUT|MASK_NOT_FOUND'):
        openai_images.validate_assets(job, tmp_path / 'batch.yaml')


def test_mask_provider_and_hash_compatibility(tmp_path):
    with pytest.raises(RuntimeError, match='provider=openai'):
        cli.build_resolved_image_job(prompt='Edit', mask='mask.png')
    with pytest.raises(RuntimeError, match='reference image'):
        cli.build_resolved_image_job(prompt='Edit', provider='openai', mask='mask.png')
    a = cli.build_resolved_image_job(prompt='Edit', provider='openai', image='source.png')
    b = dict(a, mask='mask.png')
    assert cli.build_job_hash(a) != cli.build_job_hash(b)
    assert cli.build_job_hash(a) == cli.build_job_hash({k: v for k, v in a.items() if k != 'mask'})
