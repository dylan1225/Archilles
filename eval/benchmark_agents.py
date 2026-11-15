"""Paired uncached benchmark. Makes real billable calls; never claims quality automatically.

venv/bin/python eval/benchmark_agents.py static/examples/1.jpg --runs 3 --context '...'
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnose
import redesign_agent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('photo', type=Path)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--context', default='')
    parser.add_argument('--output', type=Path, default=Path('cache/agent-benchmark.json'))
    args = parser.parse_args()
    if not 1 <= args.runs <= 10:
        parser.error('--runs must be 1 to 10')
    if not redesign_agent.status()['available']:
        parser.error(redesign_agent.status()['message'])
    photo = args.photo.read_bytes()
    rows = []
    for iteration in range(args.runs):
        for pipeline in (['gemini', 'agents'] if iteration % 2 == 0 else ['agents', 'gemini']):
            started = time.perf_counter()
            row = {'iteration': iteration + 1, 'pipeline': pipeline, 'cached': False}
            try:
                if pipeline == 'gemini':
                    result, meta = diagnose.diagnose_with_meta(photo, args.context)
                    row['diagnosis'] = result.model_dump(mode='json')
                else:
                    result = asyncio.run(redesign_agent.workflow(image=photo, context=args.context))
                    row.update(result)
                row['success'] = True
            except Exception as exc:
                row.update(success=False, error_type=type(exc).__name__)
            row['wall_s'] = round(time.perf_counter() - started, 2)
            rows.append(row)
            print(pipeline, row['success'], row['wall_s'], 'seconds')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({'runs': rows, 'quality_review': 'Pending manual review'}, indent=2))
    summary = {}
    for pipeline in ['gemini', 'agents']:
        times = [r['wall_s'] for r in rows if r['pipeline'] == pipeline and r['success']]
        summary[pipeline] = {'successful_runs': len(times), 'median_s': statistics.median(times) if times else None}
    all_succeeded = all(r['success'] for r in rows)
    summary['speedup'] = (summary['gemini']['median_s'] / summary['agents']['median_s']) if all_succeeded else None
    args.output.write_text(json.dumps({'summary': summary, 'runs': rows,
        'quality_review': 'Manual review required: evidence accuracy, constraint handling, tool usage, useful fixes. Timing alone does not establish equivalent quality.'}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
